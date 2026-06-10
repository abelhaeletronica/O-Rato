#!/usr/bin/env python3
"""
Roer — pipeline de fichamento para Obsidian
===========================================
Uso:
    python rato/scripts/roer.py --modo indexar --pasta . --saida fichas
    python rato/scripts/roer.py --modo completo --pasta . --saida fichas --modelo qwen2.5:7b

Dependências:
    pip install requests pyyaml tqdm

O Ollama precisa estar rodando localmente (ollama serve).
Os chunks originais são indexados em SQLite FTS; embeddings ficam restritos às leituras brutas.
"""

from __future__ import annotations
import argparse
import json
import re
import time
import math
import hashlib
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import calendar

import requests
import yaml
from tqdm import tqdm


# ─────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────

OLLAMA_URL       = "http://localhost:11434/api/generate"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
OLLAMA_EMBED_LEGACY_URL = "http://localhost:11434/api/embeddings"
CHUNK_MAX_WORDS  = 1200          # chunks menores preservam movimentos argumentativos
CHUNK_OVERLAP    = 220           # mais retomada para textos filosóficos e ensaísticos
TIMEOUT_SEGUNDOS = 600           # timeout por chamada ao Ollama
CACHE_DIR        = Path(".cache_indexador")  # evita re-processar chunks já feitos
EMBEDDINGS_DIR_PADRAO = Path(".embeddings")
SQLITE_BUSCA_PADRAO = Path("rato/biblioteca_referencias.sqlite")
MODELO_EMBEDDING_PADRAO = "bge-m3"
EMBEDDING_BATCH_SIZE = 6
EMBEDDING_MAX_CHARS = 5000


# ─────────────────────────────────────────────
# 1. LEITURA E METADADOS
# ─────────────────────────────────────────────

def extrair_frontmatter(texto: str) -> tuple[dict, str]:
    """Extrai frontmatter YAML se existir; retorna (metadados, corpo)."""
    if texto.startswith("---"):
        partes = texto.split("---", 2)
        if len(partes) >= 3:
            try:
                meta = yaml.safe_load(partes[1]) or {}
                return meta, partes[2].strip()
            except yaml.YAMLError:
                pass
    return {}, texto.strip()


def extrair_titulo(texto: str, caminho: Path) -> str:
    """Tenta encontrar o primeiro H1; fallback para o nome do arquivo."""
    for linha in texto.splitlines():
        linha = linha.strip()
        if linha.startswith("# "):
            return linha[2:].strip()
    return caminho.stem.replace("-", " ").replace("_", " ").title()


def ler_arquivo(caminho: Path) -> dict:
    """Lê um .md e retorna um dicionário com tudo que precisamos."""
    texto_raw = caminho.read_text(encoding="utf-8", errors="replace")
    meta, corpo = extrair_frontmatter(texto_raw)
    titulo = meta.get("title") or extrair_titulo(corpo, caminho)
    tags   = meta.get("tags") or meta.get("keywords") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    return {
        "caminho":   caminho,
        "titulo":    titulo,
        "tags":      tags,
        "meta":      meta,
        "corpo":     corpo,
        "tamanho":   len(corpo.split()),
    }


def identificar_documento_para_prompt(doc: dict) -> str:
    """Monta uma identificação curta para orientar o modelo sem poluir a ficha."""
    meta = doc.get("meta", {})
    titulo = doc.get("titulo") or doc["caminho"].stem
    autor = meta.get("author") or meta.get("authors")
    ano = meta.get("year") or meta.get("ano")
    lingua = meta.get("lingua") or meta.get("língua") or meta.get("language") or meta.get("lang") or meta.get("idioma")

    partes = []
    if autor:
        if isinstance(autor, list):
            autor = "; ".join(str(a) for a in autor)
        partes.append(str(autor))
    if ano:
        partes.append(str(ano))
    if lingua:
        partes.append(f"língua: {lingua}")

    return f"{titulo} — {', '.join(partes)}" if partes else titulo


# ─────────────────────────────────────────────
# 2. CHUNKING
# ─────────────────────────────────────────────

def quebrar_em_chunks(texto: str, max_palavras: int = CHUNK_MAX_WORDS,
                      sobreposicao: int = CHUNK_OVERLAP) -> list[str]:
    """
    Divide o texto em chunks por parágrafo, respeitando o limite de palavras.
    Adiciona sobreposição para manter contexto entre chunks.
    """
    paragrafos = [p.strip() for p in re.split(r"\n{2,}", texto) if p.strip()]
    chunks, atual, palavras_atual = [], [], 0

    for par in paragrafos:
        palavras_par = len(par.split())

        # Parágrafo sozinho já excede o limite — divide por frases
        if palavras_par > max_palavras:
            if atual:
                chunks.append("\n\n".join(atual))
                atual, palavras_atual = [], 0
            frases = re.split(r"(?<=[.!?])\s+", par)
            sub, sub_w = [], 0
            for frase in frases:
                fw = len(frase.split())
                if sub_w + fw > max_palavras and sub:
                    chunks.append(" ".join(sub))
                    sub, sub_w = [], 0
                sub.append(frase)
                sub_w += fw
            if sub:
                chunks.append(" ".join(sub))
            continue

        if palavras_atual + palavras_par > max_palavras and atual:
            chunks.append("\n\n".join(atual))
            # sobreposição: mantém os últimos N palavras no próximo chunk
            overlap_texto = " ".join(" ".join(atual).split()[-sobreposicao:])
            atual = [overlap_texto] if overlap_texto else []
            palavras_atual = len(overlap_texto.split())

        atual.append(par)
        palavras_atual += palavras_par

    if atual:
        chunks.append("\n\n".join(atual))

    return chunks or [texto]  # fallback: documento inteiro


# ─────────────────────────────────────────────
# 3. CACHE (evita re-processar)
# ─────────────────────────────────────────────

def hash_chunk(texto: str, modelo: str, prompt_template: str) -> str:
    """Gera hash incluindo modelo, prompt e texto para evitar cache obsoleto."""
    conteudo = f"{modelo}::{prompt_template}::{texto}"
    return hashlib.md5(conteudo.encode()).hexdigest()


def cache_ler(chave: str, nome_arquivo: str = "") -> str | None:
    subpasta = CACHE_DIR / nome_arquivo if nome_arquivo else CACHE_DIR
    f = subpasta / f"{chave}.txt"
    return f.read_text(encoding="utf-8") if f.exists() else None


def cache_salvar(chave: str, valor: str, nome_arquivo: str = ""):
    subpasta = CACHE_DIR / nome_arquivo if nome_arquivo else CACHE_DIR
    subpasta.mkdir(parents=True, exist_ok=True)
    (subpasta / f"{chave}.txt").write_text(valor, encoding="utf-8")




# ─────────────────────────────────────────────
# 5. AGENTE OLLAMA
# ─────────────────────────────────────────────

PROMPT_LEITURA_BRUTA = """\
Leia o trecho abaixo com atenção.

Não faça um resumo escolar.
Não tente explicar tudo.
Use apenas o que aparece no trecho.

A tarefa desta etapa é observar o trecho, não fechar a interpretação do texto inteiro.
Procure produzir matéria-prima útil para a consolidação posterior.

Perguntas de orientação:
- O que parece acontecer neste trecho?
- O que parece organizar o argumento local?
- Que ideia, relação ou passagem precisaria permanecer se 90% do trecho fosse apagado?
- O que é difícil, estranho, instável ou potencialmente central?
- Que autores, obras, exemplos ou casos são mobilizados?

Não confunda frequência com centralidade.
Um termo pode aparecer muitas vezes e ainda assim ser secundário.
Um conceito pode aparecer pouco e ainda assim organizar o trecho.

Prefira poucos elementos bem observados a listas longas.
Quando houver dúvida, marque como hipótese ou centralidade instável.
Não copie os enunciados do template na resposta.

Preserve termos técnicos importantes no idioma original quando necessário.
Se o trecho for bibliografia, agradecimentos, ficha catalográfica, lista de links ou notas editoriais, classifique como MATERIAL PARATEXTUAL.

Identificação do texto: "{titulo}".
Este é o trecho {parte_atual} de {total_partes}.

Formato obrigatório:

LEITURA-BRUTA:

MOVIMENTO:
o que parece acontecer ou se reorganizar neste trecho, com [Parte {parte_atual}]

CONCEITOS-ESTRUTURANTES:
poucos conceitos, relações ou problemas sem os quais o argumento local perderia sua forma, com [Parte {parte_atual}]

ZONAS-DENSAS:
passagens, ideias, tensões ou termos difíceis, estranhos, instáveis ou potencialmente centrais, com [Parte {parte_atual}]

REFERÊNCIAS:
autores, obras, exemplos, casos, imagens ou situações mobilizadas no trecho, se houver, com [Parte {parte_atual}]

PALAVRAS-CHAVE:
até 8 termos relevantes separados por vírgula

---
TRECHO:
{chunk}
"""

