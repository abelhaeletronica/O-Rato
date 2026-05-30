#!/usr/bin/env python3
"""
lamber.py — limpeza de Markdown extraído de PDF para a biblioteca do rato.

O rato lambe o texto antes de roer.

Sequência de limpeza:
  1. Remove cabeçalhos JSTOR e blocos editoriais do início
  2. Normaliza travessões colados
  3. Remove cabeçalhos de página repetidos
  4. Processa notas de rodapé (com continuações)
  5. Une parágrafos quebrados pelo PDF
  6. Limpa chamadas de nota soltas no texto
  7. Limpeza OCR geral (hifenização, aspas, tokens colados, domínios)
  8. (opcional) Converte notas de rodapé para formato Markdown [^N]
  9. (opcional) Marca blocos suspeitos para revisão humana
  10. (opcional) Limpeza adicional via Ollama

Uso:
    python3 rato/scripts/lamber.py arquivo.md --in-place
    python3 rato/scripts/lamber.py --pasta . --in-place
    python3 rato/scripts/lamber.py arquivo.md --in-place --com-ollama
    python3 rato/scripts/lamber.py arquivo.md --in-place --marcar-duvidas
    python3 rato/scripts/lamber.py arquivo.md --in-place --converter-notas
    python3 rato/scripts/lamber.py arquivo.md --saida arquivo-limpo.md
"""

from __future__ import annotations

import argparse
import html
import re
import time
from collections import defaultdict
from pathlib import Path

try:
    import wordninja
except ImportError:
    wordninja = None


# ─────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────

LETRAS = "A-Za-zÀ-ÖØ-öø-ÿ"
OLLAMA_URL = "http://localhost:11434/api/generate"
TIMEOUT_OLLAMA = 180
EMBEDDING_BATCH_SIZE = 6

PREFIXOS_OCR = {"cere", "meta", "movi", "perce", "repre"}
SUFIXOS_OCR = {"ção", "ções", "dade", "mente", "mento", "tude"}
CONECTORES_DOMINIO = {"www", "http", "https", "com", "br", "org", "net", "edu", "gov"}

PALAVRAS_CURTAS_COMUNS = {
    "a", "o", "as", "os", "um", "uma", "e", "ou", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "por", "com", "sem", "que", "se", "ao", "aos",
    "me", "te", "lhe", "já", "não", "mas", "sim", "tal", "sob", "sua", "seu",
}

PADROES_LIXO_PDF = [
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
]

CORRECOES_PONTUAIS = {
    "http://www. martinsfontes. com": "http://www.martinsfontes.com",
    "info@martinsfontes. com": "info@martinsfontes.com",
    "coisauma existência": "coisa - uma existência",
    "determinaçõessua": "determinações - sua",
    "necessida - de": "necessidade",
    "perma - necerão": "permanecerão",
    "inter - rupção": "interrupção",
    "neces - sariamente": "necessariamente",
    "Célu - las": "Células",
    "acres - centa": "acrescenta",
    "infinitamen - te": "infinitamente",
    "senti - dos": "sentidos",
    "estru - tura": "estrutura",
    "esta - do": "estado",
    "recupe - ramos": "recuperamos",
    "Coloca - dos": "Colocados",
    "desco - nhecido": "desconhecido",
    "inte - ligência": "inteligência",
    "seme - lhança": "semelhança",
    "inte - rior": "interior",
    "inex - tenso": "inextenso",
    "natu - ralmente": "naturalmente",
    "idea - lismo": "idealismo",
    "cons - ciência": "consciência",
    "fenô - menos": "fenômenos",
    "conti - nuidade": "continuidade",
    "claramen-te": "claramente",
    "literalmen-te": "literalmente",
    "educaçãonão": "educação não",
    "hábitos motorese o plano": "hábitos motores - e o plano",
    "Presses üniversitaires": "Presses Universitaires",
    "Presses Üniversitaires": "Presses Universitaires",
    "J 999": "1999",
    "Per  cepção": "Percepção",
}

