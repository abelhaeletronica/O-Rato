#!/usr/bin/env python3
from pathlib import Path
import argparse
import html
import re

LETRAS = "A-Za-zÀ-ÖØ-öø-ÿ"

PADROES_LIXO = [
    r"^All use subject to .*$",
    r"^Your use of the JSTOR archive.*$",
    r"^JSTOR is a not-for-profit.*$",
    r"^The MIT Press is collaborating.*$",
    r"^Accessed: .*$",
    r"^Stable URL: .*$",
    r"^Published by: .*$",
    r"^Source: .*$",
    r"^Author\(s\): .*$",
    r"^REFERENCES$",
    r"^Linked references are available on JSTOR.*$",
    r"^You may need to log in to JSTOR.*$",
    r"^http://about\.\s*jstor\.org/terms$",
    r"^<!-- image -->$",
    r"^\s*\d+\s+OCTOBER\s*$",
    r"^OCTOBER\s+\d+.*$",
    r"^Informe without Conclusion\s+\d+\s*$",
]


def eh_lixo_pdf(linha: str) -> bool:
    s = linha.strip()
    return any(re.match(p, s, flags=re.I) for p in PADROES_LIXO)


def parece_titulo(linha: str) -> bool:
    s = linha.strip()
    if not s:
        return True
    if s.startswith(("---", "#", ">", "- ", "* ", "|", "```")):
        return True
    if len(s) < 90 and s.isupper() and re.search(rf"[{LETRAS}]", s):
        return True
    return False


def parece_nota_rodape(linha: str) -> bool:
    """Detecta notas de rodapé extraídas como linhas independentes.

    A detecção é conservadora: uma linha como "1. During the time..."
    pode ser início de seção do artigo, não nota.
    """
    s = linha.strip()

    if not s:
        return False

    # Precisa começar com algo tipo:
    # 1. Texto...
    # 22. Texto...
    if not re.match(r"^\d{1,3}\.\s+\S+", s):
        return False

    # Remove "1. "
    depois_numero = re.sub(r"^\d{1,3}\.\s+", "", s)

    # Padrões típicos de nota acadêmica
    padroes_inicio_nota = (
        "This project",
        "See ",
        "Ibid",
        "Bataille",
        "Denis ",
        "Georges ",
        "Jean",
        "Laura ",
        "Jacques ",
        "Mike ",
        "Kelley",
        "Kristeva",
        "Mary ",
        "Derrida",
        "Hollier",
        "OC",
        "The ",
        "Der ",
        "Ibid.",
    )

    return depois_numero.startswith(padroes_inicio_nota)


# New functions for detecting and consuming note blocks
def parece_continuacao_nota(linha: str) -> bool:
    """Detecta linhas que continuam uma nota quebrada pelo PDF."""
    s = linha.strip()
    if not s:
        return False
    if parece_titulo(s) or parece_nota_rodape(s):
        return False

    padroes = (
        r"^[A-Z][a-z]+\s*\(",
        r"^[A-Z][a-z]+,\s",
        r"^\([A-Z][A-Za-z]+:",
        r"^[A-Z][A-Za-z]+\s+Press,",
        r"^Press,\s*\d{4}",
        r"^Review,\s*no\.",
        r"^Routledge,\s*\d{4}",
        r"^Norton,\s*\d{4}",
        r"^Wing\s*\(",
        r"^p\.\s*\d+",
        r"^pp\.\s*\d+",
        r"^OC\)",
    )
    return any(re.match(p, s) for p in padroes)


def consumir_bloco_nota(linhas: list[str], i: int) -> tuple[list[str], int]:
    """Consome uma nota e suas linhas de continuação prováveis."""
    nota = [linhas[i].strip()]
    i += 1

    while i < len(linhas):
        atual = linhas[i]

        if atual.strip() == "":
            if i + 1 < len(linhas) and parece_continuacao_nota(linhas[i + 1]):
                i += 1
                continue
            break

        if parece_continuacao_nota(atual):
            nota.append(atual.strip())
            i += 1
            continue

        break

    return [" ".join(nota)], i


def deve_unir(a: str, b: str) -> bool:
    a = a.rstrip()
    b = b.lstrip()

    if not a or not b:
        return False
    if parece_titulo(a) or parece_titulo(b):
        return False
    if a.endswith((".", "?", "!", ":", ";", ")", "]", '"', "”")):
        return False
    if re.match(r"^[a-zà-öø-ÿ(\"'“‘]", b):
        return True
    return False


def termina_com_ponte_sintatica(linha: str) -> bool:
    s = linha.strip().lower()
    if not s:
        return False

    palavras_ponte = {
        "the", "a", "an", "of", "in", "on", "at", "to", "from", "with",
        "without", "for", "by", "as", "and", "or", "but", "there", "was",
        "were", "is", "are", "its", "their", "his", "her", "this", "that",
        "de", "da", "do", "das", "dos", "e", "ou", "com", "sem", "para",
    }

    ultima = re.sub(r"[^a-zà-öø-ÿ]", "", s.split()[-1])
    return ultima in palavras_ponte