PROMPT_INTERMEDIARIO = """\
Você está aproximando leituras locais de um mesmo texto chamado "{titulo}".

As unidades abaixo cobrem: {escopo}.

Não tente fechar o sentido do texto inteiro.
Procure apenas o que retorna, o que se conecta e o que parece ganhar peso entre essas partes.

Formato obrigatório:

PARTES-COBERTAS:
liste as partes cobertas, por exemplo: [Parte 1], [Parte 2]

RECORRÊNCIAS:
conceitos, termos ou problemas que retornam entre as partes

RELAÇÕES:
relações conceituais que parecem ligar as partes

PASSAGENS-DENSAS:
frases ou ideias difíceis que parecem importantes

ZONAS-DE-BAIXA-CONFIANÇA:
conceitos ou relações ainda instáveis, mas possivelmente centrais

AUTORES-OBRAS:
preserve todos os autores e obras citados nas leituras anteriores, sem omitir nenhum

PALAVRAS-CHAVE-INTERMEDIARIAS:
termos relevantes separados por vírgula. Exemplo: environment, Umwelt, feedback

---
LEITURAS:
{leituras}
"""

# ─────────────────────────────────────────────
# CAMADA 2 — ESTRUTURA DO TEXTO
# ─────────────────────────────────────────────

PROMPT_ESTRUTURA_TEXTO = """\
Você está observando a estrutura interna de um texto chamado "{titulo}".

Use somente as notas abaixo.
Não use conhecimento externo.
Não tente tornar o texto mais claro, linear ou coerente do que ele é.

A tarefa não é preencher um formulário.
A tarefa é perceber que tipo de organização o próprio texto parece pedir.

Procure:
- o problema que parece mover o texto;
- conceitos que fazem outros conceitos se reorganizarem;
- tensões que sustentam o argumento;
- passagens que mudam o peso da leitura;
- zonas em que a centralidade ainda é incerta.

Não confunda recorrência com centralidade.
Um termo pode aparecer muitas vezes e ainda assim ser secundário.
Um conceito pode aparecer pouco e ainda assim organizar o argumento.

Baixa confiança não é erro quando aparece junto com recorrência, tensão ou conectividade.
Quando a estrutura estiver incerta, diga onde ela está incerta.

Use os títulos abaixo, mas não dê o mesmo peso a todos.
Algumas seções podem ser breves.
Outras podem ser mais densas, se o texto exigir.

ESTRUTURA-DO-TEXTO:

EIXO-EMERGENTE:
qual problema, movimento ou pergunta parece organizar o texto inteiro, com [Parte N]

CONCEITOS-QUE-GANHAM-PESO:
conceitos que parecem ganhar importância ao longo das partes, distinguindo recorrência de centralidade, com [Parte N]

OPERADORES-POSSÍVEIS:
conceitos que parecem organizar relações entre outros conceitos, mesmo que ainda de forma instável, com [Parte N]

TENSÕES-QUE-SUSTENTAM-O-TEXTO:
polaridades, conflitos ou diferenças que fazem o argumento se mover, com [Parte N]

PASSAGENS-QUE-MUDAM-A-LEITURA:
frases, ideias ou deslocamentos que reorganizam a compreensão do texto, com [Parte N]

ZONAS-DE-INCERTEZA:
o que ainda parece difícil de decidir, mas pode ser importante, com [Parte N]

RISCO-DE-ACHATAMENTO:
qual seria a simplificação mais provável deste texto, com [Parte N]

---
NOTAS:
{leitura_bruta}
"""

# ─────────────────────────────────────────────
# CAMADA 3 — RESSONÂNCIAS CONTROLADAS
# ─────────────────────────────────────────────

PROMPT_RESSONANCIAS = """\
Você está observando possíveis ressonâncias internas de um texto chamado "{titulo}".

Use apenas as notas do texto.
Separe rigorosamente:
- o que vem do texto;
- o que é inferência do leitor.

Prefira aproximações parciais a equivalências fortes.

Formato obrigatório:

RESSONANCIAS-INTERNAS:

CONCEITOS-DO-TEXTO:
conceitos do próprio texto, com base [Parte N]

RESSONÂNCIAS-POSSÍVEIS:
aproximações internas entre conceitos, exemplos ou problemas do próprio texto, marcadas como hipótese e com GRAU-DE-CONFIANÇA

LIMITES:
onde a aproximação pode forçar ou distorcer o texto

PERGUNTAS-GERADAS:
perguntas que emergem do próprio texto

---
LEITURAS-BRUTAS:
{leitura_bruta}
"""

PROMPT_CONSOLIDAR = """\
Você está consolidando rastros de leitura de um texto chamado "{titulo}".

Não use conhecimento externo.
Não transforme hipótese em certeza.
Não tente produzir uma interpretação definitiva.
Não tente tornar o texto mais coerente, sistemático ou conclusivo do que ele parece ser.

A tarefa desta etapa não é decidir o que é mais importante no texto.
A tarefa é reorganizar em prosa os rastros encontrados nas leituras-brutas para que possam ser reencontrados mais tarde.

Imagine alguém retornando a esta ficha daqui a dois anos.
O objetivo não é substituir o texto original.
O objetivo é reavivar algo que possa ter sido esquecido.

Não confunda recorrência com centralidade.
Um termo pode aparecer muitas vezes e ainda assim ser secundário.
Um conceito pode aparecer pouco e ainda assim merecer preservação.

Não ranqueie conceitos.
Não escolha uma tese central.
Não transforme a ficha em resumo escolar.
Não apresente conclusões fechadas.

Quando houver dúvida:
- preserve a dúvida;
- indique a incerteza;
- não feche a interpretação.

Use exatamente os títulos abaixo.
Toda afirmação substantiva deve manter referência [Parte N] quando a parte puder ser identificada.

Formato obrigatório:

**Movimento geral**
Descreva em prosa o percurso do texto.

Acompanhe o movimento das partes sem reduzir o texto a uma tese única.
Procure mostrar o que parece acontecer, que problemas surgem, que deslocamentos ocorrem e que questões ganham ou perdem presença ao longo da leitura.

**Rastros preservados**
Percorra principalmente o INVENTÁRIO DE COBERTURA, com atenção especial às ZONAS-DENSAS e PALAVRAS-CHAVE.

Não decida quais são os conceitos mais importantes.
Não descarte elementos apenas porque aparecem pouco.
Não transforme as zonas densas em explicações definitivas.

Procure preservar:
- conceitos, relações ou problemas que aparecem nas leituras-brutas;
- passagens difíceis, estranhas ou instáveis;
- autores, obras, exemplos, casos, imagens ou situações listadas em METADADOS PRESERVADOS;
- termos técnicos no idioma original quando forem relevantes;
- tensões que ainda não precisam ser resolvidas;
- elementos que parecem merecer retorno futuro.

Escreva em parágrafos curtos.
Apresente os rastros preservados em blocos de prosa fragmentária, sem usar conectivos de conclusão ou pacificação como "portanto", "dessa forma" ou "com isso".
Cada parágrafo pode reunir rastros próximos, mas sem forçar uma síntese única.
Use referências [Parte N].

**Referências mobilizadas**
Reorganize em prosa os autores, obras, exemplos, casos, imagens ou situações citadas nas leituras-brutas.
Use METADADOS PRESERVADOS como rede de segurança: não omita uma referência ali registrada apenas porque ela parece lateral.

Não agrupe tudo numa lista genérica.
Quando a função da referência estiver clara, indique como ela participa do trecho.
Quando não estiver clara, preserve apenas o rastro e indique a incerteza.

Use referências [Parte N].

**Perguntas abertas**
Registre perguntas que permanecem após a leitura.

Prefira perguntas geradas pelo próprio texto ou pelas zonas densas.
Não responda às perguntas.

**Riscos de redução**
Indique como o texto poderia ser achatado por uma leitura rápida.

O que provavelmente se perderia nessa redução?
Use referências [Parte N] quando possível.

**Rastros para retomar mais tarde**
Liste poucos elementos que podem merecer retorno futuro.

Podem ser conceitos, autores, passagens, tensões, perguntas ou relações ainda pouco claras.
Não explique excessivamente.
Preserve-os como rastros de investigação.

PALAVRAS-CHAVE-FINAIS:
termos relevantes separados por vírgula. Exemplo: ensaio, reificação, anacronismo, crítica imanente

---
INVENTÁRIO DE COBERTURA:
{inventario_cobertura}

---
METADADOS PRESERVADOS:
{metadados_preservados}

---
LEITURAS-BRUTAS:
{leitura_bruta}
"""


