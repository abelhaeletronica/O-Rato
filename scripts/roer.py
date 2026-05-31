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
"""

from __future__ import annotations
import argparse
import json
import re
import time
import math
import hashlib
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
MEMORIA_PADRAO   = Path("rato/memoria/memoria-conceitos.json")
EMBEDDINGS_DIR_PADRAO = Path(".embeddings")
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

    partes = []
    if autor:
        if isinstance(autor, list):
            autor = "; ".join(str(a) for a in autor)
        partes.append(str(autor))
    if ano:
        partes.append(str(ano))

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
# 4. MEMÓRIA CONCEITUAL
# ─────────────────────────────────────────────

AUTORES_EQUIVALENTES = {
    "barad": "Karen Barad",
    "karen barad": "Karen Barad",
    "puig de la bellacasa": "María Puig de la Bellacasa",
    "maría puig de la bellacasa": "María Puig de la Bellacasa",
    "maria puig de la bellacasa": "María Puig de la Bellacasa",
    "gordon": "Deborah Gordon",
    "deborah gordon": "Deborah Gordon",
    "verdesio": "Gustavo Verdesio",
    "gustavo verdesio": "Gustavo Verdesio",
    "nêgo bispo": "Nêgo Bispo",
    "nego bispo": "Nêgo Bispo",
    "merleau-ponty": "Merleau-Ponty",
    "krauss": "Rosalind Krauss",
    "rosalind krauss": "Rosalind Krauss",
    "benjamin": "Walter Benjamin",
    "walter benjamin": "Walter Benjamin",
    "c. g. jung": "Carl Gustav Jung",
    "c.g. jung": "Carl Gustav Jung",
    "carl jung": "Carl Gustav Jung",
    "carl gustav jung": "Carl Gustav Jung",
    "ingold": "Tim Ingold",
    "tim ingold": "Tim Ingold",
    "jackson": "Steven J. Jackson",
    "steven jackson": "Steven J. Jackson",
    "steven j. jackson": "Steven J. Jackson",
    "sennett": "Richard Sennett",
    "richard sennett": "Richard Sennett",
    "haraway": "Donna Haraway",
    "donna haraway": "Donna Haraway",
    "simondon": "Gilbert Simondon",
    "gilbert simondon": "Gilbert Simondon",
    "deleuze and guattari": "Gilles Deleuze e Félix Guattari",
    "deleuze & guattari": "Gilles Deleuze e Félix Guattari",
    "gilles deleuze and félix guattari": "Gilles Deleuze e Félix Guattari",
    "gilles deleuze e félix guattari": "Gilles Deleuze e Félix Guattari",
    "von franz": "Marie-Louise von Franz",
    "marie-louise von franz": "Marie-Louise von Franz",
    "edinger": "Edward F. Edinger",
    "edward edinger": "Edward F. Edinger",
    "edward f. edinger": "Edward F. Edinger",
    "urrutigaray": "Maria Cristina Urrutigaray",
    "maria cristina urrutigaray": "Maria Cristina Urrutigaray",
    "ortega y gasset": "José Ortega y Gasset",
    "josé ortega y gasset": "José Ortega y Gasset",
    "rawson": "Philip Rawson",
    "philip rawson": "Philip Rawson",
    "yanagi": "Soetsu Yanagi",
    "soetsu yanagi": "Soetsu Yanagi",
}

TERMOS_INFRAESTRUTURA = {
    "ocr",
    "markdown",
    "md",
    "pdf",
    "pipeline",
    "chunk",
    "chunks",
    "chunking",
    "embedding",
    "embeddings",
    "ollama",
    "obsidian",
    "yaml",
    "json",
    "api",
    "python",
    "script",
    "indexação",
    "indexacao",
    "leitura automática",
    "leitura automatica",
}

TERMOS_FRACOS = {
    "introdução",
    "introducao",
    "conclusão",
    "conclusao",
    "referências",
    "referencias",
    "bibliografia",
    "capítulo",
    "capitulo",
    "parte",
    "texto",
    "artigo",
    "livro",
    "autor",
    "autora",
    "obra",
    "exemplo",
    "processo",
    "prática",
    "pratica",
    "abordagem",
    "teoria",
    "conceito",
    "campo",
    "análise",
    "analise",
    "estudo",
    "pesquisa",
    "metodologia",
}

TIPOS_ENTIDADE = {
    "conceitos": "conceito",
    "operadores": "operador",
    "figuras": "figura-organizadora",
    "exemplos": "exemplo",
    "lugares": "lugar",
    "tecnicas": "técnica",
    "infraestrutura": "infraestrutura",
}

PALAVRAS_LUGAR = {
    "bangladesh",
    "brasil",
    "portugal",
    "lisboa",
    "rio de janeiro",
    "japão",
    "japao",
    "china",
    "coreia",
}

PALAVRAS_TECNICA = {
    "queima",
    "torno",
    "modelagem",
    "esmaltação",
    "esmaltacao",
    "engobe",
    "coiling",
    "laser-cut",
    "laser cut",
    "3d printing",
    "impressão 3d",
    "impressao 3d",
}

PALAVRAS_OPERADORAS = {
    "cuidado",
    "gesto",
    "repair",
    "manutenção",
    "manutencao",
    "precariedade",
    "continuidade",
    "textility",
    "textilidade",
    "making",
    "correspondence",
    "correspondência",
    "correspondencia",
    "itinerância",
    "itinerancia",
    "improvisação",
    "improvisacao",
    "duração",
    "duracao",
    "formação",
    "formacao",
}

PALAVRAS_FIGURAS = {
    "mito",
    "barro",
    "argila",
    "pote",
    "linha",
    "fio",
    "trama",
    "tecido",
    "grande mãe",
    "grande mae",
    "cracking",
    "rachadura",
    "dissolução",
    "dissolucao",
}


def chave_normalizada(texto: str) -> str:
    """Normaliza texto para comparação interna, preservando acentos no valor exibido."""
    texto = limpar_palavra_chave(texto).lower()
    texto = re.sub(r"\[parte\s+\d+\]", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[^\w\s\-áàâãéêíóôõúç]", " ", texto, flags=re.UNICODE)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def normalizar_autor(nome: str) -> str:
    """Remove marcas de parte e unifica autores recorrentes."""
    nome = re.sub(r"\[Parte\s+\d+\]", "", nome, flags=re.IGNORECASE)
    nome = limpar_autor_extraido(nome)
    if not nome:
        return ""
    chave = chave_normalizada(nome)
    return AUTORES_EQUIVALENTES.get(chave, nome)


def tipo_entidade(termo: str) -> str:
    """Classifica uma palavra-chave para evitar que tudo vire conceito forte."""
    chave = chave_normalizada(termo)
    if not chave:
        return "descartar"
    if chave in TERMOS_INFRAESTRUTURA:
        return "infraestrutura"
    if chave in TERMOS_FRACOS:
        return "descartar"
    if chave in PALAVRAS_LUGAR:
        return "lugar"
    if chave in PALAVRAS_TECNICA:
        return "técnica"
    if chave in PALAVRAS_OPERADORAS:
        return "operador"
    if chave in PALAVRAS_FIGURAS:
        return "figura-organizadora"
    if len(chave) < 3:
        return "descartar"
    if len(chave.split()) > 5:
        return "descartar"
    return "conceito"


def conceito_deve_entrar_na_memoria(termo: str) -> bool:
    """Filtra termos fracos antes de atualizar a memória conceitual."""
    return tipo_entidade(termo) not in {"descartar", "infraestrutura", "lugar"}


def deduplicar_lista_preservando_ordem(valores: list[str]) -> list[str]:
    vistos = set()
    saida = []
    for valor in valores:
        if not valor:
            continue
        chave = chave_normalizada(valor)
        if chave and chave not in vistos:
            vistos.add(chave)
            saida.append(valor)
    return saida


def deduplicar_revisitacoes(revisitacoes: list[dict]) -> list[dict]:
    """Mantém apenas uma revisitação por arquivo/data/motivo."""
    vistos = set()
    saida = []
    for item in revisitacoes:
        chave = (
            item.get("data", ""),
            item.get("arquivo", ""),
            item.get("motivo", ""),
        )
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(item)
    return saida[-100:]


MEMORIA_INICIAL = {
    "conceitos_recorrentes": [
        "gesto",
        "repair",
        "precariedade",
        "manutenção",
        "cuidado",
        "matéria",
        "continuidade",
        "agential realism",
        "stigmergy",
        "emergência situada",
        "energeia",
        "ergon",
        "niche construction",
        "comportamento coletivo",
    ],
    "autores_recorrentes": [
        "Ingold",
        "Jackson",
        "Benjamin",
        "Haraway",
        "Simondon",
        "Karen Barad",
        "María Puig de la Bellacasa",
        "Deborah Gordon",
        "Gustavo Verdesio",
        "Nêgo Bispo",
        "Merleau-Ponty",
        "Krauss",
    ],
    "perguntas_abertas": [
        "O reparo sustenta ou resiste ao sistema?",
        "Como o gesto produz continuidade?",
        "Como a precariedade produz relação?",
        "Em que medida o comportamento coletivo dos insetos é análogo à agential realism de Barad?",
        "O que distingue gesto de modelo na construção coletiva?",
        "Como a stigmergy opera como cuidado material sem sujeito intencional?",
        "O que a energeia (em oposição ao ergon) preserva do processo coletivo?",
    ],
    "conceitos": {
        "repair": {
            "relacoes": [
                "care",
                "maintenance",
                "gesture",
                "fragility",
                "continuity",
            ],
            "autores": [
                "Steven Jackson",
                "Sennett",
            ],
            "ocorrencias": 0,
            "ocorrencias_documentos": 0,
            "ocorrencias_chunks": 0,
            "arquivos": [],
            "historico": [],
            "grau_de_rastreabilidade": "manual",
        }
    },
    "revisitacoes": [],
    "principios_de_leitura": [
        "toda afirmação substantiva precisa de âncora textual identificável",
        "distinguir sempre o que o autor afirma do que o leitor infere",
        "contradições internas do texto devem ser mantidas como contradições",
        "fidelidade ao texto tem prioridade sobre utilidade imediata para a pesquisa",
        "uma fonte não rastreada não é conhecimento; é hipótese",
    ],
}


def carregar_memoria(caminho: Path) -> dict:
    if caminho.exists():
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            dados = {}
    else:
        dados = {}

    memoria = dict(MEMORIA_INICIAL)
    for chave, valor in dados.items():
        memoria[chave] = valor
    memoria.setdefault("conceitos", {})
    memoria.setdefault("revisitacoes", [])
    memoria.setdefault("entidades", {})
    memoria.setdefault("conceitos_por_tipo", {})
    return memoria


def salvar_memoria(caminho: Path, memoria: dict) -> None:
    memoria["revisitacoes"] = deduplicar_revisitacoes(memoria.get("revisitacoes", []))
    memoria["autores_recorrentes"] = deduplicar_lista_preservando_ordem(
        [normalizar_autor(a) for a in memoria.get("autores_recorrentes", []) if normalizar_autor(a)]
    )[:50]
    caminho.write_text(
        json.dumps(memoria, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def formatar_memoria_para_prompt(memoria: dict, limite_conceitos: int = 12) -> str:
    conceitos_recorrentes = memoria.get("conceitos_recorrentes", [])[:limite_conceitos]
    autores_recorrentes = memoria.get("autores_recorrentes", [])[:limite_conceitos]
    perguntas_abertas = memoria.get("perguntas_abertas", [])[:limite_conceitos]

    conceitos_detalhados = sorted(
        memoria.get("conceitos", {}).items(),
        key=lambda item: (
            item[1].get("ocorrencias_documentos", item[1].get("ocorrencias", 0)),
            item[1].get("ocorrencias_chunks", 0),
        ),
        reverse=True,
    )[:limite_conceitos]

    linhas = ["AMBIENTE CONCEITUAL DA PESQUISA:"]
    if conceitos_recorrentes:
        linhas.append("\nConceitos recorrentes:")
        linhas.extend([f"- {c}" for c in conceitos_recorrentes])
    if autores_recorrentes:
        linhas.append("\nAutores recorrentes:")
        linhas.extend([f"- {a}" for a in autores_recorrentes])
    if perguntas_abertas:
        linhas.append("\nPerguntas abertas:")
        linhas.extend([f"- {p}" for p in perguntas_abertas])
    if conceitos_detalhados:
        linhas.append("\nConceitos observados em leituras anteriores:")
        for conceito, dados in conceitos_detalhados:
            relacoes = ", ".join(dados.get("relacoes", [])[:5]) or "sem relações registradas"
            autores = ", ".join(dados.get("autores", [])[:5]) or "sem autores registrados"
            docs = dados.get("ocorrencias_documentos", dados.get("ocorrencias", 0))
            chunks = dados.get("ocorrencias_chunks", 0)
            rastreabilidade = dados.get("grau_de_rastreabilidade", "não informado")
            tipo = dados.get("tipo", "conceito")
            linhas.append(
                f"- {conceito}: tipo {tipo}; relações ({relacoes}); autores ({autores}); "
                f"documentos {docs}; chunks {chunks}; rastreabilidade {rastreabilidade}"
            )
    principios = memoria.get("principios_de_leitura", [])
    if principios:
        linhas.append("\nPrincípios de leitura desta biblioteca:")
        linhas.extend([f"- {p}" for p in principios])

    return "\n".join(linhas)


def adicionar_meses(data: datetime, meses: int) -> datetime:
    mes = data.month - 1 + meses
    ano = data.year + mes // 12
    mes = mes % 12 + 1
    dia = min(data.day, calendar.monthrange(ano, mes)[1])
    return data.replace(year=ano, month=mes, day=dia)


def extrair_autores_das_leituras(leituras: list[str]) -> list[str]:
    """Extrai autores apenas da seção AUTORES-OBRAS de cada leitura bruta."""
    autores = []
    for leitura in leituras:
        match = re.search(
            r"AUTORES-OBRAS:\s*\n(.*?)(?=\n[A-Z][A-Z\-]+:|$)",
            leitura,
            flags=re.DOTALL,
        )
        if not match:
            continue
        bloco = match.group(1)
        if "não identificado" in bloco.lower():
            continue
        for linha in bloco.splitlines():
            nome = normalizar_autor(linha)
            if nome:
                autores.append(nome)
    return list(dict.fromkeys(autores))[:12]


def limpar_autor_extraido(linha: str) -> str:
    """Filtra headings, citações e frases antes de gravar autores."""
    nome = linha.strip().lstrip("-•*").strip()
    if not nome or "não identificado" in nome.lower():
        return ""

    rejeitar_prefixos = (
        "#",
        ">",
        "capítulo",
        "capitulo",
        "textos originais",
        "referências",
        "referencias",
        "bibliografia",
    )
    if nome.lower().startswith(rejeitar_prefixos):
        return ""
    if any(c in nome for c in ['"', "“", "”", "‘", "’"]):
        return ""
    if len(nome) > 80:
        return ""
    if nome.endswith(".") and len(nome.split()) > 3:
        return ""
    if re.search(r"\b(is|are|was|were|may be|pode ser|é|são|foram)\b", nome, flags=re.IGNORECASE):
        return ""

    nome = re.split(r"[,(:;]| — | – | - ", nome)[0].strip()
    nome = re.sub(r"\s+", " ", nome)
    if not nome or len(nome) < 3:
        return ""
    palavras = nome.split()
    if len(palavras) > 5:
        return ""

    particulas = {"de", "da", "do", "das", "dos", "du", "von", "van", "y", "e"}
    tem_nome_proprio = any(
        p[:1].isupper()
        for p in palavras
        if p.lower() not in particulas
    )
    if not tem_nome_proprio:
        return ""
    if nome.isupper() and len(palavras) > 1:
        return ""
    return nome


def atualizar_memoria(memoria: dict, doc: dict, palavras_chave: list[str], leituras: list[str]) -> dict:
    autores_encontrados = extrair_autores_das_leituras(leituras)
    conceitos = memoria.setdefault("conceitos", {})
    entidades = memoria.setdefault("entidades", {})
    conceitos_por_tipo = memoria.setdefault("conceitos_por_tipo", {})
    hoje = datetime.now().strftime("%Y-%m-%d")
    arquivo = doc["caminho"].name

    conceitos_limpos = []
    for palavra in palavras_chave:
        termo = limpar_palavra_chave(palavra)
        if not termo:
            continue
        chave = chave_normalizada(termo)
        if not chave or len(chave) < 3:
            continue
        categoria = tipo_entidade(chave)
        if categoria == "descartar":
            continue
        entidades.setdefault(categoria, [])
        entidades[categoria] = deduplicar_lista_preservando_ordem(entidades[categoria] + [chave])[:200]
        conceitos_por_tipo.setdefault(categoria, [])
        conceitos_por_tipo[categoria] = deduplicar_lista_preservando_ordem(conceitos_por_tipo[categoria] + [chave])[:200]
        if conceito_deve_entrar_na_memoria(chave):
            conceitos_limpos.append(chave)

    frequencia_no_processamento = Counter(conceitos_limpos)

    for chave, frequencia in frequencia_no_processamento.items():
        categoria = tipo_entidade(chave)
        entrada = conceitos.setdefault(chave, {
            "tipo": categoria,
            "relacoes": [],
            "autores": [],
            "ocorrencias": 0,
            "ocorrencias_documentos": 0,
            "ocorrencias_chunks": 0,
            "arquivos": [],
            "historico": [],
            "grau_de_rastreabilidade": "médio",
        })
        entrada.setdefault("tipo", categoria)
        arquivos_anteriores = set(entrada.get("arquivos", []))
        if arquivo not in arquivos_anteriores:
            entrada["ocorrencias_documentos"] = int(
                entrada.get("ocorrencias_documentos", entrada.get("ocorrencias", 0))
            ) + 1
        else:
            entrada.setdefault(
                "ocorrencias_documentos",
                int(entrada.get("ocorrencias", len(arquivos_anteriores))),
            )
        entrada["ocorrencias_chunks"] = int(entrada.get("ocorrencias_chunks", 0)) + int(frequencia)
        entrada["ocorrencias"] = int(entrada.get("ocorrencias_documentos", 0))
        entrada.setdefault("grau_de_rastreabilidade", "médio")
        entrada["autores"] = deduplicar_lista_preservando_ordem(
            entrada.get("autores", []) + autores_encontrados
        )[:12]
        entrada["arquivos"] = deduplicar_lista_preservando_ordem(
            entrada.get("arquivos", []) + [arquivo]
        )[:20]

        historico = entrada.get("historico", [])
        evento = {
            "data": hoje,
            "arquivo": arquivo,
            "foco": "leitura automática",
            "ocorrencias_no_processamento": int(frequencia),
            "tipo": entrada.get("tipo", categoria),
            "grau_de_rastreabilidade": entrada.get("grau_de_rastreabilidade", "médio"),
        }
        if not any(h.get("data") == hoje and h.get("arquivo") == arquivo for h in historico):
            historico.append(evento)
        entrada["historico"] = historico[-12:]

    memoria["revisitacoes"] = deduplicar_revisitacoes(memoria.get("revisitacoes", []) + [{
        "data": hoje,
        "arquivo": arquivo,
        "titulo": doc["titulo"],
        "motivo": "nova leitura indexada",
        "proxima_revisita_sugerida": adicionar_meses(datetime.now(), 3).strftime("%Y-%m-%d"),
    }])
    return memoria


# ─────────────────────────────────────────────
# 5. AGENTE OLLAMA
# ─────────────────────────────────────────────

PROMPT_LEITURA_BRUTA = """\
Leia o trecho abaixo com atenção.

