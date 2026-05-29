#!/usr/bin/env python3
"""
Converter PDFs para Markdown com Docling.

Uso:
    python rato/scripts/converter.py arquivo.pdf
    python rato/scripts/converter.py pasta-com-pdfs --saida .
    python rato/scripts/converter.py pasta-com-pdfs --limite 5
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
from pathlib import Path


DOCLING_PADRAO = Path(
    os.environ.get(
        "DOCLING_BIN",
        "/Users/gustavodeassis/.venvs/docling-md-py312/bin/docling",
    )
)
TIMEOUT_PADRAO = 600


def slug(texto: str, max_len: int = 140) -> str:
    texto = re.sub(r"[/:\\]+", " - ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = texto.strip(" .")
    return texto[:max_len].rstrip(" .") or "documento"


def listar_pdfs(entrada: Path, recursivo: bool) -> list[Path]:
    if entrada.is_file():
        return [entrada] if entrada.suffix.lower() == ".pdf" else []

    if recursivo:
        return sorted({*entrada.rglob("*.pdf"), *entrada.rglob("*.PDF")})
    return sorted({*entrada.glob("*.pdf"), *entrada.glob("*.PDF")})


def destino_markdown(pdf: Path, entrada: Path, saida: Path) -> Path:
    if entrada.is_file():
        return saida / f"{slug(pdf.stem)}.md"

    rel = pdf.relative_to(entrada)
    digest = hashlib.sha1(str(rel).encode("utf-8")).hexdigest()[:8]
    partes_pai = rel.parts[:-1]
    prefixo = slug(" - ".join(partes_pai), 80) if partes_pai else ""
    nome = f"{prefixo} - {slug(pdf.stem, 110)} - {digest}.md" if prefixo else (
        f"{slug(pdf.stem, 130)} - {digest}.md"
    )
    return saida / nome


def converter_pdf(pdf: Path, destino: Path, args: argparse.Namespace) -> tuple[str, str]:
    if destino.exists() and destino.stat().st_size > 1000 and not args.forcar:
        return "skip", "arquivo já existe"

    tmp_output = args.saida / ".docling-current"
    tmp_output.mkdir(parents=True, exist_ok=True)
    produzido = tmp_output / f"{pdf.stem}.md"
    if produzido.exists():
        produzido.unlink()

    cmd = [
        str(args.docling),
        "--from",
        "pdf",
        "--to",
        "md",
        "--device",
        args.device,
    ]
    if not args.com_ocr:
        cmd.append("--no-ocr")
    cmd.extend([
        "--image-export-mode",
        "placeholder",
        "--output",
        str(tmp_output),
        str(pdf),
    ])

    try:
        resultado = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"excedeu {args.timeout}s"

    if resultado.returncode == 0 and produzido.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        produzido.replace(destino)
        return "ok", ""

    detalhe = (resultado.stdout or "")[-2000:].replace("\n", "\\n")
    return "fail", detalhe


def main() -> int:
    parser = argparse.ArgumentParser(description="Converte PDF(s) para Markdown com Docling")
    parser.add_argument("entrada", type=Path, help="PDF ou pasta com PDFs")
    parser.add_argument("--saida", type=Path, default=Path("."), help="Pasta de saída dos .md")
    parser.add_argument("--docling", type=Path, default=DOCLING_PADRAO, help="Caminho do binário Docling")
    parser.add_argument("--device", default="cpu", help="Dispositivo Docling: cpu, mps etc.")
    parser.add_argument("--com-ocr", action="store_true", help="Permite OCR do Docling")
    parser.add_argument("--nao-recursivo", action="store_true", help="Não buscar PDFs em subpastas")
    parser.add_argument("--forcar", action="store_true", help="Reconverter mesmo se o .md já existir")
    parser.add_argument("--limite", type=int, default=0, help="Converte só N PDFs")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_PADRAO, help="Timeout por PDF em segundos")
    parser.add_argument("--log", type=Path, default=Path("rato/logs/conversao-docling.tsv"))
    args = parser.parse_args()

    if not args.entrada.exists():
        parser.error(f"entrada não encontrada: {args.entrada}")
    if not args.docling.exists():
        parser.error(f"Docling não encontrado: {args.docling}")

    args.saida.mkdir(parents=True, exist_ok=True)
    args.log.parent.mkdir(parents=True, exist_ok=True)

    pdfs = listar_pdfs(args.entrada, recursivo=not args.nao_recursivo)
    if args.limite:
        pdfs = pdfs[: args.limite]
    if not pdfs:
        print(f"Nenhum PDF encontrado em: {args.entrada}")
        return 0

    print(f"{len(pdfs)} PDF(s) encontrado(s)")
    print(f"Saída: {args.saida.resolve()}")
    print(f"Docling: {args.docling}")

    with args.log.open("a", encoding="utf-8") as log:
        for indice, pdf in enumerate(pdfs, start=1):
            destino = destino_markdown(pdf, args.entrada, args.saida)
            print(f"[{indice}/{len(pdfs)}] {pdf.name}", flush=True)
            status, detalhe = converter_pdf(pdf, destino, args)
            print(f"  {status}: {destino.name}", flush=True)
            log.write(f"{status}\t{pdf}\t{destino}\n")
            if detalhe:
                log.write(f"detail\t{pdf}\t{detalhe}\n")
            log.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