SECOES_LEITURA_BRUTA = ["MOVIMENTO", "CONCEITOS-ESTRUTURANTES", "ZONAS-DENSAS", "REFERÊNCIAS", "PALAVRAS-CHAVE"]


def separar_partes_leitura(texto: str) -> list[tuple[str, str]]:
    texto = texto.strip()
    if not texto:
        return []

    parte_regex = re.compile(r"(?m)^\[Parte\s+(\d+)\]\s*$")
    partes = []
    matches = list(parte_regex.finditer(texto))
    if matches:
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(texto)
            body = re.sub(r"\n\s*---\s*$", "", texto[start:end].strip()).strip()
            partes.append((match.group(1), body))
    else:
        partes.append(("1", texto))
    return partes


def extrair_secao_leitura(body: str, secao: str) -> str:
    later = [m for m in SECOES_LEITURA_BRUTA if m != secao]
    secao_pattern = rf"{re.escape(secao)}S?" if secao == "MOVIMENTO" else re.escape(secao)
    padrao = re.compile(
        rf"(?ms)^{secao_pattern}:\s*(.*?)(?=^(?:{'|'.join(re.escape(m) for m in later)}):|\Z)",
        re.MULTILINE,
    )
    match = padrao.search(body)
    return match.group(1).strip() if match else ""


def montar_inventario_cobertura(texto_consolidado: str) -> str:
    """Extrai o inventário de cobertura das leituras-brutas consolidadas.

    Reúne apenas MOVIMENTO, ZONAS-DENSAS e PALAVRAS-CHAVE de cada parte.
    """
    texto = texto_consolidado.strip()
    if not texto:
        return "INVENTÁRIO DE COBERTURA\n\nMOVIMENTOS:\n\nZONAS-DENSAS:\n\nPALAVRAS-CHAVE:\n"

    movimentos = []
    zonas = []
    palavras = []
    for numero, body in separar_partes_leitura(texto):
        movimento = extrair_secao_leitura(body, "MOVIMENTO")
        zonas_densas = extrair_secao_leitura(body, "ZONAS-DENSAS")
        palavras_chave = extrair_secao_leitura(body, "PALAVRAS-CHAVE")
        if movimento:
            movimentos.append(f"[Parte {numero}]\n{movimento}")
        if zonas_densas:
            zonas.append(f"[Parte {numero}]\n{zonas_densas}")
        if palavras_chave:
            palavras.append(f"[Parte {numero}]\n{palavras_chave}")

    resultado = ["INVENTÁRIO DE COBERTURA", "", "MOVIMENTOS:"]
    if movimentos:
        resultado.extend(movimentos)
    resultado.extend(["", "ZONAS-DENSAS:"])
    if zonas:
        resultado.extend(zonas)
    resultado.extend(["", "PALAVRAS-CHAVE:"])
    if palavras:
        resultado.extend(palavras)

    return "\n".join(resultado).strip() + "\n"


def extrair_metadados_leituras(texto_consolidado: str) -> str:
    """Preserva rastros que não devem depender da compressão interpretativa."""
    secoes = {
        "CONCEITOS-ESTRUTURANTES": [],
        "ZONAS-DENSAS": [],
        "REFERÊNCIAS": [],
        "PALAVRAS-CHAVE": [],
    }
    for numero, body in separar_partes_leitura(texto_consolidado):
        for secao in secoes:
            conteudo = extrair_secao_leitura(body, secao)
            if conteudo:
                secoes[secao].append(f"[Parte {numero}]\n{conteudo}")

    linhas = ["METADADOS PRESERVADOS DAS LEITURAS-BRUTAS"]
    for secao, itens in secoes.items():
        linhas.extend(["", f"{secao}:"])
        if itens:
            linhas.extend(itens)
        else:
            linhas.append("não identificado")

    return "\n".join(linhas).strip() + "\n"


GRUPO_MAX_PARTES = 3
MAX_UNIDADES_FINAIS = 3
MAX_RODADAS_SINTESE = 3
FICHA_TITULOS_OBRIGATORIOS = [
    "**Movimento geral**",
    "**Rastros preservados**",
    "**Referências mobilizadas**",
    "**Perguntas abertas**",
    "**Riscos de redução**",
    "**Rastros para retomar mais tarde**",
]


def chamar_ollama(
    prompt: str,
    modelo: str,
    tentativas: int = 3,
    num_predict: int = 1800,
    num_ctx: int = 8192,
    temperature: float = 0.2,
) -> str:
    """Chama o Ollama com retry automático."""
    payload = {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
    }
    ultimo_erro: Exception | None = None
    for tentativa in range(tentativas):
        try:
            r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_SEGUNDOS)
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except requests.RequestException as e:
            ultimo_erro = e
            if tentativa < tentativas - 1:
                time.sleep(3 * (tentativa + 1))
    raise RuntimeError(f"Ollama falhou após {tentativas} tentativas: {ultimo_erro}")


def chamar_ollama_embeddings(textos: list[str], modelo: str) -> list[list[float]]:
    """Gera embeddings em sublotes para evitar estouro no Ollama."""
    embeddings = []
    for i in range(0, len(textos), EMBEDDING_BATCH_SIZE):
        lote = textos[i:i + EMBEDDING_BATCH_SIZE]
        embeddings.extend(chamar_ollama_embeddings_lote(lote, modelo))
    return embeddings


def chamar_ollama_embeddings_lote(textos: list[str], modelo: str, tentativas: int = 3) -> list[list[float]]:
    """Gera embeddings para um lote; divide o lote se o servidor recusar."""
    payload = {"model": modelo, "input": textos}
    ultimo_erro: Exception | None = None
    for tentativa in range(tentativas):
        try:
            r = requests.post(OLLAMA_EMBED_URL, json=payload, timeout=TIMEOUT_SEGUNDOS)
            r.raise_for_status()
            dados = r.json()
            embeddings = dados.get("embeddings")
            if embeddings is None and "embedding" in dados:
                embeddings = [dados["embedding"]]
            if not isinstance(embeddings, list) or len(embeddings) != len(textos):
                raise RuntimeError("Resposta de embeddings em formato inesperado")
            return embeddings
        except (requests.RequestException, RuntimeError) as e:
            ultimo_erro = e
            if tentativa < tentativas - 1:
                time.sleep(3 * (tentativa + 1))

    if len(textos) > 1:
        meio = len(textos) // 2
        return (
            chamar_ollama_embeddings_lote(textos[:meio], modelo)
            + chamar_ollama_embeddings_lote(textos[meio:], modelo)
        )

    try:
        return chamar_ollama_embeddings_legado(textos, modelo)
    except requests.RequestException as e:
        raise RuntimeError(f"Ollama falhou ao gerar embedding: {ultimo_erro or e}") from e