Não faça um resumo escolar.
Não tente explicar tudo.

Tente perceber:
- o que parece organizar o trecho;
- quais relações aparecem;
- quais conceitos retornam;
- quais passagens parecem densas, difíceis ou resistentes à simplificação;
- quais termos técnicos, estrangeiros ou exógenos parecem importantes.

Nem todos os elementos têm o mesmo peso.
Use apenas o que aparece no trecho.
Preserve termos técnicos importantes no idioma original quando necessário:
Umwelt, affordances, stochasticity, niche construction, feedback, algorithm, collective behavior.

Se o trecho for bibliografia, agradecimentos, ficha catalográfica, lista de links ou notas editoriais, classifique como MATERIAL PARATEXTUAL.

Identificação do texto: "{titulo}".
Este é o trecho {parte_atual} de {total_partes}.

Formato obrigatório:

LEITURA-BRUTA:

MOVIMENTO-DO-TRECHO:
o que parece acontecer ou se reorganizar neste trecho, com [Parte {parte_atual}]

OPERADORES:
conceitos que organizam relações no trecho, com [Parte {parte_atual}]

TENSÕES:
polaridades, conflitos ou instabilidades importantes, com [Parte {parte_atual}]

ZONAS-DENSAS:
passagens, conceitos ou relações que parecem difíceis,
instáveis ou potencialmente centrais, com [Parte {parte_atual}]

