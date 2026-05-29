#!/usr/bin/env python3
"""
Farejar rastros na biblioteca local
===================================

Uso básico:
    python3 rato/scripts/farejar.py indexar --pasta .
    python3 rato/scripts/farejar.py farejar "reparo manutenção cuidado"
    python3 rato/scripts/farejar.py aprender "Jackson: tratar repair como cuidado material, não só sustentabilidade"

Dependências:
    pip install requests pyyaml

Embeddings são opcionais. Sem Ollama/modelo de embedding, a busca textual continua funcionando.
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

import requests
import yaml


DB_PADRAO = Path("rato/biblioteca_referencias.sqlite")
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
CHUNK_MAX_WORDS = 650
CHUNK_OVERLAP = 80


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
            json={"model": modelo, "prompt": texto},
            timeout=60,
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


def cmd_farejar(args: argparse.Namespace) -> None:
    conn = conectar(args.db)
    if args.embedding:
        resultados = buscar_semantica(conn, args.consulta, args.embedding, args.limite)
        if resultados:
            imprimir_resultados(resultados)
            return
        print("Busca semantica indisponivel; usando busca textual.")
    imprimir_resultados(buscar_textual(conn, args.consulta, args.limite))


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Memoria local para referencias academicas")
    parser.add_argument("--db", type=Path, default=DB_PADRAO, help="Arquivo SQLite da biblioteca")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_indexar = sub.add_parser("indexar", help="Indexa arquivos Markdown")
    p_indexar.add_argument("--pasta", type=Path, default=Path("."), help="Pasta com arquivos .md")
    p_indexar.add_argument("--embedding", default="", help="Modelo de embedding Ollama, ex: nomic-embed-text")
    p_indexar.set_defaults(func=cmd_indexar)

    p_farejar = sub.add_parser("farejar", help="Fareja rastros na biblioteca")
    p_farejar.add_argument("consulta")
    p_farejar.add_argument("--limite", type=int, default=8)
    p_farejar.add_argument("--embedding", default="", help="Modelo de embedding Ollama")
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