def chamar_ollama_embeddings_legado(textos: list[str], modelo: str) -> list[list[float]]:
    """Fallback para /api/embeddings, que gera um embedding por chamada."""
    embeddings = []
    for texto in textos:
        r = requests.post(
            OLLAMA_EMBED_LEGACY_URL,
            json={"model": modelo, "prompt": texto},
            timeout=TIMEOUT_SEGUNDOS,
        )
        r.raise_for_status()
        embedding = r.json().get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError("Resposta de embedding legado em formato inesperado")
        embeddings.append(embedding)
    return embeddings


def preparar_texto_embedding(texto: str, limite: int = EMBEDDING_MAX_CHARS) -> str:
    """Limita textos longos para caber no contexto do modelo de embedding."""
    texto = texto.strip()
    if len(texto) <= limite:
        return texto
    inicio = int(limite * 0.7)
    fim = limite - inicio
    return texto[:inicio].rstrip() + "\n[...]\n" + texto[-fim:].lstrip()


def conectar_sqlite_busca(db_path: Path) -> sqlite3.Connection:
    """Abre o banco de busca textual e garante o schema FTS."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    criar_schema_sqlite_busca(conn)
    return conn


def criar_schema_sqlite_busca(conn: sqlite3.Connection) -> None:
    """Schema compatível com o farejar.py para busca textual em chunks."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY,
            caminho TEXT NOT NULL UNIQUE,
            titulo TEXT NOT NULL,
            tipo TEXT NOT NULL,
            hash TEXT NOT NULL,
            palavras INTEGER NOT NULL,
            meta_json TEXT NOT NULL,
            atualizado_em TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            documento_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
            parte INTEGER NOT NULL,
            texto TEXT NOT NULL,
            hash TEXT NOT NULL,
            embedding_json TEXT,
            UNIQUE(documento_id, parte)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            titulo,
            caminho,
            tipo,
            texto
        );
        """
    )


def hash_texto_sqlite(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def indexar_chunks_sqlite(doc: dict, chunks: list[str], db_path: Path) -> Path:
    """Indexa os chunks originais em SQLite FTS, sem gerar embeddings."""
    conn = conectar_sqlite_busca(db_path)
    caminho = str(doc["caminho"])
    corpo = doc.get("corpo", "")
    doc_hash = hash_texto_sqlite(corpo)
    agora = datetime.now().isoformat(timespec="seconds")
    meta_json = json.dumps(doc.get("meta", {}), ensure_ascii=False)

    try:
        existente = conn.execute(
            "SELECT id, hash FROM documentos WHERE caminho = ?",
            (caminho,),
        ).fetchone()

        if existente:
            documento_id = existente["id"]
            conn.execute("DELETE FROM chunks WHERE documento_id = ?", (documento_id,))
            conn.execute("DELETE FROM chunks_fts WHERE caminho = ?", (caminho,))
            conn.execute(
                """
                UPDATE documentos
                   SET titulo = ?, tipo = ?, hash = ?, palavras = ?, meta_json = ?, atualizado_em = ?
                 WHERE id = ?
                """,
                (
                    doc["titulo"],
                    "referencia",
                    doc_hash,
                    len(corpo.split()),
                    meta_json,
                    agora,
                    documento_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO documentos (caminho, titulo, tipo, hash, palavras, meta_json, atualizado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    caminho,
                    doc["titulo"],
                    "referencia",
                    doc_hash,
                    len(corpo.split()),
                    meta_json,
                    agora,
                ),
            )
            documento_id = cur.lastrowid

        for parte, chunk in enumerate(chunks, start=1):
            cur = conn.execute(
                """
                INSERT INTO chunks (documento_id, parte, texto, hash, embedding_json)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (documento_id, parte, chunk, hash_texto_sqlite(chunk)),
            )
            conn.execute(
                "INSERT INTO chunks_fts(rowid, titulo, caminho, tipo, texto) VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, doc["titulo"], caminho, "referencia", chunk),
            )

        conn.commit()
    finally:
        conn.close()

    return db_path


