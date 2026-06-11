#!/usr/bin/env python3
"""
digerir.py — Modo Digestão do Ecossistema Rato
Identifica tensões, incômodos, contradições e perguntas persistentes.
Recusa consenso. Abraça opacidade.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
import requests

# Configuração de caminhos
REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "rato" / "biblioteca_referencias.sqlite"
FICHAS_DIR = REPO_ROOT / "fichas"
DIGESTOES_DIR = FICHAS_DIR / "digestoes"

# Configuração Ollama
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:14b"


def carregar_arquivos(caminhos: list[str]) -> str:
    """
    Modo Fricção Consciente: Lê arquivos .md e remove frontmatter.
    """
    recortes = []
    for caminho_str in caminhos:
        caminho = Path(caminho_str)
        if not caminho.exists():
            print(f"⚠ Aviso: arquivo não encontrado: {caminho}", file=sys.stderr)
            continue

        try:
            conteudo = caminho.read_text(encoding="utf-8")
            # Remove frontmatter (tudo entre --- iniciais)
            linhas = conteudo.split("\n")
            if linhas[0].strip() == "---":
                # Encontra o segundo ---
                for i, linha in enumerate(linhas[1:], start=1):
                    if linha.strip() == "---":
                        conteudo = "\n".join(linhas[i + 1 :])
                        break
            recortes.append(f"## {caminho.name}\n\n{conteudo}")
        except Exception as e:
            print(f"✗ Erro ao ler {caminho}: {e}", file=sys.stderr)
            continue

    return "\n\n---\n\n".join(recortes) if recortes else ""


def extrair_temas_persistentes(
    dias: int,
) -> str:
    """
    Busca documentos com `atualizado_em` nos últimos N dias.
    Extrai tags/palavras-chave de meta_json.
    Retorna termos que aparecem mais de uma vez.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Data limite
        data_limite = (datetime.now() - timedelta(days=dias)).isoformat()

        # Buscar documentos recentes
        cursor.execute(
            """
            SELECT meta_json FROM documentos
            WHERE atualizado_em >= ?
            ORDER BY atualizado_em DESC
            """,
            (data_limite,),
        )

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "Nenhum documento atualizado nos últimos {} dias.".format(dias)

        # Extrair e contar termos
        contador_termos: dict[str, int] = {}
        for (meta_json_str,) in rows:
            try:
                meta = json.loads(meta_json_str)
                tags = meta.get("tags", []) or []
                palavras_chave = meta.get("palavras-chave", []) or []

                termos = tags + palavras_chave
                for termo in termos:
                    contador_termos[termo] = contador_termos.get(termo, 0) + 1
            except json.JSONDecodeError:
                continue

        # Isolar termos recorrentes (> 1 ocorrência)
        temas_recorrentes = {
            k: v for k, v in contador_termos.items() if v > 1
        }

        if not temas_recorrentes:
            return "Nenhuma recorrência observada nos últimos {} dias.".format(dias)

        # Formatação
        linhas = []
        for tema, count in sorted(
            temas_recorrentes.items(), key=lambda x: x[1], reverse=True
        ):
            linhas.append(f"- **{tema}** ({count}x)")

        return "\n".join(linhas)

    except sqlite3.Error as e:
        return f"Erro ao consultar banco: {e}"


def extrair_perguntas_orfas() -> str:
    """
    Busca chunks que contenham '?' e palavras-chave
    como "pergunta", "aberta", "emergente" ou linhas com '-'.
    Limita a 15 resultados.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Padrões de busca
        cursor.execute(
            """
            SELECT texto FROM chunks
            WHERE texto LIKE '%?%'
               OR texto LIKE '%pergunta%'
               OR texto LIKE '%aberta%'
               OR texto LIKE '%emergente%'
            LIMIT 15
            """
        )

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "Nenhuma pergunta órfã detectada."

        # Extrair trechos significativos
        trechos = []
        for (texto,) in rows:
            # Limita a 200 caracteres por trecho
            trecho = texto.strip()[:200]
            if trecho:
                trechos.append(f"> {trecho}")

        return "\n".join(trechos)

    except sqlite3.Error as e:
        return f"Erro ao consultar perguntas: {e}"


def extrair_recortes_ponto_cego(dias: int) -> str:
    """
    Modo Ponto Cego: Busca os 4 chunks mais recentes
    (ordenados por atualizado_em de seus documentos).
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Data limite
        data_limite = (datetime.now() - timedelta(days=dias)).isoformat()

        cursor.execute(
            """
            SELECT c.texto, d.titulo, d.atualizado_em
            FROM chunks c
            JOIN documentos d ON c.documento_id = d.id
            WHERE d.atualizado_em >= ?
            ORDER BY d.atualizado_em DESC
            LIMIT 4
            """,
            (data_limite,),
        )

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "Nenhum chunk recente encontrado."

        # Formatação
        recortes = []
        for texto, titulo, data in rows:
            trecho = texto.strip()[:400]  # Limita a 400 caracteres
            recortes.append(f"**{titulo}** ({data})\n\n{trecho}")

        return "\n\n---\n\n".join(recortes)

    except sqlite3.Error as e:
        return f"Erro ao extrair recortes: {e}"