TERMOS-EXÓGENOS:
termos técnicos, estrangeiros ou conceitualmente densos
que parecem importantes

EXEMPLOS:
casos, imagens, situações ou objetos mencionados no trecho

AUTORES-OBRAS:
autores e obras citados no trecho, se houver

PALAVRAS-CHAVE:
termos relevantes separados por vírgula.
Exemplo: collective behavior, environment, feedback

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
Não tente tornar o texto mais claro do que ele é.

Procure o eixo que parece emergir da repetição, da dificuldade e das relações entre partes.
Baixa confiança não é erro quando aparece junto com recorrência e conectividade.

Formato obrigatório:

ESTRUTURA-DO-TEXTO:

EIXO-EMERGENTE:
qual problema parece organizar o texto inteiro, com [Parte N]

RECORRÊNCIAS-CENTRAIS:
conceitos ou termos que retornam e conectam partes diferentes, com [Parte N]

ZONAS-DE-CENTRALIDADE-INSTÁVEL:
conceitos difíceis, exógenos ou instáveis que podem ser centrais, com [Parte N]

OPERADORES:
conceitos que organizam relações, e não apenas temas recorrentes, com [Parte N]

PASSAGENS-DENSAS:
frases ou ideias que reorganizam a leitura do texto, com [Parte N]