def similaridade_cosseno(a: list[float], b: list[float]) -> float:
    """Calcula similaridade cosseno entre dois vetores."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return dot / (norma_a * norma_b)


def gerar_vizinhancas_semanticas(
    titulo: str,
    leituras: list[str],
    modelo_embedding: str,
    top_k: int = 2,
) -> str:
    """
    Usa embeddings para detectar proximidades semânticas internas entre partes.
    Esta camada não interpreta o texto; apenas mostra quais partes parecem conversar.
    """
    if not modelo_embedding or len(leituras) < 2:
        return "não gerado"

    textos_embedding = [preparar_texto_embedding(leitura) for leitura in leituras]
    embeddings = chamar_ollama_embeddings(textos_embedding, modelo_embedding)

    linhas = [
        "VIZINHANÇAS SEMÂNTICAS INTERNAS:",
        f"Texto: {titulo}",
        "As relações abaixo foram calculadas por similaridade cosseno entre embeddings das leituras brutas.",
        "Elas indicam proximidade semântica, não equivalência conceitual.",
        "",
    ]

    for i, emb_i in enumerate(embeddings):
        similares = []
        for j, emb_j in enumerate(embeddings):
            if i == j:
                continue
            sim = similaridade_cosseno(emb_i, emb_j)
            similares.append((sim, j + 1))
        similares.sort(reverse=True)
        vizinhos = similares[:top_k]
        partes = ", ".join(
            [f"[Parte {parte}] ({sim:.3f})" for sim, parte in vizinhos]
        )
        linhas.append(f"- [Parte {i + 1}] aproxima-se de: {partes}")

    linhas.append("")
    linhas.append(
        "Use estas vizinhanças como rastros de recorrência distribuída, não como prova automática de centralidade."
    )
    return "\n".join(linhas)


def gerar_embeddings_documento(
    doc: dict,
    leituras: list[str],
    pasta_embeddings: Path,
    modelo_embedding: str,
) -> Path:
    """Gera embeddings em lote apenas para leituras brutas."""
    pasta_embeddings.mkdir(parents=True, exist_ok=True)
    saida = pasta_embeddings / f"{doc['caminho'].stem}.jsonl"

    registros_sem_embedding = []
    textos = []
    data = datetime.now().strftime("%Y-%m-%d")
    for i, leitura in enumerate(leituras, start=1):
        texto_embedding = preparar_texto_embedding(leitura)
        registros_sem_embedding.append({
            "caminho": str(doc["caminho"]),
            "titulo": doc["titulo"],
            "parte": i,
            "tipo": "leitura-bruta",
            "modelo_embedding": modelo_embedding,
            "data_indexacao": data,
            "texto": texto_embedding,
            "texto_hash": hashlib.md5(leitura.encode()).hexdigest(),
            "texto_embedding_hash": hashlib.md5(texto_embedding.encode()).hexdigest(),
            "texto_embedding_chars": len(texto_embedding),
            "texto_truncado_para_embedding": len(texto_embedding) < len(leitura.strip()),
            "palavras": len(leitura.split()),
        })
        textos.append(texto_embedding)

    embeddings = chamar_ollama_embeddings(textos, modelo_embedding)
    with saida.open("w", encoding="utf-8") as f:
        for registro, embedding in zip(registros_sem_embedding, embeddings):
            registro["embedding"] = embedding
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")

    return saida


def resumir_chunk(
    chunk: str,
    modelo: str,
    titulo: str,
    parte_atual: int,
    total_partes: int,
    nome_arquivo: str = "",
) -> tuple[str, list[str]]:
    """Produz leitura bruta de um chunk e retorna (leitura, palavras_chave)."""
    prompt = PROMPT_LEITURA_BRUTA.format(
        titulo=titulo,
        parte_atual=parte_atual,
        total_partes=total_partes,
        chunk=chunk,
    )
    chave = hash_chunk(chunk, modelo, prompt)
    cached = cache_ler(chave, nome_arquivo)
    if cached:
        dados = json.loads(cached)
        return dados["resumo"], dados["palavras_chave"]

    resposta = chamar_ollama(prompt, modelo)

    # Mantemos a resposta estruturada inteira como leitura bruta parcial.
    # Isso preserva conceitos, exemplos e autores para a consolidação final.
    leitura = resposta.strip()
    palavras_chave = []

    if "PALAVRAS-CHAVE:" in resposta:
        trecho_kw = resposta.split("PALAVRAS-CHAVE:", 1)[1]
        trecho_kw = trecho_kw.split("---", 1)[0]
        palavras_chave = [p.strip() for p in trecho_kw.split(",") if p.strip()]

    cache_salvar(chave, json.dumps({"resumo": leitura, "palavras_chave": palavras_chave}, ensure_ascii=False), nome_arquivo)
    return leitura, palavras_chave


def consolidar_unidades(
    titulo: str,
    unidades: list[tuple[str, str]],
    rodada: int,
    grupo_num: int,
    modelo: str,
    nome_arquivo: str = "",
) -> str:
    """Consolida poucas unidades adjacentes numa síntese intermediária."""
    escopo = f"{unidades[0][0]} a {unidades[-1][0]}"
    texto = "\n\n---\n\n".join(
        [f"{rotulo}\n{conteudo}" for rotulo, conteudo in unidades]
    )
    prompt = PROMPT_INTERMEDIARIO.format(
        titulo=titulo,
        escopo=escopo,
        leituras=texto,
    )
    chave = hash_chunk(f"rodada-{rodada}-grupo-{grupo_num}\n{texto}", modelo, prompt)
    cached = cache_ler(chave, nome_arquivo)
    if cached:
        return json.loads(cached)["resumo"]

    resposta = chamar_ollama(prompt, modelo, num_predict=800)
    cache_salvar(chave, json.dumps({"resumo": resposta}, ensure_ascii=False), nome_arquivo)
    return resposta


def partes_em_unidades(unidades: list[tuple[str, str]]) -> list[int]:
    """Extrai as Partes originais citadas em rótulos e conteúdos."""
    texto = "\n".join([f"{rotulo}\n{conteudo}" for rotulo, conteudo in unidades])
    partes = [int(p) for p in re.findall(r"\[Parte\s+(\d+)\]", texto)]
    return sorted(set(partes))


def formatar_partes_cobertas(partes: list[int]) -> str:
    if not partes:
        return "não identificado"
    return ", ".join([f"[Parte {p}]" for p in partes])


def compactar_unidades_restantes(unidades: list[tuple[str, str]]) -> str:
    """Monta as unidades finais preservando cobertura explícita de partes."""
    blocos = []
    for rotulo, conteudo in unidades:
        partes = [int(p) for p in re.findall(r"\[Parte\s+(\d+)\]", f"{rotulo}\n{conteudo}")]
        blocos.append(
            f"{rotulo}\nPARTES-COBERTAS: {formatar_partes_cobertas(sorted(set(partes)))}\n{conteudo}"
        )
    return "\n\n---\n\n".join(blocos)


def reduzir_leituras_em_rodadas(
    titulo: str,
    leituras: list[str],
    modelo: str,
    nome_arquivo: str = "",
) -> str:
    """Reduz leituras em rodadas pequenas antes da ficha final."""
    unidades = [(f"[Parte {i + 1}]", leitura) for i, leitura in enumerate(leituras)]
    rodada = 1

    while len(unidades) > MAX_UNIDADES_FINAIS and rodada <= MAX_RODADAS_SINTESE:
        novas_unidades = []
        total_grupos = -(-len(unidades) // GRUPO_MAX_PARTES)
        for i in range(0, len(unidades), GRUPO_MAX_PARTES):
            grupo = unidades[i:i + GRUPO_MAX_PARTES]
            grupo_num = i // GRUPO_MAX_PARTES + 1
            partes_cobertas = partes_em_unidades(grupo)
            resumo_grupo = consolidar_unidades(
                titulo=titulo,
                unidades=grupo,
                rodada=rodada,
                grupo_num=grupo_num,
                modelo=modelo,
                nome_arquivo=nome_arquivo,
            )
            if "PARTES-COBERTAS:" not in resumo_grupo:
                resumo_grupo = (
                    f"PARTES-COBERTAS:\n{formatar_partes_cobertas(partes_cobertas)}\n\n"
                    + resumo_grupo
                )
            rotulo = (
                f"[Síntese R{rodada}G{grupo_num} - "
                f"{formatar_partes_cobertas(partes_cobertas)}]"
            )
            novas_unidades.append((rotulo, resumo_grupo))
            print(f"    -> Rodada {rodada}: grupo {grupo_num}/{total_grupos} consolidado")
        unidades = novas_unidades
        rodada += 1

    return compactar_unidades_restantes(unidades)


def ficha_valida(ficha: str, total_partes: int = 0) -> bool:
    """Confere se a consolidação obedeceu ao molde mínimo esperado."""
    if any(titulo not in ficha for titulo in FICHA_TITULOS_OBRIGATORIOS):
        return False
    proibidos = ["**Resumo e Análise**", "### Resumo", "#### Parte"]
    if any(marca in ficha for marca in proibidos):
        return False

    partes = [int(p) for p in re.findall(r"\[Parte\s+(\d+)\]", ficha)]
    distintas = set(partes)
    if total_partes >= 8:
        tem_inicio = any(p <= 3 for p in distintas)
        tem_meio = any(4 <= p <= max(4, total_partes - 3) for p in distintas)
        tem_fim = any(p >= total_partes - 2 for p in distintas)
        return len(distintas) >= 3 and tem_inicio and tem_meio and tem_fim
    if total_partes >= 5:
        return len(distintas) >= 3
    return True


def separar_ficha_e_palavras_chave(resposta: str) -> tuple[str, list[str]]:
    partes = re.split(r"PALAVRAS-CHAVE-FINAIS\s*:", resposta, maxsplit=1, flags=re.IGNORECASE)
    ficha = partes[0].replace("FICHA:", "").strip()
    palavras_chave = []
    if len(partes) > 1:
        palavras_chave = [limpar_palavra_chave(p) for p in partes[1].split(",")]
        palavras_chave = [p for p in palavras_chave if p]
    return limpar_ficha(ficha), palavras_chave


def consolidar_resumos(
    titulo: str,
    leituras: list[str],
    modelo: str,
    nome_arquivo: str = "",
    modelo_embedding: str = "",
    usar_estrutura: bool = False,
    usar_ressonancias: bool = False,
) -> tuple[str, list[str]]:
    """Consolida leituras-brutas diretamente numa ficha de rastros para releitura."""
    texto_leitura_bruta = "\n\n---\n\n".join(
        [f"[Parte {i + 1}]\n{r}" for i, r in enumerate(leituras)]
    )

    # Pipeline mínimo: para textos pré-curados, a ficha final nasce diretamente
    # das leituras-brutas. As camadas de redução intermediária, estrutura,
    # ressonância e vizinhança semântica ficam preservadas no arquivo,
    # mas não são chamadas aqui.
    estrutura_texto = ""
    ressonancias = ""
    vizinhancas_semanticas = ""
    inventario_cobertura = montar_inventario_cobertura(texto_leitura_bruta)
    metadados_preservados = extrair_metadados_leituras(texto_leitura_bruta)
    prompt = PROMPT_CONSOLIDAR.format(
        titulo=titulo,
        inventario_cobertura=inventario_cobertura,
        metadados_preservados=metadados_preservados,
        leitura_bruta=texto_leitura_bruta,
    )
    resposta = chamar_ollama(
        prompt,
        modelo,
        num_predict=5000,
        num_ctx=16384,
        temperature=0.5,
    )
    ficha, palavras_chave = separar_ficha_e_palavras_chave(resposta)

    if not ficha_valida(ficha, total_partes=len(leituras)):
        titulos_obrigatorios = ", ".join(FICHA_TITULOS_OBRIGATORIOS)
        prompt_retry = (
            "A resposta anterior não seguiu o molde. Refaça do zero.\n"
            f"Comece obrigatoriamente com {FICHA_TITULOS_OBRIGATORIOS[0]} e use todos os títulos pedidos: {titulos_obrigatorios}.\n"
            "Não escreva Resumo, Análise, Conclusão, Parte 1, Parte 2 ou títulos com ###.\n\n"
            "Para este texto longo, cite explicitamente partes do início, do meio e do fim.\n"
            "Não concentre a ficha em apenas uma ou duas partes.\n\n"
            + prompt
        )
        resposta = chamar_ollama(
            prompt_retry,
            modelo,
            num_predict=5000,
            num_ctx=16384,
            temperature=0.5,
        )
        ficha, palavras_chave = separar_ficha_e_palavras_chave(resposta)

    return ficha, palavras_chave


# ─────────────────────────────────────────────
# Funções para gerar CAMADA 2 (estrutura) e CAMADA 3 (ressonâncias)
# ─────────────────────────────────────────────

def gerar_estrutura_texto(
    titulo: str,
    texto_leitura_bruta: str,
    modelo: str,
    nome_arquivo: str = "",
) -> str:
    """Gera a camada estrutural do texto antes da ficha final."""
    prompt = PROMPT_ESTRUTURA_TEXTO.format(
        titulo=titulo,
        leitura_bruta=texto_leitura_bruta,
    )
    chave = hash_chunk(f"estrutura-texto\n{texto_leitura_bruta}", modelo, prompt)
    cached = cache_ler(chave, nome_arquivo)
    if cached:
        return json.loads(cached)["resumo"]

    resposta = chamar_ollama(prompt, modelo, num_predict=1600, num_ctx=16384)
    cache_salvar(chave, json.dumps({"resumo": resposta}, ensure_ascii=False), nome_arquivo)
    return resposta


def gerar_ressonancias_controladas(
    titulo: str,
    texto_leitura_bruta: str,
    modelo: str,
    nome_arquivo: str = "",
) -> str:
    """Gera a camada de ressonâncias sem misturar inferência com tese do texto."""
    prompt = PROMPT_RESSONANCIAS.format(
        titulo=titulo,
        leitura_bruta=texto_leitura_bruta,
    )
    chave = hash_chunk(f"ressonancias-internas\n{texto_leitura_bruta}", modelo, prompt)
    cached = cache_ler(chave, nome_arquivo)
    if cached:
        return json.loads(cached)["resumo"]

    resposta = chamar_ollama(prompt, modelo, num_predict=1600, num_ctx=16384)
    cache_salvar(chave, json.dumps({"resumo": resposta}, ensure_ascii=False), nome_arquivo)
    return resposta


def limpar_palavra_chave(palavra: str) -> str:
    """Remove marcas de parte e pontuação solta de palavras-chave."""
    palavra = re.sub(r"\[Parte\s+\d+\]", "", palavra, flags=re.IGNORECASE)
    palavra = palavra.strip().strip("-*`'\". <>")
    lixo_prompt = [
        "até 8 termos",
        "até 12 termos",
        "termos específicos separados",
        "sem numeração",
        "exemplo de formato",
        "termos relevantes separados por vírgula",
    ]
    if any(lixo in palavra.lower() for lixo in lixo_prompt):
        return ""
    palavra = re.sub(r"\s+", " ", palavra)
    palavra = re.split(
        r"\n|Resumo de cada parte|Evidências|Inferências|Palavras-chave",
        palavra,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    return palavra


def limpar_ficha(ficha: str) -> str:
    """Remove sobras comuns de saída conversacional do modelo."""
    ficha = re.sub(r"\(Parte\s+(\d+)\)", r"[Parte \1]", ficha)
    cortes = [
        r"\n\s*PALAVRAS-CHAVE-FINAIS\s*:",
        r"\n\s*\*\*PALAVRAS-CHAVE-FINAIS\*\*\s*:",
        r"\n\s*\*\*Resumo de cada parte:\*\*",
        r"\n\s*Resumo de cada parte:",
        r"\n\s*###\s*\[Parte\s+\d+\]",
        r"\n\s*###\s*Conclusão",
        r"\n\s*###\s*Dúvidas Abertas",
        r"\n\s*\*\*Palavras-chave:\*\*",
        r"\n\s*\*\*Evidências e Inferências para a Pesquisa\*\*",
    ]
    for padrao in cortes:
        match = re.search(padrao, ficha, flags=re.IGNORECASE)
        if match:
            ficha = ficha[: match.start()]
            break

    linhas_limpas = []
    for linha in ficha.splitlines():
        texto = linha.strip()
        if texto in {"****", "**", "---"}:
            continue
        if texto.lower().startswith(("este esboço", "essa ficha", "esta ficha")):
            continue
        linhas_limpas.append(linha)
    return "\n".join(linhas_limpas).strip()


def limpar_tag(tag: str) -> str:
    """Normaliza uma palavra-chave para tag simples de Obsidian."""
    tag = limpar_palavra_chave(tag)
    if not tag:
        return ""
    tag = tag.strip().lower()
    tag = re.sub(r"[^\w\s-]", "", tag, flags=re.UNICODE)
    tag = re.sub(r"\s+", "-", tag)
    tag = re.sub(r"-{2,}", "-", tag)
    return tag.strip("-")


# ─────────────────────────────────────────────
# 5. EXPORTADOR OBSIDIAN
# ─────────────────────────────────────────────

def gerar_leitura_bruta_obsidian(doc: dict, leituras: list[str],
                                  palavras_chave: list[str], n_chunks: int,
                                  chunk_words: list[int]) -> str:
    """Gera o Markdown da leitura bruta, separada da ficha interpretativa."""
    palavras_chave_limpas = list(dict.fromkeys(
        [limpar_palavra_chave(p) for p in palavras_chave if limpar_palavra_chave(p)]
    ))[:16]
    tags_final = list(dict.fromkeys(
        [limpar_tag(t) for t in doc["tags"] + palavras_chave_limpas if limpar_tag(t)]
    ))[:16]
    frontmatter = {
        "titulo":           doc["titulo"],
        "arquivo-original": doc["caminho"].name,
        "tipo":             "leitura-bruta",
        "tags":             tags_final,
        "palavras-chave":   palavras_chave_limpas,
        "tamanho-palavras": doc["tamanho"],
        "chunks-processados": n_chunks,
        "palavras-por-chunk": chunk_words,
        "data-indexacao":   datetime.now().strftime("%Y-%m-%d"),
        "modelo-ollama":    doc.get("modelo", "qwen2.5:7b"),
        "modelo-consolidacao": doc.get("modelo_consolidacao", doc.get("modelo", "qwen2.5:7b")),
        "modelo-embedding": doc.get("modelo_embedding", ""),
        "revisao-humana":   False,
    }
    for campo in ("author", "authors", "year", "doi", "url", "journal", "tipo", "instituicao"):
        if campo in doc["meta"] and campo != "tipo":
            frontmatter[campo] = doc["meta"][campo]
    yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False,
                         sort_keys=False)
    partes = "\n\n---\n\n".join(
        [f"## Parte {i+1}\n\n{leitura.strip()}" for i, leitura in enumerate(leituras)]
    )

    return f"""---
{yaml_str}---

