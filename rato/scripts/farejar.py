#!/usr/bin/env python3
"""
Farejar rastros na biblioteca local
===================================

Uso básico:
    python3 rato/scripts/farejar.py indexar --pasta .
    python3 rato/scripts/farejar.py farejar "reparo manutenção cuidado" --busca conceitual
    python3 rato/scripts/farejar.py farejar "argila cuidado" --busca palavras
    python3 rato/scripts/farejar.py farejar "gesto material" --busca hibrida
    python3 rato/scripts/farejar.py farejar
    python3 rato/scripts/farejar.py aprender "Jackson: tratar repair como cuidado material, não só sustentabilidade"

Dependências:
    pip install requests pyyaml

Busca conceitual usa embeddings em .embeddings/*.jsonl.
Busca por palavras usa SQLite FTS em rato/biblioteca_referencias.sqlite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml


DB_PADRAO = Path("rato/biblioteca_referencias.sqlite")
EMBEDDINGS_DIR_PADRAO = Path(".embeddings")
MODELO_EMBEDDING_PADRAO = "bge-m3"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
OLLAMA_EMBED_LEGACY_URL = "http://localhost:11434/api/embeddings"
OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
CHUNK_MAX_WORDS = 650
CHUNK_OVERLAP = 80

PROMPT_AVALIAR_RELACOES = """\
Você recebeu trechos recuperados por proximidade semântica em uma biblioteca de pesquisa.
Responda diretamente em Markdown. Não inclua cadeia de pensamento.

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

# Avaliando relações

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
class Documento:
    caminho: Path
    titulo: str
    tipo: str
    corpo: str
    meta: dict