PROMPT_LIMPEZA_OLLAMA = """\
Você é um editor de texto especializado em limpar OCR de textos acadêmicos em português.

TAREFA:
Corrija apenas artefatos evidentes de OCR no trecho abaixo.

REGRAS OBRIGATÓRIAS:
- Não reescreva o estilo.
- Não resuma.
- Não interprete.
- Não traduza.
- Não complete lacunas.
- Não acrescente informação.
- Preserve a estrutura Markdown.
- Preserve citações, notas, nomes próprios e referências.
- Preserve comentários REVISAR OCR exatamente como aparecem.
- Se não tiver certeza sobre uma correção, mantenha o trecho original.
- Corrija apenas problemas evidentes: palavras quebradas, espaços indevidos,
  pontuação colada, letras trocadas claramente por OCR.
- Responda apenas com o texto limpo, sem comentário antes ou depois.
"""


# ─────────────────────────────────────────────
# 1. REMOÇÃO DE BLOCOS EDITORIAIS (JSTOR etc.)
# ─────────────────────────────────────────────

def remover_bloco_jstor_inicial(texto: str) -> str:
    """Remove cabeçalhos JSTOR e blocos editoriais do início do ficheiro."""
    # Tenta encontrar marcador de início do conteúdo real
    marcadores = [
        r"^##\s+\w",
        r"^#\s+\w",
    ]
    for marcador in marcadores:
        match = re.search(marcador, texto, flags=re.MULTILINE)
        if match and match.start() > 200:
            yaml_match = re.match(r"\A(---\n.*?\n---\n\n)", texto, flags=re.DOTALL)
            yaml = yaml_match.group(1) if yaml_match else ""
            return yaml + texto[match.start():]

    # Remove linhas de lixo PDF linha a linha
    linhas = texto.splitlines()
    resultado = []
    for linha in linhas:
        s = linha.strip()
        if any(re.match(p, s, flags=re.IGNORECASE) for p in PADROES_LIXO_PDF):
            continue
        resultado.append(linha)
    return "\n".join(resultado)


# ─────────────────────────────────────────────
# 2. TRAVESSÕES
# ─────────────────────────────────────────────

def normalizar_travessoes(texto: str) -> str:
    """Normaliza travessões colados entre palavras."""
    texto = re.sub(rf"(?<=[{LETRAS}])-(?=[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ])", "—", texto)
    texto = re.sub(
        rf"(?<=[{LETRAS}])-(?=(and|or|but|with|without|to|from|for|hence|rather|"
        rf"though|that|which|when|where|while|because|since)\b)",
        "—", texto,
    )
    texto = re.sub(r"\s+-\s+", " — ", texto)
    texto = re.sub(r"\s+—\s+", " — ", texto)
    return texto


# ─────────────────────────────────────────────
# 3. CABEÇALHOS DE PÁGINA REPETIDOS
# ─────────────────────────────────────────────

def remover_cabecalhos_repetidos(texto: str) -> str:
    """Remove linhas curtas repetidas 3+ vezes — típicas de cabeçalho de página OCR."""
    linhas = texto.split("\n")
    contagem: dict[str, int] = {}
    for linha in linhas:
        normalizada = re.sub(r"\s+", "", linha).strip().lower()
        if (
            8 <= len(normalizada) <= 45
            and re.fullmatch(r"[a-zà-öø-ÿ0-9]+", normalizada)
            and not re.fullmatch(r"\d+", normalizada)
        ):
            contagem[normalizada] = contagem.get(normalizada, 0) + 1

    removiveis = {chave for chave, total in contagem.items() if total >= 3}
    if not removiveis:
        return texto

    filtradas = []
    for linha in linhas:
        if re.fullmatch(r"\s*\d{1,4}\s*", linha):
            continue
        normalizada = re.sub(r"\s+", "", linha).strip().lower()
        if normalizada in removiveis and not re.search(r"\s", linha.strip()):
            continue
        filtradas.append(linha)
    return "\n".join(filtradas)


# ─────────────────────────────────────────────
# 4. NOTAS DE RODAPÉ (lógica robusta do doc20)
# ─────────────────────────────────────────────

PADROES_INICIO_NOTA = (
    "This project", "See ", "Ibid", "Bataille", "Denis ", "Georges ", "Jean",
    "Laura ", "Jacques ", "Mike ", "Kelley", "Kristeva", "Mary ", "Derrida",
    "Hollier", "OC", "The ", "Der ", "Ibid.",
)