def gerar_prompt_digestao(
    temas_persistentes: str,
    perguntas_orfas: str,
    recortes_texto: str,
) -> str:
    """
    Monta o prompt denso para o Ollama em modo Digestão.
    """
    return f"""Você é o Rato, operando no modo DIGESTÃO. 
Sua função não é resumir, pacificar ou clarear. Sua bússola é a incompreensão e a opacidade.
Você deve analisar a constelação de rastros fornecida sob o princípio da precariedade epistemológica: a dúvida, a falha e o mal-estar são os dados mais valiosos.

DIRETRIZ METODOLÓGICA ABSOLUTA: Não procurar consenso. Procurar tensões.

---
SINTOMAS DETECTADOS NO BANCO DE DADOS:
Temas que insistem em retornar na pesquisa:
{temas_persistentes}

Perguntas que reapareceram ou ficaram em aberto nas fichas anteriores:
{perguntas_orfas}
---

RASTROS SELECIONADOS PARA ESTA SESSÃO DE FRICÇÃO:
{recortes_texto}

Gere o relatório de Digestão estruturado exatamente assim:

## 1. O Mapa dos Tropeços (Opacidade como Método)
Não diga o que compreendeu. Onde o texto escapou à lógica formal? Indique quais metáforas resistiram à interpretação e quais trechos permaneceram obscuros ou opacos ao processamento. Aponte onde você, como modelo, "tropeçou" ou encontrou limites intransponíveis de leitura.

## 2. Contextos Incompatíveis e Atritos
- Que conceito aparece aqui em contextos incompatíveis ou que se repelem?
- Que autor desorganiza, tensiona ou contradiz outro autor frequentemente associado nesta pesquisa?

## 3. Perguntas Insistentes (Inquietação Organizada)
Aponte qual pergunta ou angústia conceitual insiste em reaparecer sob nomes diferentes ao longo das fichas. O que continua sem solução?

## 4. Hipóteses em Colapso
Que hipótese ou certeza do pesquisador (sobre o reparo, a matéria, as vivências subjetivas) está sendo silenciosamente contradita ou fraturada pelos próprios textos e pela contingência da matéria aqui reunidos?"""


def chamar_ollama(prompt: str) -> str | None:
    """
    Faz requisição POST síncrona para Ollama.
    stream: False para resposta completa.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    try:
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=300)
        response.raise_for_status()

        data = response.json()
        return data.get("response", "")

    except requests.exceptions.ConnectionError:
        print(
            "✗ Erro: Não conseguiu conectar ao Ollama em localhost:11434",
            file=sys.stderr,
        )
        return None
    except requests.exceptions.Timeout:
        print("✗ Erro: Requisição ao Ollama expirou (timeout)", file=sys.stderr)
        return None
    except requests.exceptions.RequestException as e:
        print(f"✗ Erro na requisição ao Ollama: {e}", file=sys.stderr)
        return None


def salvar_digestao(conteudo: str) -> Path:
    """
    Salva a digestão em fichas/digestoes/digestao_AAAA-MM-DD.md
    com frontmatter apropriado.
    """
    DIGESTOES_DIR.mkdir(parents=True, exist_ok=True)

    data_hoje = datetime.now().strftime("%Y-%m-%d")
    arquivo = DIGESTOES_DIR / f"digestao_{data_hoje}.md"

    frontmatter = f"""---
titulo: "Digestão: {data_hoje}"
tipo: digestao
data: "{data_hoje}"
modelo: {OLLAMA_MODEL}
---

"""

    arquivo.write_text(frontmatter + conteudo, encoding="utf-8")
    return arquivo


def main() -> None:
    """
    Orquestra o fluxo completo de digestão.
    """
    parser = argparse.ArgumentParser(
        description="Rato em modo Digestão: identifica tensões e opacidades."
    )
    parser.add_argument(
        "--arquivos",
        nargs="+",
        default=[],
        help="Caminhos de arquivos .md (Modo Fricção Consciente)",
    )
    parser.add_argument(
        "--dias",
        type=int,
        default=30,
        help="Dias retroativos para buscar (padrão: 30)",
    )
    parser.add_argument(
        "--salvar",
        action="store_true",
        help="Salvar resultado em fichas/digestoes/",
    )

    args = parser.parse_args()

    print("🐭 Rato em Modo Digestão...", file=sys.stderr)

    # Extrai temas e perguntas (sempre)
    print("   [1/4] Minerando temas persistentes...", file=sys.stderr)
    temas_persistentes = extrair_temas_persistentes(args.dias)

    print("   [2/4] Colhendo perguntas órfãs...", file=sys.stderr)
    perguntas_orfas = extrair_perguntas_orfas()

    # Seleciona recortes de texto
    if args.arquivos:
        print("   [3/4] Fricção Consciente: carregando arquivos...", file=sys.stderr)
        recortes_texto = carregar_arquivos(args.arquivos)
    else:
        print("   [3/4] Ponto Cego: extraindo chunks recentes...", file=sys.stderr)
        recortes_texto = extrair_recortes_ponto_cego(args.dias)

    # Monta prompt
    prompt = gerar_prompt_digestao(temas_persistentes, perguntas_orfas, recortes_texto)

    # Chama Ollama
    print("   [4/4] Consultando Ollama qwen3:14b...", file=sys.stderr)
    digestao = chamar_ollama(prompt)

    if digestao is None:
        sys.exit(1)

    # Output e salvamento
    if args.salvar:
        arquivo = salvar_digestao(digestao)
        print(f"\n✓ Digestão salva em: {arquivo}", file=sys.stderr)
    else:
        # Print com delimitadores
        delim = "═" * 80
        print(f"\n{delim}")
        print(digestao)
        print(f"{delim}\n")


if __name__ == "__main__":
    main()