# Leitura bruta · {doc["titulo"]}

> Extração fiel gerada automaticamente por pipeline Ollama · {datetime.now().strftime("%Y-%m-%d")}
> Arquivo original: `{doc["caminho"].name}` · {doc["tamanho"]} palavras · {n_chunks} chunk(s)

---

{partes}
"""


def gerar_ficha_obsidian(doc: dict, ficha: str, palavras_chave: list[str],
                          n_chunks: int, chunk_words: list[int]) -> str:
    """Gera o conteúdo Markdown da ficha Obsidian."""
    palavras_chave_limpas = list(dict.fromkeys(
        [limpar_palavra_chave(p) for p in palavras_chave if limpar_palavra_chave(p)]
    ))[:12]
    tags_final = list(dict.fromkeys(
        [limpar_tag(t) for t in doc["tags"] + palavras_chave_limpas if limpar_tag(t)]
    ))[:12]

    frontmatter = {
        "titulo":           doc["titulo"],
        "arquivo-original": doc["caminho"].name,
        "tags":             tags_final,
        "palavras-chave":   palavras_chave_limpas,
        "tamanho-palavras": doc["tamanho"],
        "chunks-processados": n_chunks,
        "palavras-por-chunk": chunk_words,
        "data-indexacao":   datetime.now().strftime("%Y-%m-%d"),
        "modelo-ollama":    doc.get("modelo", "qwen2.5:7b"),
        "modelo-consolidacao": doc.get("modelo_consolidacao", doc.get("modelo", "qwen2.5:7b")),
        "modelo-embedding": doc.get("modelo_embedding", ""),
        "revisao-humana":   False,
    }

    # Adiciona metadados originais se existirem
    for campo in ("author", "authors", "year", "doi", "url", "journal", "tipo", "instituicao"):
        if campo in doc["meta"]:
            frontmatter[campo] = doc["meta"][campo]

    yaml_str  = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False,
                           sort_keys=False)
    links_str = " ".join([f"[[{t}]]" for t in tags_final[:6]])

    return f"""---
{yaml_str}---