PADROES_CONTINUACAO_NOTA = (
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


def parece_nota_rodape(linha: str) -> bool:
    s = linha.strip()
    if not s:
        return False
    if not re.match(r"^\d{1,3}\.\s+\S+", s):
        return False
    depois_numero = re.sub(r"^\d{1,3}\.\s+", "", s)
    return depois_numero.startswith(PADROES_INICIO_NOTA)


def parece_continuacao_nota(linha: str) -> bool:
    s = linha.strip()
    if not s or parece_titulo_ou_bloco(s) or parece_nota_rodape(s):
        return False
    return any(re.match(p, s) for p in PADROES_CONTINUACAO_NOTA)


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


def parece_titulo_ou_bloco(linha: str) -> bool:
    s = linha.strip()
    if not s:
        return True
    if s.startswith(("#", ">", "- ", "* ", "|", "```", "<!--")):
        return True
    if re.match(r"^\d{1,3}\.\s+", s) and not parece_nota_rodape(s):
        return True
    if len(s) <= 80 and s.isupper() and re.search(rf"[{LETRAS}]", s):
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
    a = a.rstrip()
    b = b.lstrip()
    if not a or not b:
        return False
    if parece_titulo_ou_bloco(a) or parece_titulo_ou_bloco(b):
        return False
    if a.endswith((".", "?", "!", ":", ";", ")", "]", '"', "\u201d")):
        return False
    if re.match(r"^[a-zà-öø-ÿ(\"'\u2018\u2019]", b):
        return True
    if termina_com_ponte_sintatica(a) and re.match(r"^[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ]", b):
        return True
    if "—" in a[-40:] and re.match(rf"^[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ][{LETRAS}''-]+", b):
        return True
    return False


def processar_notas_e_paragrafos(texto: str) -> str:
    """Une parágrafos quebrados e processa notas de rodapé (lógica robusta)."""
    linhas = texto.splitlines()
    resultado: list[str] = []
    notas_pendentes: list[str] = []
    i = 0

    while i < len(linhas):
        linha = linhas[i]

        if parece_nota_rodape(linha):
            nota, i = consumir_bloco_nota(linhas, i)
            notas_pendentes.extend(nota)
            continue

        if linha.strip() == "":
            j = i + 1
            notas_encontradas: list[str] = []

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

    return "\n".join(resultado)


def limpar_chamadas_nota(texto: str) -> str:
    """Remove chamadas de nota soltas no texto (ex: 'palavra 1 . frase')."""
    texto = re.sub(r'([.!?][""]?)\s+I\s+([A-Z])', r"\1 \2", texto)
    texto = re.sub(r'([.!?][""]?)\s*\d{1,2}\s+([A-Z])', r"\1 \2", texto)
    texto = re.sub(r'([a-zà-öø-ÿ][""]?)\s*\d{1,2}\s+([A-Z])', r"\1 \2", texto)
    return texto


# ─────────────────────────────────────────────
# 5. LIMPEZA OCR GERAL
# ─────────────────────────────────────────────

def juntar_hifenizacao(match: re.Match) -> str:
    esquerda = match.group(1)
    direita = match.group(2)
    direita_norm = direita.lower()

    if direita_norm in {
        "a", "o", "as", "os", "um", "uma", "uns", "umas", "e", "ou",
        "ao", "aos", "à", "às", "de", "do", "da", "dos", "das", "em", "por", "para", "com",
    }:
        return f"{esquerda} - {direita}"

    if direita_norm in {"me", "te", "se", "lhe", "lhes", "lo", "la", "los", "las", "no", "na", "nos", "nas"}:
        return f"{esquerda}-{direita}"

    if (
        len(esquerda) <= 3
        or len(direita) <= 3
        or esquerda.lower() in PREFIXOS_OCR
        or direita_norm in SUFIXOS_OCR
    ):
        return f"{esquerda}{direita}"

    return match.group(0)


def normalizar_dominios_e_emails(texto: str) -> str:
    texto = re.sub(r"\b(www|http|https)\.\s+", r"\1.", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\b(www|http|https)\s+\.", r"\1.", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\.\s+(?=(com|br|org|net|edu|gov)\b)", ".", texto, flags=re.IGNORECASE)

    def juntar_ponto(match: re.Match) -> str:
        esquerda, direita = match.group(1), match.group(2)
        if (
            esquerda.lower() in CONECTORES_DOMINIO
            or direita.lower() in CONECTORES_DOMINIO
            or "@" in esquerda
            or "@" in direita
        ):
            return f"{esquerda}.{direita}"
        return match.group(0)

    for _ in range(3):
        texto = re.sub(
            r"\b([A-Za-z0-9_@-]{2,})\s*\.\s*([A-Za-z]{2,})\b",
            juntar_ponto,
            texto,
        )
    return texto


def separar_token_colado(match: re.Match) -> str:
    palavra = match.group(0)
    if wordninja is None or len(palavra) < 18 or not palavra.islower():
        return palavra
    partes = wordninja.split(palavra)
    if 2 <= len(partes) <= 4 and all(len(p) >= 2 for p in partes):
        return " ".join(partes)
    return palavra


def normalizar_aspas_linha(linha: str) -> str:
    partes = linha.split('"')
    if len(partes) < 3:
        return linha
    resultado = [partes[0]]
    aberta = True
    for parte in partes[1:]:
        if aberta:
            if resultado[-1] and re.search(rf"[{LETRAS}0-9)]$", resultado[-1]):
                resultado[-1] += " "
            resultado.append('"' + parte.lstrip())
        else:
            if resultado[-1].endswith(" "):
                resultado[-1] = resultado[-1].rstrip()
            proxima = parte
            if proxima and re.match(rf"^[{LETRAS}0-9]", proxima):
                proxima = " " + proxima
            resultado.append('"' + proxima)
        aberta = not aberta
    return "".join(resultado)


def limpar_ocr(texto: str) -> str:
    """Limpeza OCR geral: hifenização, aspas, tokens, domínios."""
    texto = html.unescape(texto)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = texto.replace("\u0002", "")
    texto = texto.replace("，", ",").replace("（", "(").replace("）", ")")
    texto = normalizar_dominios_e_emails(texto)

    for original, corrigido in CORRECOES_PONTUAIS.items():
        texto = texto.replace(original, corrigido)

    # Espaço antes de pontuação
    texto = re.sub(r"\s+([,.;:!?])", r"\1", texto)

    # Aspas internas
    texto = re.sub(
        r'"([^"\n]{1,120})"',
        lambda m: '"' + m.group(1).strip() + '"',
        texto,
    )
    texto = re.sub(rf"\"[ \t]+(?=[{LETRAS}])", '"', texto)
    texto = re.sub(rf"(?<=[{LETRAS}])[ \t]+\"(?=[\s,.;:)])", '"', texto)

    # Parênteses e colchetes
    texto = re.sub(r"([(\[])\s+", r"\1", texto)
    texto = re.sub(r"\s+([)\]])", r"\1", texto)

    # Hifenização OCR
    for padrao in [
        rf"\b([{LETRAS}]{{1,20}})\s+-\s+([{LETRAS}]{{1,20}})\b",
        rf"\b([{LETRAS}]{{1,20}})\s+-([{LETRAS}]{{1,20}})\b",
        rf"\b([{LETRAS}]{{1,20}})-\s+([{LETRAS}]{{1,20}})\b",
    ]:
        texto = re.sub(padrao, juntar_hifenizacao, texto)

    texto = re.sub(r"\s+-\s+", " - ", texto)
    texto = re.sub(rf"(?<=[{LETRAS}])\s+-([{LETRAS}]{{4,}})", r" - \1", texto)
    texto = re.sub(rf"([,.;:!?])([{LETRAS}])", r"\1 \2", texto)

    # Tokens colados (CamelCase acidental)
    texto = re.sub(
        rf"(?<=[a-zà-öø-ÿ])(?=[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ][a-zà-öø-ÿ]{{2,}})",
        " ", texto,
    )
    texto = re.sub(r"(?<=[a-zà-öø-ÿ])(?=[A-Z]{2}\b)", " ", texto)
    texto = re.sub(r"\b[a-z]{18,}\b", separar_token_colado, texto)

    for original, corrigido in CORRECOES_PONTUAIS.items():
        texto = texto.replace(original, corrigido)
    texto = normalizar_dominios_e_emails(texto)

    linhas = []
    for linha in texto.split("\n"):
        linha = normalizar_aspas_linha(linha)
        if "|" in linha:
            linhas.append(linha.rstrip())
        else:
            linhas.append(re.sub(r"[ \t]{2,}", " ", linha).rstrip())

    texto = "\n".join(linhas)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip() + "\n"


# ─────────────────────────────────────────────
# 6. CONVERSÃO DE NOTAS PARA FORMATO MARKDOWN
# ─────────────────────────────────────────────

def converter_notas_markdown(texto: str) -> str:
    """Converte notas de rodapé soltas para formato [^N] do Markdown."""
    linhas = texto.split('\n')
    notas_processadas: dict[str, dict] = {}
    ocorrencias_por_num: dict[str, list] = defaultdict(list)
    linhas_para_remover: set[int] = set()

    # Encontrar definições explícitas
    for idx, linha in enumerate(linhas):
        match = re.search(r'^-\s+(\d+)\s{2,}(.+)$', linha)
        if match:
            num, texto_def = match.group(1), match.group(2).strip()
            ocorrencias_por_num[num].append(('def_expl', idx, texto_def))
            continue
        match = re.search(r'^(\d+)\s{2,}(.+)$', linha)
        if match:
            num, texto_def = match.group(1), match.group(2).strip()
            if len(num) <= 3 and len(texto_def) > 10:
                ocorrencias_por_num[num].append(('def_expl', idx, texto_def))

    # Encontrar referências no texto
    for idx, linha in enumerate(linhas):
        for match in re.finditer(r'\s(\d+)\s+\.', linha):
            num = match.group(1)
            if num not in notas_processadas:
                notas_processadas[num] = {'tipo': 'ponto', 'linha_idx': idx, 'match': match, 'def_texto': ''}
        for match in re.finditer(r'\s(\d+)\s+,', linha):
            num = match.group(1)
            if num not in notas_processadas:
                notas_processadas[num] = {'tipo': 'virgula', 'linha_idx': idx, 'match': match, 'def_texto': ''}
        for match in re.finditer(r'([\w\'\"\)\]]) (\d+)(\s|$)', linha):
            num = match.group(2)
            if num not in notas_processadas:
                notas_processadas[num] = {
                    'tipo': 'geral', 'linha_idx': idx, 'match': match,
                    'grupos': (match.group(1), match.group(3)), 'def_texto': '',
                }

    # Associar definições
    for num in ocorrencias_por_num:
        def_expl = [o for o in ocorrencias_por_num[num] if o[0] == 'def_expl']
        if def_expl and num in notas_processadas:
            notas_processadas[num]['def_texto'] = def_expl[0][2]
            linhas_para_remover.add(def_expl[0][1])

    # Aplicar conversões por linha
    linhas_editadas = linhas.copy()
    conversoes_por_linha: dict[int, list] = defaultdict(list)
    for num, info in notas_processadas.items():
        if info['def_texto']:
            conversoes_por_linha[info['linha_idx']].append((num, info))

    for idx in sorted(conversoes_por_linha.keys()):
        linha = linhas_editadas[idx]
        conversoes_ord = sorted(conversoes_por_linha[idx], key=lambda x: x[1]['match'].start(), reverse=True)
        for num, info in conversoes_ord:
            match = info['match']
            tipo = info['tipo']
            if tipo == 'ponto':
                linha = linha[:match.start()] + f'[^{num}].' + linha[match.end():]
            elif tipo == 'virgula':
                linha = linha[:match.start()] + f'[^{num}],' + linha[match.end():]
            elif tipo == 'geral':
                g1, g3 = info['grupos']
                linha = linha[:match.start()] + f'{g1}[^{num}]{g3}' + linha[match.end():]
        linhas_editadas[idx] = linha

    linhas_editadas = [l for i, l in enumerate(linhas_editadas) if i not in linhas_para_remover]

    if notas_processadas:
        resultado_linhas = linhas_editadas + ['', '## Notas de Rodapé', '']
        for num in sorted(notas_processadas.keys(), key=lambda x: int(x)):
            def_texto = notas_processadas[num]['def_texto']
            if def_texto:
                resultado_linhas.append(f'[^{num}]: {def_texto}')
        return '\n'.join(resultado_linhas)

    return '\n'.join(linhas_editadas)


# ─────────────────────────────────────────────
# 7. MARCAÇÃO DE DÚVIDAS OCR
# ─────────────────────────────────────────────

def motivos_revisao_ocr(bloco: str) -> list[str]:
    motivos = []
    texto = bloco.strip()
    if not texto or texto.startswith("<!-- REVISAR OCR:"):
        return motivos
    if "\ufffd" in texto or "?" in texto:
        motivos.append("caractere ilegível")
    if re.search(r"\b[a-zà-öø-ÿ]{22,}\b", texto):
        motivos.append("token longo possivelmente colado")
    if re.search(r"\b[bcdfghjklmnpqrstvwxyzç]{5,}\b", texto, flags=re.IGNORECASE):
        motivos.append("sequência consonantal incomum")
    tokens = re.findall(rf"\b[{LETRAS}]{{1,20}}\b", texto.lower())
    sequencia_curta = estranhas = maior_suspeita = 0
    for token in tokens:
        if len(token) <= 4:
            sequencia_curta += 1
            if token not in PALAVRAS_CURTAS_COMUNS:
                estranhas += 1
            if sequencia_curta >= 8 and estranhas >= 4:
                maior_suspeita = max(maior_suspeita, sequencia_curta)
        else:
            sequencia_curta = estranhas = 0
    if maior_suspeita:
        motivos.append("sequência de palavras curtas incomuns")
    return motivos


def marcar_duvidas_ocr(texto: str) -> str:
    blocos = re.split(r"(\n{2,})", texto.strip())
    resultado = []
    for bloco in blocos:
        if not bloco or re.fullmatch(r"\n{2,}", bloco):
            resultado.append(bloco)
            continue
        motivos = motivos_revisao_ocr(bloco)
        if motivos:
            marcador = (
                "<!-- REVISAR OCR: trecho possivelmente corrompido; "
                f"motivos: {', '.join(motivos)} -->"
            )
            resultado.append(f"{marcador}\n{bloco}")
        else:
            resultado.append(bloco)
    return "".join(resultado).strip() + "\n"


# ─────────────────────────────────────────────
# 8. LIMPEZA VIA OLLAMA (opcional)
# ─────────────────────────────────────────────

def chamar_ollama_limpeza(prompt: str, modelo: str, tentativas: int = 3) -> str:
    import requests
    payload = {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 8192, "num_predict": 2200},
    }
    for tentativa in range(tentativas):
        try:
            resposta = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_OLLAMA)
            resposta.raise_for_status()
            return resposta.json().get("response", "").strip()
        except Exception as erro:
            if tentativa == tentativas - 1:
                raise RuntimeError(f"Ollama falhou: {erro}") from erro
            time.sleep(2 * (tentativa + 1))
    return ""


