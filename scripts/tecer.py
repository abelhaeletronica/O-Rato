

#!/usr/bin/env python3
"""
Tecer relações semânticas na biblioteca.

Consulta embeddings já gerados por `roer.py`, recupera trechos semanticamente
próximos e pede a um modelo local via Ollama para tecer relações.

Uso básico:
    python rato/scripts/tecer.py \
        --consulta "cuidado como manutenção de relações materiais" \
        --embeddings .embeddings \
        --saida relacoes \
        --modelo-embedding nomic-embed-text \
        --modelo qwen3:8b
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

OLLAMA_URL       = "http://localhost:11434/api/generate"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"

PROMPT_TECER = """\
Você recebeu trechos recuperados por proximidade semântica em uma biblioteca de pesquisa.

Consulta do leitor:
"{consulta}"

Sua tarefa NÃO é assumir que os trechos dizem a mesma coisa.
Sua tarefa é comparar cuidadosamente os trechos e avaliar se a proximidade semântica é conceitualmente produtiva.

Regras:
- Diferencie relação textual de inferência do leitor.
- Não atribua a um autor conceitos que aparecem apenas em outro trecho.
- Não force equivalências.
- Se a relação for fraca ou acidental, diga claramente.
- Use os identificadores [Trecho N] para sustentar suas afirmações.
- Prefira formular ressonâncias parciais, tensões e deslocamentos em vez de sínteses totalizantes.

Formato obrigatório:

# Relação entre textos

## Consulta
{consulta}

## Eixo de ressonância
<qual problema, gesto ou campo comum parece aproximar os trechos; cite [Trecho N]>

## Conceitos compartilhados
- <conceito> — <como aparece nos trechos> [Trecho N]

## Ressonâncias parciais
- <conceito/imagem/processo A> ↔ <conceito/imagem/processo B> — <por que ressoam sem serem idênticos> [Trecho N]

## Tensões e diferenças
- <diferença importante entre os trechos> [Trecho N]

## Deslocamentos possíveis para a pesquisa
- Hipótese: <aproximação possível para a pesquisa do leitor>
  Origem: inferência do leitor
  Grau de confiança: <baixo/médio/alto>

## Risco de forçar a relação
<onde a comparação pode exagerar, apagar diferenças ou criar falsa equivalência>

## Perguntas geradas
- <pergunta útil para continuar a pesquisa>

## Trechos mais relevantes
- [Trecho N] <arquivo ou título> — similaridade: <valor>