# {doc["titulo"]}

> Ficha gerada automaticamente por pipeline Ollama · {datetime.now().strftime("%Y-%m-%d")}
> Arquivo original: `{doc["caminho"].name}` · {doc["tamanho"]} palavras · {n_chunks} chunk(s)

---

{ficha}

---

## Tags e Conexões

{links_str}
"""


def carregar_leitura_bruta_processada(leitura_path: Path) -> tuple[list[str], list[str], list[int]]:
    """Lê uma leitura bruta já gerada e recupera partes/palavras-chave."""
    texto = leitura_path.read_text(encoding="utf-8")
    meta, corpo = extrair_frontmatter(texto)
    partes = re.split(r"\n---\n\n## Parte\s+\d+\n\n", "\n" + corpo)
    leituras = [p.strip() for p in partes[1:] if p.strip()]
    palavras_chave = meta.get("palavras-chave", []) or []
    chunk_words = meta.get("palavras-por-chunk", []) or []
    return leituras, palavras_chave, chunk_words


# ─────────────────────────────────────────────
# 6. PIPELINE PRINCIPAL
# ─────────────────────────────────────────────

def processar_documento(caminho: Path, pasta_saida: Path, pasta_leituras: Path,
                         modelo: str,
                         modelo_consolidacao: str = "",
                         pasta_embeddings: Path | None = None,
                         modelo_embedding: str = "",
                         gerar_embeddings: bool = True,
                         sqlite_busca_path: Path | None = None,
                         indexar_sqlite: bool = True,
                         modo: str = "completo",
                         forcar: bool = False,
                         chunk_palavras: int = CHUNK_MAX_WORDS,
                         chunk_sobreposicao: int = CHUNK_OVERLAP,
                         usar_estrutura: bool = True,
                         usar_ressonancias: bool = True) -> dict:
    """Processa um único .md e salva leitura bruta + ficha. Retorna status."""
    pasta_saida.mkdir(parents=True, exist_ok=True)
    pasta_leituras.mkdir(parents=True, exist_ok=True)
    saida_path = pasta_saida / f"FICHA_{caminho.stem}.md"
    leitura_path = pasta_leituras / f"LEITURA_{caminho.stem}.md"
    if modo == "completo" and saida_path.exists() and leitura_path.exists() and not forcar:
        return {"arquivo": caminho.name, "status": "pulado (já existe)"}
    if modo == "indexar" and leitura_path.exists() and not forcar:
        return {"arquivo": caminho.name, "status": "pulado (leitura já existe)"}
    if modo == "fichar" and saida_path.exists() and not forcar:
        return {"arquivo": caminho.name, "status": "pulado (ficha já existe)"}

    try:
        doc    = ler_arquivo(caminho)
        doc["modelo"] = modelo
        doc["modelo_consolidacao"] = modelo_consolidacao or modelo
        doc["modelo_embedding"] = modelo_embedding if gerar_embeddings else ""
        titulo_prompt = identificar_documento_para_prompt(doc)
        nome_cache = caminho.stem  # subpasta do cache com o nome do arquivo

        if modo == "fichar":
            if not leitura_path.exists():
                return {"arquivo": caminho.name, "status": "ERRO: leitura bruta não encontrada; rode --modo indexar primeiro"}
            leituras, palavras_chave_leitura, chunk_words = carregar_leitura_bruta_processada(leitura_path)
            todas_kw = list(doc["tags"]) + palavras_chave_leitura
        else:
            chunks = quebrar_em_chunks(
                doc["corpo"],
                max_palavras=chunk_palavras,
                sobreposicao=chunk_sobreposicao,
            )
            chunk_words = [len(chunk.split()) for chunk in chunks]
            # Resume cada chunk sequencialmente para não sobrecarregar o Ollama local.
            leituras = []
            todas_kw = list(doc["tags"])
            total_partes = len(chunks)
            for i, chunk in enumerate(chunks, start=1):
                leitura, kw = resumir_chunk(
                    chunk=chunk,
                    modelo=modelo,
                    titulo=titulo_prompt,
                    parte_atual=i,
                    total_partes=total_partes,
                    nome_arquivo=nome_cache,
                )
                leituras.append(leitura)
                todas_kw.extend(kw)

            leitura_conteudo = gerar_leitura_bruta_obsidian(doc, leituras, todas_kw, len(chunks), chunk_words)
            leitura_path.write_text(leitura_conteudo, encoding="utf-8")

        if modo in {"indexar", "completo"} and indexar_sqlite and sqlite_busca_path is not None:
            sqlite_path = indexar_chunks_sqlite(doc=doc, chunks=chunks, db_path=sqlite_busca_path)
            print(f"    -> Busca textual SQLite: {sqlite_path}")

        if modo in {"indexar", "completo"} and gerar_embeddings and modelo_embedding and pasta_embeddings is not None:
            embeddings_path = gerar_embeddings_documento(
                doc=doc,
                leituras=leituras,
                pasta_embeddings=pasta_embeddings,
                modelo_embedding=modelo_embedding,
            )
            print(f"    -> Embeddings: {embeddings_path}")

        if modo == "indexar":
            return {"arquivo": caminho.name, "status": "ok (indexado)", "chunks": len(chunks)}

        # Consolida sempre, mesmo em documentos curtos, para manter formato único.
        ficha_texto, kw_final = consolidar_resumos(
            titulo_prompt,
            leituras,
            modelo_consolidacao or modelo,
            nome_cache,
            modelo_embedding if gerar_embeddings else "",
            usar_estrutura=usar_estrutura,
            usar_ressonancias=usar_ressonancias,
        )

        todas_kw = list(dict.fromkeys(todas_kw + kw_final))[:12]
        conteudo = gerar_ficha_obsidian(doc, ficha_texto, todas_kw, len(leituras), chunk_words)
        saida_path.write_text(conteudo, encoding="utf-8")

        return {"arquivo": caminho.name, "status": "ok", "chunks": len(leituras)}

    except Exception as e:
        return {"arquivo": caminho.name, "status": f"ERRO: {e}"}


def verificar_ollama(modelo: str):
    """Verifica se o Ollama está rodando e o modelo disponível."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        modelos = [m["name"] for m in r.json().get("models", [])]
        if not any(modelo in m for m in modelos):
            print(f"⚠️  Modelo '{modelo}' não encontrado. Modelos disponíveis: {modelos}")
            print(f"   Execute: ollama pull {modelo}")
            return False
        return True
    except requests.ConnectionError:
        print("❌ Ollama não está rodando. Execute: ollama serve")
        return False