def dividir_chunks_ollama(texto: str, max_chars: int = 3000) -> list[str]:
    paragrafos = [p for p in re.split(r"\n{2,}", texto) if p.strip()]
    chunks, atual, tamanho = [], [], 0
    for par in paragrafos:
        p_tam = len(par)
        if atual and tamanho + p_tam + 2 > max_chars:
            chunks.append("\n\n".join(atual))
            atual, tamanho = [], 0
        if p_tam > max_chars:
            frases = re.split(r"(?<=[.!?])\s+", par)
            bloco, bloco_tam = [], 0
            for frase in frases:
                if bloco and bloco_tam + len(frase) + 1 > max_chars:
                    chunks.append(" ".join(bloco))
                    bloco, bloco_tam = [], 0
                bloco.append(frase)
                bloco_tam += len(frase) + 1
            if bloco:
                chunks.append(" ".join(bloco))
            continue
        atual.append(par)
        tamanho += p_tam + 2
    if atual:
        chunks.append("\n\n".join(atual))
    return chunks or [texto]


def limpar_com_ollama(texto: str, modelo: str, max_chars: int = 3000, seletivo: bool = False) -> str:
    if seletivo:
        blocos = re.split(r"(\n{2,})", texto.strip())
        suspeitos = [b for b in blocos if b.strip() and not re.fullmatch(r"\n{2,}", b) and motivos_revisao_ocr(b)]
        print(f"  Ollama seletivo: {len(suspeitos)} bloco(s) suspeito(s)")
        resultado = []
        processados = 0
        for bloco in blocos:
            if not bloco.strip() or re.fullmatch(r"\n{2,}", bloco):
                resultado.append(bloco)
                continue
            motivos = motivos_revisao_ocr(bloco)
            if not motivos:
                resultado.append(bloco)
                continue
            processados += 1
            print(f"  [{processados}/{len(suspeitos)}] limpando bloco ({', '.join(motivos)})...")
            prompt = f"{PROMPT_LIMPEZA_OLLAMA}\n\nTRECHO:\n{bloco}"
            limpo = chamar_ollama_limpeza(prompt, modelo=modelo)
            resultado.append(limpo or bloco)
        return "".join(resultado).strip() + "\n"
    else:
        chunks = dividir_chunks_ollama(texto, max_chars=max_chars)
        print(f"  Ollama completo: {len(chunks)} chunk(s)")
        limpos = []
        for i, chunk in enumerate(chunks, start=1):
            print(f"  [{i}/{len(chunks)}] limpando...")
            if "<!-- REVISAR OCR:" in chunk:
                limpos.append(chunk)
                continue
            prompt = f"{PROMPT_LIMPEZA_OLLAMA}\n\nTRECHO:\n{chunk}"
            limpo = chamar_ollama_limpeza(prompt, modelo=modelo)
            limpos.append(limpo or chunk)
        return "\n\n".join(limpos).strip() + "\n"