TENSÕES:
polaridades ou conflitos que estruturam o argumento, com [Parte N]

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
Você está observando possíveis ressonâncias entre um texto chamado "{titulo}" e a pesquisa do leitor.

Use as notas do texto e a memória conceitual abaixo.
Separe rigorosamente:
- o que vem do texto;
- o que é inferência do leitor.

Não atribua ao autor conceitos que aparecem apenas na memória conceitual.
Prefira aproximações parciais a equivalências fortes.

Formato obrigatório:

RESSONANCIAS-COM-A-PESQUISA:

CONCEITOS-DO-TEXTO:
conceitos do próprio texto, com base [Parte N]

RESSONÂNCIAS-POSSÍVEIS:
aproximações parciais com a pesquisa do leitor, marcadas como hipótese e com GRAU-DE-CONFIANÇA

LIMITES:
onde a aproximação pode forçar ou distorcer o texto

PERGUNTAS-GERADAS:
perguntas que emergem do encontro entre texto e pesquisa

---
NOTAS:
{leitura_bruta}

---
MEMÓRIA CONCEITUAL:
{memoria_conceitual}
"""

PROMPT_CONSOLIDAR = """\
Você está consolidando uma ficha de leitura para Obsidian.

Texto: "{titulo}".

Use somente as notas abaixo, a estrutura do texto e as ressonâncias controladas.
Não use conhecimento externo.
Não transforme hipótese em certeza.
Não escreva em tom escolar.