def status_ok(status: str) -> bool:
    return status.startswith("ok")


def status_pulado(status: str) -> bool:
    return "pulado" in status


def main():
    parser = argparse.ArgumentParser(description="Roer textos Markdown → leituras, embeddings e fichas Obsidian")
    parser.add_argument("--pasta",    required=True,        help="Pasta com os .md de referências")
    parser.add_argument("--saida",    required=True,        help="Pasta de saída das fichas")
    parser.add_argument("--leituras", default="leituras-brutas", help="Pasta de saída das leituras brutas")
    parser.add_argument("--modelo",   default="qwen2.5:7b", help="Modelo Ollama")
    parser.add_argument("--modelo-consolidacao", default="", help="Modelo Ollama opcional para resumos intermediários e ficha final")
    parser.add_argument("--modelo-embedding", default=MODELO_EMBEDDING_PADRAO, help="Modelo Ollama para embeddings em lote")
    parser.add_argument("--embeddings", default=str(EMBEDDINGS_DIR_PADRAO), help="Pasta de saída dos embeddings JSONL")
    parser.add_argument("--sqlite", default=str(SQLITE_BUSCA_PADRAO), help="Banco SQLite FTS para busca textual dos chunks originais")
    parser.add_argument("--chunk-palavras", type=int, default=CHUNK_MAX_WORDS, help="Número máximo de palavras por chunk")
    parser.add_argument("--chunk-sobreposicao", type=int, default=CHUNK_OVERLAP, help="Número de palavras de sobreposição entre chunks")
    parser.add_argument("--sem-embeddings", action="store_true", help="Não gera embeddings antes da consolidação")
    parser.add_argument("--sem-sqlite", action="store_true", help="Não indexa chunks originais no SQLite de busca textual")
    parser.add_argument("--sem-estrutura", action="store_true", help="Não gera camada intermediária de estrutura do texto")
    parser.add_argument("--sem-ressonancias", action="store_true", help="Não gera camada intermediária de ressonâncias internas")
    parser.add_argument("--modo", choices=("completo", "indexar", "fichar"), default="completo", help="Etapa do pipeline a executar")
    parser.add_argument("--workers",  type=int, default=1,  help="Documentos em paralelo (cuidado com RAM)")
    parser.add_argument("--forcar",   action="store_true",  help="Re-processa fichas já existentes")
    parser.add_argument("--limite",   type=int, default=0,  help="Processa só N arquivos (teste)")
    parser.add_argument("--arquivo",  default="",           help="Processa um único arquivo pelo nome (ex: jackson-repair.md)")
    args = parser.parse_args()

    pasta_entrada = Path(args.pasta)
    pasta_saida   = Path(args.saida)
    pasta_leituras = Path(args.leituras)
    pasta_embeddings = Path(args.embeddings)
    sqlite_busca_path = Path(args.sqlite)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    pasta_leituras.mkdir(parents=True, exist_ok=True)
    if not args.sem_embeddings:
        pasta_embeddings.mkdir(parents=True, exist_ok=True)

    # Verificação inicial
    if args.modo in {"completo", "indexar"} and not verificar_ollama(args.modelo):
        return
    if args.modo in {"completo", "indexar"} and not args.sem_embeddings and not verificar_ollama(args.modelo_embedding):
        return
    if args.modo in {"completo", "fichar"} and args.modelo_consolidacao and not verificar_ollama(args.modelo_consolidacao):
        return
    if args.modo == "fichar" and not args.modelo_consolidacao and not verificar_ollama(args.modelo):
        return

    # Filtro por arquivo específico
    if args.arquivo:
        nome = args.arquivo if args.arquivo.endswith(".md") else f"{args.arquivo}.md"
        alvo = pasta_entrada / nome
        if not alvo.exists():
            print(f"❌ Arquivo não encontrado: {alvo}")
            return
        arquivos = [alvo]
    else:
        arquivos = sorted(pasta_entrada.glob("*.md"))
        if not arquivos:
            print(f"Nenhum .md encontrado em {pasta_entrada}")
            return
        if args.limite:
            arquivos = arquivos[:args.limite]

    print(f"\n📚 {len(arquivos)} arquivo(s) encontrado(s)")
    modelo_ficha = args.modelo_consolidacao or args.modelo
    modelo_embedding = "desativado" if args.sem_embeddings or args.modo == "fichar" else args.modelo_embedding
    print(
        f"🤖 Modelo leitura: {args.modelo} | Embeddings: {modelo_embedding} | "
        f"Modelo ficha: {modelo_ficha} | Workers: {args.workers}"
    )
    print(f"🧩 Chunk max words: {args.chunk_palavras} | Chunk overlap: {args.chunk_sobreposicao}")
    print("🧠 Pipeline mínimo: leituras-brutas → ficha final")
    print(f"⚙️  Modo: {args.modo}")
    print(f"📝 Leituras: {pasta_leituras} | Fichas: {pasta_saida}\n")
    if not args.sem_embeddings and args.modo != "fichar":
        print(f"🧭 Embeddings: {pasta_embeddings}\n")
    if not args.sem_sqlite and args.modo != "fichar":
        print(f"🔎 Busca textual SQLite: {sqlite_busca_path}\n")
    print("🧠 Memória conceitual: desativada no roer.py\n")

    resultados = []

    # Workers > 1 só faz sentido se tiveres GPU ou muita RAM
    if args.workers == 1:
        for arq in tqdm(arquivos, desc="Processando", unit="doc"):
            r = processar_documento(
                arq,
                pasta_saida,
                pasta_leituras,
                args.modelo,
                args.modelo_consolidacao,
                pasta_embeddings,
                args.modelo_embedding,
                not args.sem_embeddings,
                sqlite_busca_path,
                not args.sem_sqlite,
                args.modo,
                args.forcar,
                args.chunk_palavras,
                args.chunk_sobreposicao,
                not args.sem_estrutura,
                not args.sem_ressonancias,
            )
            resultados.append(r)
            status = "✅" if status_ok(r["status"]) else ("⏭️" if status_pulado(r["status"]) else "❌")
            tqdm.write(f"  {status} {r['arquivo']} — {r['status']}")
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futuros = {
                pool.submit(
                    processar_documento,
                    arq,
                    pasta_saida,
                    pasta_leituras,
                    args.modelo,
                    args.modelo_consolidacao,
                    pasta_embeddings,
                    args.modelo_embedding,
                    not args.sem_embeddings,
                    sqlite_busca_path,
                    not args.sem_sqlite,
                    args.modo,
                    args.forcar,
                    args.chunk_palavras,
                    args.chunk_sobreposicao,
                    not args.sem_estrutura,
                    not args.sem_ressonancias,
                ): arq
                for arq in arquivos
            }
            for fut in tqdm(as_completed(futuros), total=len(futuros), desc="Processando"):
                r = fut.result()
                resultados.append(r)
                status = "✅" if status_ok(r["status"]) else ("⏭️" if status_pulado(r["status"]) else "❌")
                tqdm.write(f"  {status} {r['arquivo']} — {r['status']}")

    # Relatório final
    ok      = sum(1 for r in resultados if status_ok(r["status"]))
    pulados = sum(1 for r in resultados if status_pulado(r["status"]))
    erros   = sum(1 for r in resultados if "ERRO" in r["status"])

    print(f"\n{'─'*50}")
    print(f"✅ Processados: {ok}  ⏭️  Pulados: {pulados}  ❌ Erros: {erros}")
    print(f"📁 Leituras salvas em: {pasta_leituras.resolve()}")
    print(f"📁 Fichas salvas em: {pasta_saida.resolve()}")
    print("🧠 Memória conceitual: desativada no roer.py")

    if erros:
        print("\nArquivos com erro:")
        for r in resultados:
            if "ERRO" in r["status"]:
                print(f"  • {r['arquivo']}: {r['status']}")


if __name__ == "__main__":
    main()