# ─────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────

def lamber(
    texto: str,
    com_ollama: bool = False,
    modelo_ollama: str = "qwen2.5:7b",
    ollama_chars: int = 3000,
    ollama_seletivo: bool = False,
    marcar_duvidas: bool = False,
    converter_notas: bool = False,
) -> str:
    """Pipeline completo de limpeza."""

    # 1. Remove blocos JSTOR e editoriais
    texto = remover_bloco_jstor_inicial(texto)

    # 2. Normaliza travessões
    texto = normalizar_travessoes(texto)

    # 3. Remove cabeçalhos de página repetidos
    texto = remover_cabecalhos_repetidos(texto)

    # 4 + 5. Processa notas e une parágrafos quebrados
    texto = processar_notas_e_paragrafos(texto)

    # 6. Limpa chamadas de nota soltas
    texto = limpar_chamadas_nota(texto)

    # 7. Limpeza OCR geral
    texto = limpar_ocr(texto)

    # 8. Converte notas para formato Markdown [^N] (opcional)
    if converter_notas:
        texto = converter_notas_markdown(texto)

    # 9. Marca dúvidas (opcional, antes do Ollama)
    if marcar_duvidas and not com_ollama:
        texto = marcar_duvidas_ocr(texto)

    # 10. Limpeza via Ollama (opcional)
    if com_ollama:
        if marcar_duvidas:
            texto = marcar_duvidas_ocr(texto)
        texto = limpar_com_ollama(
            texto,
            modelo=modelo_ollama,
            max_chars=ollama_chars,
            seletivo=ollama_seletivo,
        )

    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip() + "\n"