def deve_unir_forte(a: str, b: str) -> bool:
    if deve_unir(a, b):
        return True

    a_limpa = a.rstrip()
    b_limpa = b.lstrip()

    if not a_limpa or not b_limpa:
        return False
    if parece_titulo(a_limpa) or parece_titulo(b_limpa):
        return False
    if a_limpa.endswith((".", "?", "!", ":", ";", ")", "]", '"', "”")):
        return False

    # Caso: "there was the" + "Centre Pompidou's..."
    if termina_com_ponte_sintatica(a_limpa) and re.match(r"^[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ]", b_limpa):
        return True

    # Caso: "Jean—Michel" + "Othoniel"
    if "—" in a_limpa[-40:] and re.match(r"^[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ][A-Za-zÀ-ÖØ-öø-ÿ'’-]+", b_limpa):
        return True

    return False


def limpar_chamadas_nota(texto: str) -> str:
    texto = re.sub(r'([.!?]["”]?)\s+I\s+([A-Z])', r"\1 \2", texto)
    texto = re.sub(r'([.!?]["”]?)\s*\d{1,2}\s+([A-Z])', r"\1 \2", texto)
    texto = re.sub(r'([a-zà-öø-ÿ]["”]?)\s*\d{1,2}\s+([A-Z])', r"\1 \2", texto)
    return texto


def limpar_travessoes(texto: str) -> str:
    texto = re.sub(rf"(?<=[{LETRAS}])-(?=[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ])", "—", texto)
    texto = re.sub(
        rf"(?<=[{LETRAS}])-(?=(and|or|but|with|without|to|from|for|hence|rather|though|that|which|when|where|while|because|since)\b)",
        "—",
        texto,
    )
    texto = re.sub(r"\s+-\s+", "—", texto)
    texto = re.sub(r"\s+—\s+", "—", texto)
    return texto


def remover_bloco_jstor_inicial(texto: str) -> str:
    marcador = "## Informe without Conclusion"
    idx = texto.find(marcador)

    if idx == -1:
        return texto

    yaml_match = re.match(r"\A(---\n.*?\n---\n\n)", texto, flags=re.S)
    yaml = yaml_match.group(1) if yaml_match else ""

    return yaml + texto[idx:]


def processar(texto: str) -> str:
    texto = html.unescape(texto)
    texto = remover_bloco_jstor_inicial(texto)
    texto = limpar_travessoes(texto)

    linhas = []
    for linha in texto.splitlines():
        if eh_lixo_pdf(linha):
            continue
        linhas.append(linha.rstrip())

    resultado = []
    notas_pendentes = []
    i = 0

    while i < len(linhas):
        linha = linhas[i]

        if parece_nota_rodape(linha):
            nota, i = consumir_bloco_nota(linhas, i)
            notas_pendentes.extend(nota)
            continue

        if linha.strip() == "":
            j = i + 1
            notas_encontradas = []

            while j < len(linhas):
                if linhas[j].strip() == "":
                    j += 1
                    continue
                if parece_nota_rodape(linhas[j]):
                    nota, j = consumir_bloco_nota(linhas, j)
                    notas_encontradas.extend(nota)
                    continue
                break

            if j < len(linhas) and resultado and deve_unir_forte(resultado[-1], linhas[j]):
                notas_pendentes.extend(notas_encontradas)
                resultado[-1] = resultado[-1].rstrip() + " " + linhas[j].strip()
                i = j + 1
                continue

            if notas_pendentes:
                resultado.extend(["", *notas_pendentes, ""])
                notas_pendentes = []
            if notas_encontradas:
                resultado.extend(["", *notas_encontradas, ""])

            if resultado and resultado[-1].strip() != "":
                resultado.append("")

            i += 1
            continue

        if resultado and deve_unir_forte(resultado[-1], linha):
            resultado[-1] = resultado[-1].rstrip() + " " + linha.strip()
        else:
            if notas_pendentes and linha.strip():
                resultado.extend(["", *notas_pendentes, ""])
                notas_pendentes = []
            resultado.append(linha)

        i += 1

    if notas_pendentes:
        resultado.extend(["", *notas_pendentes])

    texto = "\n".join(resultado)
    texto = limpar_chamadas_nota(texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    return texto.strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Limpeza estrutural leve de Markdown extraído de PDF acadêmico"
    )
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--in-place", action="store_true")

    args = parser.parse_args()

    if args.in_place and args.saida:
        parser.error("use --in-place ou --saida, não ambos")

    texto = args.arquivo.read_text(encoding="utf-8", errors="replace")
    limpo = processar(texto)

    if args.in_place:
        destino = args.arquivo
    else:
        destino = args.saida or args.arquivo.with_name(
            f"{args.arquivo.stem}-pdf{args.arquivo.suffix}"
        )

    destino.write_text(limpo, encoding="utf-8")
    print(f"Arquivo PDF depurado salvo em: {destino}")


if __name__ == "__main__":
    main()