---
TRECHOS RECUPERADOS:
{trechos}
"""


@dataclass
class Resultado:
    score: float
    arquivo_jsonl: Path
    linha: int
    item: dict[str, Any]


def chamar_ollama_generate(prompt: str, modelo: str, num_predict: int = 2200) -> str:
    payload = {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": num_predict, "num_ctx": 16384},
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=180)
    r.raise_for_status()
    return r.json().get("response", "").strip()


def gerar_embedding(texto: str, modelo_embedding: str) -> list[float]:
    r = requests.post(OLLAMA_EMBED_URL, json={"model": modelo_embedding, "prompt": texto}, timeout=60)
    r.raise_for_status()
    emb = r.json().get("embedding")
    if not isinstance(emb, list):
        raise RuntimeError("Resposta de embedding inválida: campo 'embedding' ausente.")
    return [float(x) for x in emb]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return -1.0
    return dot / (norm_a * norm_b)


def encontrar_arquivos_jsonl(pasta: Path) -> list[Path]:
    if pasta.is_file() and pasta.suffix == ".jsonl":
        return [pasta]
    return sorted(pasta.rglob("*.jsonl"))


def extrair_embedding(item: dict[str, Any]) -> list[float] | None:
    for chave in ("embedding", "vetor", "vector"):
        valor = item.get(chave)
        if isinstance(valor, list) and valor and all(isinstance(x, int | float) for x in valor):
            return [float(x) for x in valor]
    return None


def texto_do_item(item: dict[str, Any]) -> str:
    for chave in ("texto", "text", "chunk", "conteudo", "content", "trecho"):
        valor = item.get(chave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return ""


def titulo_do_item(item: dict[str, Any], fallback: str) -> str:
    for chave in ("titulo", "title", "arquivo", "file", "source", "documento"):
        valor = item.get(chave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return fallback


def metadados_resumidos(item: dict[str, Any]) -> str:
    partes = []
    for chave in ("titulo", "arquivo", "documento", "chunk_id", "parte", "pagina", "autor", "year"):
        valor = item.get(chave)
        if valor not in (None, "", []):
            partes.append(f"{chave}: {valor}")
    return "; ".join(partes)


def buscar_vizinhos(
    consulta_embedding: list[float],
    pasta_embeddings: Path,
    top_k: int,
    minimo: float,
) -> list[Resultado]:
    resultados: list[Resultado] = []
    arquivos = encontrar_arquivos_jsonl(pasta_embeddings)
    if not arquivos:
        raise FileNotFoundError(f"Nenhum .jsonl encontrado em: {pasta_embeddings}")

    for arquivo in arquivos:
        with arquivo.open("r", encoding="utf-8") as f:
            for idx, linha in enumerate(f, start=1):
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    item = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                emb = extrair_embedding(item)
                if emb is None:
                    continue
                score = cosine_similarity(consulta_embedding, emb)
                if score >= minimo:
                    resultados.append(Resultado(score=score, arquivo_jsonl=arquivo, linha=idx, item=item))

    resultados.sort(key=lambda r: r.score, reverse=True)
    return resultados[:top_k]


def limitar_texto(texto: str, max_chars: int) -> str:
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) <= max_chars:
        return texto
    return texto[: max_chars - 1].rstrip() + "…"


def formatar_trechos(resultados: list[Resultado], max_chars_por_trecho: int) -> str:
    blocos = []
    for i, resultado in enumerate(resultados, start=1):
        item = resultado.item
        texto = texto_do_item(item)
        titulo = titulo_do_item(item, resultado.arquivo_jsonl.name)
        meta = metadados_resumidos(item)
        trecho = limitar_texto(texto, max_chars_por_trecho)
        blocos.append(
            f"[Trecho {i}]\n"
            f"Título/arquivo: {titulo}\n"
            f"Arquivo JSONL: {resultado.arquivo_jsonl}\n"
            f"Linha JSONL: {resultado.linha}\n"
            f"Similaridade: {resultado.score:.4f}\n"
            f"Metadados: {meta or 'não informado'}\n"
            f"Texto: {trecho}"
        )
    return "\n\n---\n\n".join(blocos)


def slugify(texto: str, limite: int = 80) -> str:
    texto = texto.lower().strip()
    texto = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE)
    texto = re.sub(r"\s+", "-", texto)
    texto = re.sub(r"-+", "-", texto).strip("-")
    return texto[:limite].strip("-") or "consulta"


def salvar_relacao(saida: Path, consulta: str, conteudo: str) -> Path:
    saida.mkdir(parents=True, exist_ok=True)
    data = datetime.now().strftime("%Y%m%d-%H%M")
    nome = f"RELACAO_{data}_{slugify(consulta)}.md"
    caminho = saida / nome
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tece relações entre textos a partir de embeddings locais e gera análise com Ollama."
    )
    parser.add_argument("--consulta", required=True, help="Consulta conceitual em linguagem natural.")
    parser.add_argument("--embeddings", default=".embeddings", help="Pasta ou arquivo .jsonl com embeddings.")
    parser.add_argument("--saida", default="relacoes", help="Pasta onde salvar a análise em Markdown.")
    parser.add_argument("--modelo-embedding", default="nomic-embed-text", help="Modelo de embedding do Ollama.")
    parser.add_argument("--modelo", default="qwen3:8b", help="Modelo gerador do Ollama.")
    parser.add_argument("--top-k", type=int, default=8, help="Número de vizinhos recuperados.")
    parser.add_argument("--minimo", type=float, default=-1.0, help="Similaridade mínima para aceitar resultado.")
    parser.add_argument("--max-chars", type=int, default=1800, help="Máximo de caracteres por trecho enviado ao LLM.")
    parser.add_argument("--somente-busca", action="store_true", help="Não chama LLM; apenas imprime os vizinhos.")
    args = parser.parse_args()

    pasta_embeddings = Path(args.embeddings).expanduser().resolve()
    saida = Path(args.saida).expanduser().resolve()

    consulta_embedding = gerar_embedding(args.consulta, args.modelo_embedding)
    resultados = buscar_vizinhos(
        consulta_embedding=consulta_embedding,
        pasta_embeddings=pasta_embeddings,
        top_k=args.top_k,
        minimo=args.minimo,
    )

    if not resultados:
        print("Nenhum resultado encontrado.")
        return

    trechos = formatar_trechos(resultados, args.max_chars)

    if args.somente_busca:
        print(trechos)
        return

    prompt = PROMPT_TECER.format(
        consulta=args.consulta,
        trechos=trechos,
    )
    analise = chamar_ollama_generate(prompt, args.modelo)
    caminho = salvar_relacao(saida, args.consulta, analise)

    print(f"Relação salva em: {caminho}")
    print("\nTop resultados:")
    for i, resultado in enumerate(resultados, start=1):
        titulo = titulo_do_item(resultado.item, resultado.arquivo_jsonl.name)
        print(f"{i}. {resultado.score:.4f} — {titulo}")


if __name__ == "__main__":
    main()