A ficha deve preservar o eixo que parece emergir do texto, mas também suas dúvidas produtivas.
Termos técnicos, estrangeiros ou exógenos podem indicar zonas de alta centralidade.

Comece diretamente por **Tese central**.
Mantenha exatamente os títulos abaixo.
Toda afirmação substantiva precisa de referência [Parte N].
As relações com a pesquisa do leitor devem aparecer apenas em **Possíveis relações com minha pesquisa**.

**Tese central**
O argumento principal do texto, em um parágrafo, com referências [Parte N].

**Deslocamento teórico**
O que o texto torna difícil continuar pensando da mesma maneira, com referências [Parte N].

**Conceitos-chave do autor**
- conceito — explicação breve, com [Parte N]

**Operadores centrais**
- conceito — que relações organiza no texto, com [Parte N]

**Zonas de centralidade instável**
- conceito ou termo difícil — por que pode ser central, mesmo com baixa confiança, com [Parte N]

**Frases-charneira**
- frase curta ou paráfrase muito próxima — por que reorganiza o argumento, com [Parte N]

**Cadeia argumentativa**
- passo do argumento, com [Parte N]

**Tensões estruturantes**
- tensão — como estrutura o texto, com [Parte N]

**Exemplos empíricos**
- caso, imagem, tecnologia ou situação, com [Parte N]