# ─────────────────────────────────────────────
# PROCESSAMENTO DE FICHEIROS
# ─────────────────────────────────────────────

def processar_arquivo(
    caminho: Path,
    destino: Path | None,
    in_place: bool,
    sufixo: str,
    **kwargs,
) -> Path:
    texto = caminho.read_text(encoding="utf-8", errors="replace")
    if in_place:
        backup = caminho.with_suffix(caminho.suffix + ".bak")
        if not backup.exists():
            backup.write_text(texto, encoding="utf-8")
    limpo = lamber(texto, **kwargs)

    if in_place:
        saida = caminho
    elif destino:
        saida = destino
    else:
        saida = caminho.with_name(f"{caminho.stem}{sufixo}{caminho.suffix}")

    saida.write_text(limpo, encoding="utf-8")
    return saida


def listar_markdowns(pasta: Path) -> list[Path]:
    pastas_ignoradas = {
        ".cache_indexador", ".embeddings", ".git", "__pycache__",
        "fichas", "leituras-brutas", "rato", "relacoes",
    }
    return [
        c for c in sorted(pasta.glob("*.md"))
        if not any(parte in pastas_ignoradas for parte in c.parts)
    ]

# ─────────────────────────────────────────────
# ENTRADA
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="lamber.py — limpeza de Markdown extraído de PDF para a biblioteca do rato",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    grupo_entrada = parser.add_mutually_exclusive_group(required=True)
    grupo_entrada.add_argument("arquivo", nargs="?", type=Path, help="Ficheiro .md a limpar")
    grupo_entrada.add_argument("--pasta", type=Path, help="Pasta com ficheiros .md")

    grupo_saida = parser.add_mutually_exclusive_group()
    grupo_saida.add_argument("--in-place", action="store_true", help="Sobrescreve o ficheiro original")
    grupo_saida.add_argument("--saida", type=Path, help="Ficheiro de saída (só com --arquivo)")

    parser.add_argument("--com-ollama", action="store_true", help="Activa limpeza adicional via Ollama")
    parser.add_argument("--modelo-ollama", default="qwen2.5:7b", help="Modelo Ollama para limpeza")
    parser.add_argument("--ollama-chars", type=int, default=3000, help="Tamanho máximo de chunk para Ollama")
    parser.add_argument("--ollama-seletivo", action="store_true", help="Envia só blocos suspeitos ao Ollama")
    parser.add_argument("--marcar-duvidas", action="store_true", help="Marca blocos suspeitos com comentário REVISAR OCR")
    parser.add_argument("--limite", type=int, default=0, help="Processa só N ficheiros (útil para teste)")
    parser.add_argument("--converter-notas", action="store_true", help="Converte notas de rodapé soltas para formato Markdown [^N]")

    args = parser.parse_args()

    if args.ollama_seletivo and not args.com_ollama:
        parser.error("--ollama-seletivo requer --com-ollama")
    if args.saida and args.pasta:
        parser.error("--saida só pode ser usado com --arquivo")

    # Sufixo para ficheiro de saída quando não é in-place
    sufixo = "-lambo"
    if args.com_ollama:
        sufixo += "-ollama-seletivo" if args.ollama_seletivo else "-ollama"
    if args.marcar_duvidas:
        sufixo += "-revisar"
    if args.converter_notas:
        sufixo += "-notas"

    kwargs = dict(
        com_ollama=args.com_ollama,
        modelo_ollama=args.modelo_ollama,
        ollama_chars=args.ollama_chars,
        ollama_seletivo=args.ollama_seletivo,
        marcar_duvidas=args.marcar_duvidas,
        converter_notas=args.converter_notas,
    )

    if args.pasta:
        arquivos = listar_markdowns(args.pasta)
        if args.limite:
            arquivos = arquivos[:args.limite]
        if not arquivos:
            raise SystemExit(f"Nenhum .md encontrado em: {args.pasta}")
        print(f"📄 {len(arquivos)} ficheiro(s) encontrado(s)")
        ok = erros = 0
        for arq in arquivos:
            try:
                saida = processar_arquivo(arq, None, args.in_place, sufixo, **kwargs)
                print(f"  ✅ {arq.name} → {saida.name}")
                ok += 1
            except Exception as e:
                print(f"  ❌ {arq.name} — {e}")
                erros += 1
        print(f"\n✅ {ok}  ❌ {erros}")
    else:
        if not args.arquivo or not args.arquivo.exists():
            raise SystemExit(f"Ficheiro não encontrado: {args.arquivo}")
        saida = processar_arquivo(args.arquivo, args.saida, args.in_place, sufixo, **kwargs)
        print(f"✅ {args.arquivo.name} → {saida.name}")


if __name__ == "__main__":
    main()