def conectar(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    criar_schema(conn)
    return conn


def criar_schema(conn: sqlite3.Connection) -> None:
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

        CREATE TABLE IF NOT EXISTS aprendizados (
            id INTEGER PRIMARY KEY,
            texto TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            criado_em TEXT NOT NULL
        );
        """
    )


def extrair_frontmatter(texto: str) -> tuple[dict, str]:
    if texto.startswith("---"):
        partes = texto.split("---", 2)
        if len(partes) >= 3:
            try:
                return yaml.safe_load(partes[1]) or {}, partes[2].strip()
            except yaml.YAMLError:
                pass
    return {}, texto.strip()


def extrair_titulo(corpo: str, caminho: Path, meta: dict) -> str:
    for campo in ("titulo", "title"):
        if meta.get(campo):
            return str(meta[campo]).strip()
    for linha in corpo.splitlines():
        linha = linha.strip()
        if linha.startswith("# "):
            return linha[2:].strip()
    return caminho.stem.replace("-", " ").replace("_", " ").title()


def ler_documento(caminho: Path) -> Documento:
    texto = caminho.read_text(encoding="utf-8", errors="replace")
    meta, corpo = extrair_frontmatter(texto)
    tipo = "ficha" if caminho.name.startswith("FICHA_") or caminho.parent.name == "fichas" else "referencia"
    titulo = extrair_titulo(corpo, caminho, meta)
    return Documento(caminho=caminho, titulo=titulo, tipo=tipo, corpo=corpo, meta=meta)


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def quebrar_em_chunks(texto: str, max_palavras: int = CHUNK_MAX_WORDS) -> list[str]:
    paragrafos = [p.strip() for p in re.split(r"\n{2,}", texto) if p.strip()]
    chunks: list[str] = []
    atual: list[str] = []
    palavras_atual = 0

    for paragrafo in paragrafos:
        palavras = len(paragrafo.split())
        if palavras_atual + palavras > max_palavras and atual:
            chunks.append("\n\n".join(atual))
            overlap = " ".join(" ".join(atual).split()[-CHUNK_OVERLAP:])
            atual = [overlap] if overlap else []
            palavras_atual = len(overlap.split())
        atual.append(paragrafo)
        palavras_atual += palavras

    if atual:
        chunks.append("\n\n".join(atual))
    return chunks or [texto]


def embedding_ollama(texto: str, modelo: str) -> list[float] | None:
    try:
        resp = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": modelo, "input": texto},
            timeout=600,
        )
        resp.raise_for_status()
        dados = resp.json()
        embeddings = dados.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            vetor = embeddings[0]
        else:
            vetor = dados.get("embedding")
        return vetor if isinstance(vetor, list) else None
    except requests.RequestException:
        try:
            resp = requests.post(
                OLLAMA_EMBED_LEGACY_URL,
                json={"model": modelo, "prompt": texto},
                timeout=600,
            )
            resp.raise_for_status()
            vetor = resp.json().get("embedding")
            return vetor if isinstance(vetor, list) else None
        except requests.RequestException:
            return None


def inserir_documento(conn: sqlite3.Connection, doc: Documento, modelo_embedding: str | None) -> str:
    doc_hash = hash_texto(doc.corpo)
    existente = conn.execute(
        "SELECT id, hash FROM documentos WHERE caminho = ?",
        (str(doc.caminho),),
    ).fetchone()
    if existente and existente["hash"] == doc_hash:
        return "pulado"

    if existente:
        documento_id = existente["id"]
        conn.execute("DELETE FROM chunks WHERE documento_id = ?", (documento_id,))
        conn.execute("DELETE FROM chunks_fts WHERE caminho = ?", (str(doc.caminho),))
        conn.execute(
            """
            UPDATE documentos
               SET titulo = ?, tipo = ?, hash = ?, palavras = ?, meta_json = ?, atualizado_em = ?
             WHERE id = ?
            """,
            (
                doc.titulo,
                doc.tipo,
                doc_hash,
                len(doc.corpo.split()),
                json.dumps(doc.meta, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
                documento_id,
            ),
        )
        status = "atualizado"
    else:
        cur = conn.execute(
            """
            INSERT INTO documentos (caminho, titulo, tipo, hash, palavras, meta_json, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(doc.caminho),
                doc.titulo,
                doc.tipo,
                doc_hash,
                len(doc.corpo.split()),
                json.dumps(doc.meta, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        documento_id = cur.lastrowid
        status = "indexado"

    for parte, chunk in enumerate(quebrar_em_chunks(doc.corpo), start=1):
        embedding = embedding_ollama(chunk[:6000], modelo_embedding) if modelo_embedding else None
        conn.execute(
            """
            INSERT INTO chunks (documento_id, parte, texto, hash, embedding_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                documento_id,
                parte,
                chunk,
                hash_texto(chunk),
                json.dumps(embedding) if embedding else None,
            ),
        )
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO chunks_fts(rowid, titulo, caminho, tipo, texto) VALUES (?, ?, ?, ?, ?)",
            (rowid, doc.titulo, str(doc.caminho), doc.tipo, chunk),
        )

    return status


def arquivos_markdown(pasta: Path) -> list[Path]:
    ignorar = {".cache_indexador", "__pycache__"}
    arquivos = []
    for caminho in pasta.rglob("*.md"):
        if any(parte in ignorar for parte in caminho.parts):
            continue
        arquivos.append(caminho)
    return sorted(arquivos)


def cmd_indexar(args: argparse.Namespace) -> None:
    conn = conectar(args.db)
    arquivos = arquivos_markdown(args.pasta)
    contagem = {"indexado": 0, "atualizado": 0, "pulado": 0, "erro": 0}

    for caminho in arquivos:
        try:
            status = inserir_documento(conn, ler_documento(caminho), args.embedding)
            contagem[status] += 1
        except Exception as exc:
            contagem["erro"] += 1
            print(f"ERRO {caminho}: {exc}")

    conn.commit()
    print(
        f"Indexacao concluida: {contagem['indexado']} novos, "
        f"{contagem['atualizado']} atualizados, {contagem['pulado']} pulados, "
        f"{contagem['erro']} erros."
    )
    if args.embedding:
        print(f"Embeddings: {args.embedding}")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def consulta_fts(consulta: str) -> str:
    termos = re.findall(r"[\w-]{3,}", consulta.lower(), flags=re.UNICODE)
    if not termos:
        return consulta
    return " OR ".join(f'"{termo}"' for termo in termos[:12])


def buscar_textual(conn: sqlite3.Connection, consulta: str, limite: int) -> list[sqlite3.Row]:
    try:
        resultados = conn.execute(
            """
            SELECT c.id, c.parte, d.titulo, d.caminho, d.tipo, c.texto,
                   bm25(chunks_fts) AS score
              FROM chunks_fts
              JOIN chunks c ON c.id = chunks_fts.rowid
              JOIN documentos d ON d.caminho = chunks_fts.caminho
             WHERE chunks_fts MATCH ?
             ORDER BY score
             LIMIT ?
            """,
            (consulta_fts(consulta), limite),
        ).fetchall()
        if resultados:
            return resultados
    except sqlite3.OperationalError:
        pass

    termos = re.findall(r"[\w-]{3,}", consulta.lower(), flags=re.UNICODE)
    if not termos:
        return []
    termos = termos[:8]
    where = " OR ".join(["lower(c.texto) LIKE ?"] * len(termos))
    params = [f"%{termo}%" for termo in termos]
    return conn.execute(
        f"""
        SELECT c.id, c.parte, d.titulo, d.caminho, d.tipo, c.texto,
               0.0 AS score
          FROM chunks c
          JOIN documentos d ON d.id = c.documento_id
         WHERE {where}
         LIMIT ?
        """,
        (*params, limite),
    ).fetchall()


def buscar_semantica(conn: sqlite3.Connection, consulta: str, modelo: str, limite: int) -> list[dict]:
    vetor = embedding_ollama(consulta, modelo)
    if not vetor:
        return []
    linhas = conn.execute(
        """
        SELECT c.id, c.parte, c.texto, c.embedding_json, d.titulo, d.caminho, d.tipo
          FROM chunks c
          JOIN documentos d ON d.id = c.documento_id
         WHERE c.embedding_json IS NOT NULL
        """
    ).fetchall()
    resultados = []
    for linha in linhas:
        score = cosine(vetor, json.loads(linha["embedding_json"]))
        resultados.append({**dict(linha), "score": score})
    return sorted(resultados, key=lambda r: r["score"], reverse=True)[:limite]


# ==== Funções para .embeddings/*.jsonl ====

def arquivos_jsonl(embeddings_dir: Path) -> list[Path]:
    if embeddings_dir.is_file() and embeddings_dir.suffix == ".jsonl":
        return [embeddings_dir]
    if embeddings_dir.is_dir():
        return sorted(embeddings_dir.rglob("*.jsonl"))
    return []


def carregar_chunks_jsonl(embeddings_dir: Path) -> list[dict]:
    """Carrega chunks previamente indexados em .embeddings/*.jsonl."""
    if not embeddings_dir.exists():
        return []

    chunks: list[dict] = []
    for caminho_jsonl in arquivos_jsonl(embeddings_dir):
        try:
            with caminho_jsonl.open("r", encoding="utf-8") as f:
                for numero_linha, linha in enumerate(f, start=1):
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        item = json.loads(linha)
                    except json.JSONDecodeError:
                        continue
                    if item.get("texto"):
                        # Normalize common field names produced by roer.py and other tools
                        # prefer 'caminho' but keep 'arquivo' for backward compatibility
                        if "arquivo" in item and "caminho" not in item:
                            item["caminho"] = item.get("arquivo")
                        # Ensure we always have a reference to which jsonl file it came from
                        item.setdefault("arquivo_jsonl", str(caminho_jsonl))
                        item.setdefault("linha_jsonl", numero_linha)
                        # Ensure titulo is present
                        item.setdefault("titulo", item.get("titulo") or Path(item.get("caminho", "")).stem)
                        # Normalize parte to int when possible
                        if "parte" in item:
                            try:
                                item["parte"] = int(item["parte"])
                            except Exception:
                                pass
                        else:
                            item["parte"] = item.get("parte", "?")
                        # Normalize embedding key(s)
                        if "embedding" not in item and "embeddings" in item:
                            item["embedding"] = item.get("embeddings")
                        # Ensure modelo_embedding exists
                        item.setdefault("modelo_embedding", item.get("modelo_embedding", ""))
                        chunks.append(item)
        except OSError:
            continue
    return chunks


def buscar_textual_jsonl(embeddings_dir: Path, consulta: str, limite: int) -> list[dict]:
    chunks = carregar_chunks_jsonl(embeddings_dir)
    termos = re.findall(r"[\w-]{3,}", consulta.lower(), flags=re.UNICODE)
    if not termos:
        return []

    resultados: list[dict] = []
    for item in chunks:
        texto = item.get("texto", "")
        texto_lower = texto.lower()
        ocorrencias = sum(texto_lower.count(termo) for termo in termos)
        if ocorrencias > 0:
            resultados.append(
                {
                    "titulo": item.get("titulo") or item.get("arquivo") or Path(item.get("arquivo_jsonl", "")).stem,
                    "tipo": item.get("tipo", "jsonl"),
                    "caminho": item.get("caminho") or item.get("arquivo") or item.get("arquivo_jsonl", ""),
                    "arquivo_jsonl": item.get("arquivo_jsonl", ""),
                    "linha_jsonl": item.get("linha_jsonl", ""),
                    "parte": item.get("parte", "?"),
                    "texto": texto,
                    "score": float(ocorrencias),
                }
            )

    return sorted(resultados, key=lambda r: r["score"], reverse=True)[:limite]


def buscar_semantica_jsonl(embeddings_dir: Path, consulta: str, modelo: str, limite: int) -> list[dict]:
    vetor = embedding_ollama(consulta, modelo)
    if not vetor:
        return []

    chunks = carregar_chunks_jsonl(embeddings_dir)
    resultados: list[dict] = []
    for item in chunks:
        modelo_item = item.get("modelo_embedding")
        if modelo_item and modelo_item != modelo:
            continue
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            continue
        score = cosine(vetor, embedding)
        if score <= 0:
            continue
        resultados.append(
            {
                "titulo": item.get("titulo") or item.get("arquivo") or Path(item.get("arquivo_jsonl", "")).stem,
                "tipo": item.get("tipo", "jsonl"),
                "caminho": item.get("caminho") or item.get("arquivo") or item.get("arquivo_jsonl", ""),
                "arquivo_jsonl": item.get("arquivo_jsonl", ""),
                "linha_jsonl": item.get("linha_jsonl", ""),
                "parte": item.get("parte", "?"),
                "texto": item.get("texto", ""),
                "modelo_embedding": modelo_item or "",
                "score": score,
            }
        )

    return sorted(resultados, key=lambda r: r["score"], reverse=True)[:limite]


# ==== Funções para leitura crítica interativa e farejada ====

def limpar_slug(texto: str, limite: int = 70) -> str:
    slug = texto.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:limite].strip("-") or "farejada"


def montar_contexto_para_qwen(resultados: list[dict], max_chars_por_trecho: int = 1400) -> str:
    blocos: list[str] = []
    for i, row in enumerate(resultados, start=1):
        texto = re.sub(r"\s+", " ", row.get("texto", "")).strip()
        if len(texto) > max_chars_por_trecho:
            texto = texto[:max_chars_por_trecho].rstrip() + "..."
        blocos.append(
            f"RASTRO {i}\n"
            f"Busca: {row.get('modo_busca', 'não informado')}\n"
            f"Título: {row.get('titulo', '')}\n"
            f"Arquivo: {row.get('caminho', '')}\n"
            f"Parte: {row.get('parte', '?')}\n"
            f"Tipo: {row.get('tipo', '')}\n"
            f"Score: {row.get('score', 0):.4f}\n"
            f"Trecho: {texto}"
        )
    return "\n\n---\n\n".join(blocos)


def chamar_qwen(prompt: str, modelo: str, num_predict: int = 3200) -> str:
    try:
        resp = requests.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": modelo,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": num_predict,
                    "temperature": 0.4,
                },
            },
            timeout=600,
        )
        resp.raise_for_status()
        dados = resp.json()
        resposta = dados.get("response", "")
        return resposta.strip()
    except requests.RequestException as exc:
        return f"ERRO ao chamar o modelo {modelo}: {exc}"


def montar_prompt_farejada(consulta: str, resultados: list[dict]) -> str:
    contexto = montar_contexto_para_qwen(resultados)
    return f"""
Você é o Rato, um assistente de pesquisa que fareja ressonâncias críticas em uma biblioteca acadêmica pessoal.
Responda diretamente em Markdown. Não inclua cadeia de pensamento.

Consulta do pesquisador:
{consulta}

Rastros encontrados:

{contexto}

Primeiro construa um mapa dos rastros:

- Agrupe os rastros por proximidade conceitual.
- Identifique operadores recorrentes.
- Identifique tensões recorrentes.
- Explique por que esses trechos podem ter sido encontrados para esta consulta.
- Não force relações. Quando a conexão for fraca, diga claramente.

Depois faça uma leitura crítica mais livre, mas sem forçar relações.

Que relação conceitual existe entre esses rastros?

Onde há aproximação real e onde há falsa ressonância?

Antes de formular uma hipótese, separe claramente:

## O que os rastros sustentam

Liste apenas relações que aparecem diretamente nos trechos.

## O que é inferência plausível

Liste relações que podem ser pensadas a partir dos trechos, mas que já dependem de interpretação.

## O que seria especulativo demais

Liste relações que pareceriam interessantes, mas que os rastros ainda não sustentam suficientemente.

Depois formule:

## Hipótese emergente

Uma hipótese de pesquisa que poderia emergir deste conjunto, sem transformar toda precariedade em falta, controle ou dominação se os rastros não exigirem isso.

## Pergunta emergente

Escolha a pergunta mais fértil que surgiu durante sua análise.

## Resposta provisória

Tente responder provisoriamente à pergunta emergente usando apenas os rastros apresentados.

## O que permanece aberto?

Indique onde seria necessário investigar mais.
""".strip()


def salvar_farejada(
    consulta: str,
    hipoteses: str,
    avaliacao_relacoes: str,
    resultados: list[dict],
    pasta: Path,
    modelo_hipoteses: str,
    modelo_relacoes: str,
) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    data = datetime.now().strftime("%Y-%m-%d")
    slug = limpar_slug(consulta)
    caminho = pasta / f"farejada-{data}-{slug}.md"

    rastros = []
    for i, row in enumerate(resultados, start=1):
        modo_busca = row.get("modo_busca", "não informado")
        rastros.append(
            f"{i}. **{row.get('titulo', '')}** — busca: {modo_busca} · `{row.get('caminho', '')}` · "
            f"Parte {row.get('parte', '?')} · score={row.get('score', 0):.4f}"
        )

    conteudo = f"""---
titulo: "Farejada: {consulta}"
tipo: farejada
data: "{data}"
consulta: "{consulta}"
modelo-hipoteses: {modelo_hipoteses}
modelo-relacoes: {modelo_relacoes}
---

# Farejada: {consulta}

## Consulta

{consulta}

## Hipóteses

{hipoteses}

## Avaliando relações

{avaliacao_relacoes}

## Rastros usados

{chr(10).join(rastros)}
"""

    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def buscar_por_modo(args: argparse.Namespace, consulta: str) -> dict[str, list]:
    """Executa busca conceitual, por palavras ou ambas, conforme a CLI."""
    resultados: dict[str, list] = {}

    if args.busca in {"conceitual", "hibrida"}:
        resultados["conceitual"] = buscar_semantica_jsonl(
            args.embeddings_dir,
            consulta,
            args.embedding,
            args.limite,
        )

    if args.busca in {"palavras", "hibrida"}:
        conn = conectar(args.db)
        resultados["palavras"] = buscar_textual(conn, consulta, args.limite)
        conn.close()

    return resultados


def linha_para_dict(row) -> dict:
    if isinstance(row, sqlite3.Row):
        return {chave: row[chave] for chave in row.keys()}
    return dict(row)


def resultados_para_leitura(resultados_por_modo: dict[str, list]) -> list[dict]:
    """Achata resultados mantendo a origem da busca para a leitura crítica."""
    saida: list[dict] = []
    for modo, resultados in resultados_por_modo.items():
        for row in resultados:
            item = linha_para_dict(row)
            item["modo_busca"] = modo
            saida.append(item)
    return saida


def executar_farejada(args: argparse.Namespace, consulta: str, perguntar_salvar: bool) -> None:
    """Busca rastros, gera leitura crítica e salva ou pergunta se salva a farejada."""
    print("\nFarejando rastros na biblioteca...\n")
    resultados_por_modo = buscar_por_modo(args, consulta)
    resultados = resultados_para_leitura(resultados_por_modo)

    if not resultados:
        print("Nenhum resultado encontrado.")
        return

    print("Rastros encontrados:")
    for i, row in enumerate(resultados, start=1):
        print(
            f"{i}. [{row.get('modo_busca', '?')}] {row['titulo']} · "
            f"Parte {row['parte']} · score={row['score']:.4f}"
        )

    print(f"\nChamando {args.modelo_chat} para levantar hipóteses...\n")
    prompt = montar_prompt_farejada(consulta, resultados)
    hipoteses = chamar_qwen(prompt, args.modelo_chat, num_predict=args.max_tokens_hipoteses)

    print("\n" + "=" * 72)
    print("HIPÓTESES")
    print("=" * 72)
    print(hipoteses)
    print("=" * 72 + "\n")

    print(f"\nChamando {args.modelo_relacoes} para avaliar relações...\n")
    trechos = formatar_trechos_relacoes(resultados, args.max_chars_relacoes)
    prompt_relacoes = PROMPT_AVALIAR_RELACOES.format(consulta=consulta, trechos=trechos)
    avaliacao_relacoes = chamar_qwen(
        prompt_relacoes,
        args.modelo_relacoes,
        num_predict=args.max_tokens_relacoes,
    )

    print("\n" + "=" * 72)
    print("AVALIANDO RELAÇÕES")
    print("=" * 72)
    print(avaliacao_relacoes)
    print("=" * 72 + "\n")

    if perguntar_salvar:
        salvar = input("Guardar esta farejada em uma ficha .md? [s/N] ").strip().lower()
        if salvar not in {"s", "sim", "y", "yes"}:
            print("Farejada não salva.")
            return

    caminho = salvar_farejada(
        consulta,
        hipoteses,
        avaliacao_relacoes,
        resultados,
        args.pasta_saida,
        args.modelo_chat,
        args.modelo_relacoes,
    )
    print(f"Ficha salva em: {caminho}")


def cmd_farejar_interativo(args: argparse.Namespace) -> None:
    print("🐀 O que quer que eu fareje hoje?")
    consulta = input("> ").strip()
    if not consulta:
        print("Nenhuma consulta informada.")
        return
    executar_farejada(args, consulta, perguntar_salvar=True)


def imprimir_resultados(resultados: list, campo_score: str = "score") -> None:
    if not resultados:
        print("Nenhum resultado encontrado.")
        return
    for i, row in enumerate(resultados, start=1):
        texto = re.sub(r"\s+", " ", row["texto"]).strip()
        trecho = texto[:450] + ("..." if len(texto) > 450 else "")
        print(f"\n{i}. {row['titulo']} [{row['tipo']}]")
        print(f"   {row['caminho']} · Parte {row['parte']} · score={row[campo_score]:.4f}")
        print(f"   {trecho}")


def imprimir_resultados_por_modo(resultados_por_modo: dict[str, list]) -> None:
    if not any(resultados_por_modo.values()):
        print("Nenhum resultado encontrado.")
        return

    titulos = {
        "conceitual": "Busca conceitual (embeddings JSONL)",
        "palavras": "Busca por palavras (SQLite FTS)",
    }
    for modo in ("conceitual", "palavras"):
        resultados = resultados_por_modo.get(modo, [])
        if not resultados:
            continue
        print(f"\n== {titulos[modo]} ==")
        imprimir_resultados(resultados)


def cmd_farejar(args: argparse.Namespace) -> None:
    if not args.consulta:
        cmd_farejar_interativo(args)
        return

    if args.somente_busca:
        resultados_por_modo = buscar_por_modo(args, args.consulta)
        imprimir_resultados_por_modo(resultados_por_modo)
        return
    executar_farejada(args, args.consulta, perguntar_salvar=False)


def cmd_aprender(args: argparse.Namespace) -> None:
    conn = conectar(args.db)
    conn.execute(
        "INSERT INTO aprendizados (texto, tags, criado_em) VALUES (?, ?, ?)",
        (args.texto, ",".join(args.tags), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    print("Aprendizado registrado.")


def cmd_aprendizados(args: argparse.Namespace) -> None:
    conn = conectar(args.db)
    rows = conn.execute(
        "SELECT id, texto, tags, criado_em FROM aprendizados ORDER BY id DESC LIMIT ?",
        (args.limite,),
    ).fetchall()
    for row in rows:
        tags = f" · tags: {row['tags']}" if row["tags"] else ""
        print(f"{row['id']}. {row['texto']} ({row['criado_em']}){tags}")


def limitar_texto(texto: str, max_chars: int) -> str:
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) <= max_chars:
        return texto
    return texto[: max_chars - 1].rstrip() + "..."


def metadados_resumidos(item: dict[str, Any]) -> str:
    partes = []
    for chave in ("titulo", "arquivo", "caminho", "documento", "chunk_id", "parte", "pagina", "autor", "year"):
        valor = item.get(chave)
        if valor not in (None, "", []):
            partes.append(f"{chave}: {valor}")
    return "; ".join(partes)


def formatar_trechos_relacoes(resultados: list[dict], max_chars_por_trecho: int) -> str:
    blocos = []
    for i, item in enumerate(resultados, start=1):
        texto = limitar_texto(str(item.get("texto", "")), max_chars_por_trecho)
        titulo = item.get("titulo") or Path(str(item.get("caminho", ""))).stem or "sem titulo"
        blocos.append(
            f"[Trecho {i}]\n"
            f"Titulo/arquivo: {titulo}\n"
            f"Arquivo: {item.get('caminho', item.get('arquivo_jsonl', ''))}\n"
            f"Arquivo JSONL: {item.get('arquivo_jsonl', 'nao informado')}\n"
            f"Linha JSONL: {item.get('linha_jsonl', 'nao informada')}\n"
            f"Parte: {item.get('parte', '?')}\n"
            f"Similaridade: {item.get('score', 0):.4f}\n"
            f"Metadados: {metadados_resumidos(item) or 'nao informado'}\n"
            f"Texto: {texto}"
        )
    return "\n\n---\n\n".join(blocos)


def main() -> None:
    parser = argparse.ArgumentParser(description="Memoria local para referencias academicas")
    parser.add_argument("--db", type=Path, default=DB_PADRAO, help="Arquivo SQLite da biblioteca")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_indexar = sub.add_parser("indexar", help="Indexa arquivos Markdown")
    p_indexar.add_argument("--pasta", type=Path, default=Path("."), help="Pasta com arquivos .md")
    p_indexar.add_argument("--embedding", default="", help=f"Modelo de embedding Ollama, ex: {MODELO_EMBEDDING_PADRAO}")
    p_indexar.set_defaults(func=cmd_indexar)

    p_farejar = sub.add_parser("farejar", help="Fareja rastros na biblioteca")
    p_farejar.add_argument("consulta", nargs="?", default="")
    p_farejar.add_argument("--limite", type=int, default=8)
    p_farejar.add_argument(
        "--busca",
        choices=("conceitual", "palavras", "hibrida"),
        default="conceitual",
        help="conceitual usa embeddings JSONL; palavras usa SQLite FTS; hibrida usa ambos",
    )
    p_farejar.add_argument("--embedding", default=MODELO_EMBEDDING_PADRAO, help="Modelo de embedding Ollama para busca conceitual")
    p_farejar.add_argument("--modelo-chat", default="qwen3:8b", help="Modelo Ollama usado para levantar hipoteses")
    p_farejar.add_argument("--modelo-relacoes", default="qwen3:8b", help="Modelo Ollama usado para avaliar relacoes entre rastros")
    p_farejar.add_argument("--pasta-saida", type=Path, default=Path("fichas/farejadas"), help="Pasta para salvar farejadas em Markdown")
    p_farejar.add_argument("--embeddings-dir", type=Path, default=EMBEDDINGS_DIR_PADRAO, help="Pasta com arquivos .jsonl de embeddings")
    p_farejar.add_argument("--max-chars-relacoes", type=int, default=1600, help="Maximo de caracteres por rastro na avaliacao de relacoes")
    p_farejar.add_argument("--max-tokens-hipoteses", type=int, default=3200, help="Maximo de tokens gerados na camada de hipoteses")
    p_farejar.add_argument("--max-tokens-relacoes", type=int, default=2600, help="Maximo de tokens gerados na camada de avaliacao de relacoes")
    p_farejar.add_argument("--somente-busca", action="store_true", help="Nao chama LLM nem salva ficha; apenas imprime os rastros")
    p_farejar.set_defaults(func=cmd_farejar)

    p_aprender = sub.add_parser("aprender", help="Registra uma correcao/preferencia sua")
    p_aprender.add_argument("texto")
    p_aprender.add_argument("--tags", nargs="*", default=[])
    p_aprender.set_defaults(func=cmd_aprender)

    p_aprend = sub.add_parser("aprendizados", help="Lista aprendizados registrados")
    p_aprend.add_argument("--limite", type=int, default=20)
    p_aprend.set_defaults(func=cmd_aprendizados)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