**Autores e referências mobilizadas**
- autor ou obra, com [Parte N]

**Contribuição para o campo**
Um parágrafo sobre o que o texto permite compreender, sem extrapolar além das notas, com [Parte N].

**Possíveis relações com minha pesquisa**
- Hipótese: relação possível com matéria, cuidado, precariedade, reparo, manutenção, gesto, técnica ou infraestruturas, com [Parte N]
  GRAU-DE-CONFIANÇA: baixo / médio / alto

**Perguntas ao leitor**
- pergunta específica e situada

**Riscos de leitura superficial**
Como o texto poderia ser reduzido ou mal interpretado, com [Parte N].

**Evidências para conferência**
- afirmação verificável, com [Parte N]

**O que esta leitura pode ter distorcido**
Onde a ficha pode ter simplificado, deslocado ou perdido nuance em relação ao texto original.

PALAVRAS-CHAVE-FINAIS:
termos relevantes separados por vírgula. Exemplo: environment, Umwelt, feedback, collective behavior

---
NOTAS:
{leitura_bruta}

---
ESTRUTURA DO TEXTO:
{estrutura_texto}

---
RESSONÂNCIAS CONTROLADAS:
{ressonancias}

---
MEMÓRIA CONCEITUAL, USAR SOMENTE EM **Possíveis relações com minha pesquisa**:
{memoria_conceitual}

---
VIZINHANÇAS SEMÂNTICAS INTERNAS:
{vizinhancas_semanticas}
"""


GRUPO_MAX_PARTES = 3
MAX_UNIDADES_FINAIS = 3
MAX_RODADAS_SINTESE = 3


def chamar_ollama(prompt: str, modelo: str, tentativas: int = 3, num_predict: int = 1800, num_ctx: int = 8192) -> str:
    """Chama o Ollama com retry automático."""
    payload = {
        "model":  modelo,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": num_predict, "num_ctx": num_ctx},
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
    chunks: list[str],
    leituras: list[str],
    pasta_embeddings: Path,
    modelo_embedding: str,
) -> Path:
    """Gera embeddings em lote para chunks originais e leituras brutas."""
    pasta_embeddings.mkdir(parents=True, exist_ok=True)
    saida = pasta_embeddings / f"{doc['caminho'].stem}.jsonl"

    registros_sem_embedding = []
    textos = []
    data = datetime.now().strftime("%Y-%m-%d")
    for i, (chunk, leitura) in enumerate(zip(chunks, leituras), start=1):
        for tipo, texto in (("chunk-original", chunk), ("leitura-bruta", leitura)):
            texto_embedding = preparar_texto_embedding(texto)
            registros_sem_embedding.append({
                "arquivo": doc["caminho"].name,
                "titulo": doc["titulo"],
                "parte": i,
                "tipo": tipo,
                "modelo_embedding": modelo_embedding,
                "data_indexacao": data,
                "texto": texto_embedding,
                "texto_hash": hashlib.md5(texto.encode()).hexdigest(),
                "texto_embedding_hash": hashlib.md5(texto_embedding.encode()).hexdigest(),
                "texto_embedding_chars": len(texto_embedding),
                "texto_truncado_para_embedding": len(texto_embedding) < len(texto.strip()),
                "palavras": len(texto.split()),
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
    obrigatorios = [
        "**Tese central**",
        "**Deslocamento teórico**",
        "**Conceitos-chave do autor**",
        "**Operadores centrais**",
        "**Frases-charneira**",
        "**Zonas de centralidade instável**",
        "**Cadeia argumentativa**",
        "**Tensões estruturantes**",
        "**Possíveis relações com minha pesquisa**",
        "**Perguntas ao leitor**",
        "**Evidências para conferência**",
    ]
    if any(titulo not in ficha for titulo in obrigatorios):
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
    memoria: dict | None = None,
    nome_arquivo: str = "",
    modelo_embedding: str = "",
) -> tuple[str, list[str]]:
    """Consolida a leitura bruta de um documento numa ficha interpretativa."""
    if len(leituras) <= MAX_UNIDADES_FINAIS:
        texto_leitura_bruta = "\n\n---\n\n".join(
            [f"[Parte {i+1}]\n{r}" for i, r in enumerate(leituras)]
        )
    else:
        texto_leitura_bruta = reduzir_leituras_em_rodadas(
            titulo=titulo,
            leituras=leituras,
            modelo=modelo,
            nome_arquivo=nome_arquivo,
        )

    try:
        vizinhancas_semanticas = gerar_vizinhancas_semanticas(
            titulo=titulo,
            leituras=leituras,
            modelo_embedding=modelo_embedding,
            top_k=2,
        )
    except Exception as e:
        vizinhancas_semanticas = f"não gerado: {e}"

    memoria_conceitual = formatar_memoria_para_prompt(
        memoria or carregar_memoria(MEMORIA_PADRAO),
        limite_conceitos=6,
    )
    estrutura_texto = gerar_estrutura_texto(
        titulo=titulo,
        texto_leitura_bruta=texto_leitura_bruta,
        modelo=modelo,
        nome_arquivo=nome_arquivo,
    )
    ressonancias = gerar_ressonancias_controladas(
        titulo=titulo,
        texto_leitura_bruta=texto_leitura_bruta,
        memoria_conceitual=memoria_conceitual,
        modelo=modelo,
        nome_arquivo=nome_arquivo,
    )
    prompt = PROMPT_CONSOLIDAR.format(
        titulo=titulo,
        memoria_conceitual=memoria_conceitual,
        leitura_bruta=texto_leitura_bruta,
        estrutura_texto=estrutura_texto,
        ressonancias=ressonancias,
        vizinhancas_semanticas=vizinhancas_semanticas,
    )
    resposta = chamar_ollama(prompt, modelo, num_predict=5000, num_ctx=16384)
    ficha, palavras_chave = separar_ficha_e_palavras_chave(resposta)

    if not ficha_valida(ficha, total_partes=len(leituras)):
        prompt_retry = (
            "A resposta anterior não seguiu o molde. Refaça do zero.\n"
            "Comece obrigatoriamente com **Problema principal** e use todos os títulos pedidos.\n"
            "Não escreva Resumo, Análise, Conclusão, Parte 1, Parte 2 ou títulos com ###.\n\n"
            "Para este texto longo, cite explicitamente partes do início, do meio e do fim.\n"
            "Não concentre a ficha em apenas uma ou duas partes.\n\n"
            + prompt
        )
        resposta = chamar_ollama(prompt_retry, modelo, num_predict=5000, num_ctx=16384)
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
    memoria_conceitual: str,
    modelo: str,
    nome_arquivo: str = "",
) -> str:
    """Gera a camada de ressonâncias sem misturar inferência com tese do texto."""
    prompt = PROMPT_RESSONANCIAS.format(
        titulo=titulo,
        leitura_bruta=texto_leitura_bruta,
        memoria_conceitual=memoria_conceitual,
    )
    chave = hash_chunk(f"ressonancias\n{texto_leitura_bruta}\n{memoria_conceitual}", modelo, prompt)
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
                         memoria_path: Path, modelo: str,
                         modelo_consolidacao: str = "",
                         pasta_embeddings: Path | None = None,
                         modelo_embedding: str = "",
                         gerar_embeddings: bool = True,
                         modo: str = "completo",
                         forcar: bool = False) -> dict:
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
            chunks = quebrar_em_chunks(doc["corpo"])
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

        if modo in {"indexar", "completo"} and gerar_embeddings and modelo_embedding and pasta_embeddings is not None:
            embeddings_path = gerar_embeddings_documento(
                doc=doc,
                chunks=chunks,
                leituras=leituras,
                pasta_embeddings=pasta_embeddings,
                modelo_embedding=modelo_embedding,
            )
            print(f"    -> Embeddings: {embeddings_path}")

        if modo == "indexar":
            memoria = carregar_memoria(memoria_path)
            memoria = atualizar_memoria(memoria, doc, todas_kw, leituras)
            salvar_memoria(memoria_path, memoria)
            return {"arquivo": caminho.name, "status": "ok (indexado)", "chunks": len(chunks)}

        memoria = carregar_memoria(memoria_path)

        # Consolida sempre, mesmo em documentos curtos, para manter formato único.
        ficha_texto, kw_final = consolidar_resumos(
            titulo_prompt,
            leituras,
            modelo_consolidacao or modelo,
            memoria,
            nome_cache,
            modelo_embedding if gerar_embeddings else "",
        )

        todas_kw = list(dict.fromkeys(todas_kw + kw_final))[:12]
        conteudo = gerar_ficha_obsidian(doc, ficha_texto, todas_kw, len(leituras), chunk_words)
        saida_path.write_text(conteudo, encoding="utf-8")
        memoria = atualizar_memoria(memoria, doc, todas_kw, leituras)
        salvar_memoria(memoria_path, memoria)

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
    parser.add_argument("--memoria",  default=str(MEMORIA_PADRAO), help="Arquivo JSON de memória conceitual")
    parser.add_argument("--modelo",   default="qwen2.5:7b", help="Modelo Ollama")
    parser.add_argument("--modelo-consolidacao", default="", help="Modelo Ollama opcional para resumos intermediários e ficha final")
    parser.add_argument("--modelo-embedding", default="nomic-embed-text", help="Modelo Ollama para embeddings em lote")
    parser.add_argument("--embeddings", default=str(EMBEDDINGS_DIR_PADRAO), help="Pasta de saída dos embeddings JSONL")
    parser.add_argument("--sem-embeddings", action="store_true", help="Não gera embeddings antes da consolidação")
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
    memoria_path = Path(args.memoria)
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
    print(f"⚙️  Modo: {args.modo}")
    print(f"📝 Leituras: {pasta_leituras} | Fichas: {pasta_saida}\n")
    if not args.sem_embeddings and args.modo != "fichar":
        print(f"🧭 Embeddings: {pasta_embeddings}\n")
    print(f"🧠 Memória conceitual: {memoria_path}\n")

    resultados = []

    # Workers > 1 só faz sentido se tiveres GPU ou muita RAM
    if args.workers == 1:
        for arq in tqdm(arquivos, desc="Processando", unit="doc"):
            r = processar_documento(
                arq,
                pasta_saida,
                pasta_leituras,
                memoria_path,
                args.modelo,
                args.modelo_consolidacao,
                pasta_embeddings,
                args.modelo_embedding,
                not args.sem_embeddings,
                args.modo,
                args.forcar,
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
                    memoria_path,
                    args.modelo,
                    args.modelo_consolidacao,
                    pasta_embeddings,
                    args.modelo_embedding,
                    not args.sem_embeddings,
                    args.modo,
                    args.forcar,
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
    print(f"🧠 Memória conceitual: {memoria_path.resolve()}")

    if erros:
        print("\nArquivos com erro:")
        for r in resultados:
            if "ERRO" in r["status"]:
                print(f"  • {r['arquivo']}: {r['status']}")


if __name__ == "__main__":
    main()