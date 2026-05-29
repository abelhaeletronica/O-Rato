#!/usr/bin/env python3
"""
Limpeza conservadora de OCR em português.

Uso:
    python3 limpar.py arquivo.md --in-place
    python3 limpar.py arquivo.md --saida arquivo-limpo.md
    python3 limpar.py arquivo.md
    python3 limpar.py arquivo.md --com-ollama
    python3 limpar.py arquivo.md --marcar-duvidas
    python3 limpar.py arquivo.md --limpeza-pdf
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

try:
    import wordninja
except ImportError:  # dependência opcional; a limpeza principal continua funcionando
    wordninja = None


LETRAS = "A-Za-zÀ-ÖØ-öø-ÿ"
PREFIXOS_OCR = {
    "cere",
    "meta",
    "movi",
    "perce",
    "repre",
}
SUFIXOS_OCR = {
    "ção",
    "ções",
    "dade",
    "mente",
    "mento",
    "tude",
}

CORRECOES_FINAIS = {
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
    "suti - lizá-la": "sutilizá-la",
    "inte - rior": "interior",
    "inex - tenso": "inextenso",
    "não deixa - remos": "não deixaremos",
    "me-nos": "menos",
    "ape-nas": "apenas",
    "estímu-lo": "estímulo",
    "infinitamen-te": "infinitamente",
    "natu - ralmente": "naturalmente",
    "Consi - deramos": "Consideramos",
    "restau - rando": "restaurando",
    "segun - do": "segundo",
    "idea - lismo": "idealismo",
    "necessi - dades": "necessidades",
    "cons - ciência": "consciência",
    "fenô - menos": "fenômenos",
    "desemba - raçada": "desembaraçada",
    "conse - qüentemente": "conseqüentemente",
    "conti - nuidade": "continuidade",
}

CONECTORES_DOMINIO = {"www", "http", "https", "com", "br", "org", "net", "edu", "gov"}
OLLAMA_URL = "http://localhost:11434/api/generate"
TIMEOUT_OLLAMA = 180
PALAVRAS_CURTAS_COMUNS = {
    "a", "o", "as", "os", "um", "uma", "e", "ou", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "por", "com", "sem", "que", "se", "ao", "aos",
    "me", "te", "lhe", "já", "não", "mas", "sim", "tal", "sob", "sua", "seu",
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
- Preserve comentários `REVISAR OCR` exatamente como aparecem.
- Se não tiver certeza sobre uma correção, mantenha o trecho original.
- Se encontrar um comentário `REVISAR OCR`, não corrija o bloco marcado.
- Não tente reconstruir trechos ilegíveis ou corrompidos; preserve a sequência como está.
- Não transforme ruído de OCR em frase coerente por aproximação.
- Corrija apenas problemas evidentes: palavras quebradas, espaços indevidos, pontuação colada, letras trocadas claramente por OCR.
- Responda apenas com o texto limpo, sem comentário antes ou depois.
"""


def juntar_hifenizacao(match: re.Match) -> str:
    esquerda = match.group(1)
    direita = match.group(2)
    direita_norm = direita.lower()

    if direita_norm in {
        "a", "o", "as", "os", "um", "uma", "uns", "umas", "e", "ou", "ao", "aos", "à", "às",
        "de", "do", "da", "dos", "das", "em", "por", "para", "com",
    }:
        return f"{esquerda} - {direita}"

    # Casos de pronome enclítico: coloca -se, fazê -la etc.
    if direita_norm in {
        "me", "te", "se", "lhe", "lhes",
        "lo", "la", "los", "las", "no", "na", "nos", "nas",
    }:
        return f"{esquerda}-{direita}"

    # Hifenização de OCR por quebra de linha/coluna: ma -téria, claramen -te.
    # Evita juntar travessões editoriais como "ação - conforme".
    if (
        len(esquerda) <= 3
        or len(direita) <= 3
        or esquerda.lower() in PREFIXOS_OCR
        or direita_norm in SUFIXOS_OCR
    ):
        return f"{esquerda}{direita}"

    return match.group(0)


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


def separar_token_colado(match: re.Match) -> str:
    """Separa tokens longos colados pelo OCR, apenas quando o corte parece seguro."""
    palavra = match.group(0)
    if wordninja is None or len(palavra) < 18 or not palavra.islower():
        return palavra

    partes = wordninja.split(palavra)
    if 2 <= len(partes) <= 4 and all(len(p) >= 2 for p in partes):
        return " ".join(partes)
    return palavra


def normalizar_dominios_e_emails(texto: str) -> str:
    """Remove espaços espúrios em domínios/e-mails sem colar texto comum."""
    texto = re.sub(r"\b(www|http|https)\.\s+", r"\1.", texto, flags=re.IGNORECASE)
    texto = re.sub(
        r"\b(www|http|https)\s+\.",
        r"\1.",
        texto,
        flags=re.IGNORECASE,
    )
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


def remover_cabecalhos_repetidos(texto: str) -> str:
    """Remove linhas curtas repetidas muitas vezes, típicas de cabeçalho de página OCR."""
    linhas = texto.split("\n")
    contagem = {}
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


def parece_nota_rodape(linha: str) -> bool:
    """Detecta linhas isoladas que parecem notas de rodapé extraídas do PDF."""
    return bool(re.match(r"^\s*\d{1,3}\.\s+\S+", linha))


def parece_titulo_ou_bloco_especial(linha: str) -> bool:
    """Evita unir títulos, epígrafes, listas e blocos Markdown."""
    limpa = linha.strip()
    if not limpa:
        return True
    if limpa.startswith(("#", ">", "- ", "* ", "|", "```", "<!--")):
        return True
    if re.match(r"^\d{1,3}\.\s+", limpa):
        return True
    if len(limpa) <= 80 and limpa.isupper() and re.search(rf"[{LETRAS}]", limpa):
        return True
    return False


def deve_unir_linhas_pdf(anterior: str, atual: str) -> bool:
    """Decide se duas linhas consecutivas são provavelmente o mesmo parágrafo quebrado pelo PDF."""
    a = anterior.rstrip()
    b = atual.lstrip()
    if not a or not b:
        return False
    if parece_titulo_ou_bloco_especial(a) or parece_titulo_ou_bloco_especial(b):
        return False
    if parece_nota_rodape(a) or parece_nota_rodape(b):
        return False
    if a.endswith((".", "?", "!", ":", ";", ")", "]", '"')):
        return False
    if re.match(r"^[a-zà-öø-ÿ(\"'“‘]", b):
        return True
    return False


def limpar_estrutura_pdf(texto: str) -> str:
    """Limpeza estrutural leve para Markdown vindo de PDF acadêmico.

    Esta etapa tenta corrigir problemas que a limpeza OCR conservadora não deve
    resolver sozinha: entidades HTML, travessões colados, quebras artificiais de
    linha dentro de parágrafos e notas de rodapé soltas. É deliberadamente
    prudente para não reescrever o texto.
    """
    texto = texto.replace("&amp;", "&")
    texto = texto.replace("&quot;", '"').replace("&apos;", "'")
    texto = texto.replace("&lt;", "<").replace("&gt;", ">")

    # Normaliza travessões colados entre palavras: art-David, Bourgeois-and.
    texto = re.sub(rf"(?<=[{LETRAS}])-(?=[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ])", "—", texto)
    texto = re.sub(rf"(?<=[{LETRAS}])-(?=(and|or|but|with|without|to|from|for)\b)", "—", texto)
    texto = re.sub(r"\s+—\s+", "—", texto)

    linhas = texto.split("\n")
    resultado: list[str] = []
    notas: list[str] = []

    for linha in linhas:
        limpa = linha.strip()
        if parece_nota_rodape(linha):
            notas.append(limpa)
            continue

        if resultado and deve_unir_linhas_pdf(resultado[-1], linha):
            resultado[-1] = resultado[-1].rstrip() + " " + limpa
        else:
            resultado.append(linha.rstrip())

    texto = "\n".join(resultado)
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    if notas:
        texto = texto.rstrip() + "\n\n## Notas extraídas do PDF\n\n"
        texto += "\n".join(notas)

    return texto.strip() + "\n"


def limpar_texto(texto: str) -> str:
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = texto.replace("\u0002", "")
    texto = texto.replace("，", ",").replace("（", "(").replace("）", ")")
    texto = normalizar_dominios_e_emails(texto)

    # Normalizações pontuais comuns no arquivo do Bergson.
    texto = texto.replace("http :llwww. martinsfontes .com", "http://www.martinsfontes.com")
    texto = texto.replace("Presses üniversitaires", "Presses Universitaires")
    texto = texto.replace("Presses Üniversitaires", "Presses Universitaires")
    texto = texto.replace("2 a edição", "2a edição")
    texto = texto.replace("J 999", "1999")
    texto = texto.replace("Per  cepção", "Percepção")
    texto = texto.replace("claramen-te", "claramente")
    texto = texto.replace("literalmen-te", "literalmente")
    texto = texto.replace("coisauma existência", "coisa - uma existência")
    texto = texto.replace("educaçãonão", "educação não")
    texto = texto.replace("determinaçõessua", "determinações - sua")
    texto = texto.replace("hábitos motorese o plano", "hábitos motores - e o plano")
    for original, corrigido in CORRECOES_FINAIS.items():
        texto = texto.replace(original, corrigido)

    # Remove espaço antes de pontuação.
    texto = re.sub(r"\s+([,.;:!?])", r"\1", texto)

    # Limpa espaços internos de aspas sem colar as aspas às palavras vizinhas.
    texto = re.sub(
        r'"([^"\n]{1,120})"',
        lambda m: '"' + m.group(1).strip() + '"',
        texto,
    )
    texto = re.sub(rf"\"[ \t]+(?=[{LETRAS}])", '"', texto)
    texto = re.sub(rf"(?<=[{LETRAS}])[ \t]+\"(?=[\s,.;:)])", '"', texto)
    texto = re.sub(rf"\"[ \t]+([{LETRAS}])", r'"\1', texto)
    texto = re.sub(rf"([{LETRAS}])[ \t]+\"", r'\1"', texto)
    texto = re.sub(r"([(\[])\s+", r"\1", texto)
    texto = re.sub(r"\s+([)\]])", r"\1", texto)

    # Corrige separação por hífen dentro de palavras.
    texto = re.sub(
        rf"\b([{LETRAS}]{{1,20}})\s+-\s+([{LETRAS}]{{1,20}})\b",
        juntar_hifenizacao,
        texto,
    )
    texto = re.sub(
        rf"\b([{LETRAS}]{{1,20}})\s+-([{LETRAS}]{{1,20}})\b",
        juntar_hifenizacao,
        texto,
    )
    texto = re.sub(
        rf"\b([{LETRAS}]{{1,20}})-\s+([{LETRAS}]{{1,20}})\b",
        juntar_hifenizacao,
        texto,
    )

    # Hífen usado como travessão fica legível.
    texto = re.sub(r"\s+-\s+", " - ", texto)
    texto = re.sub(rf"(?<=[{LETRAS}])\s+-([{LETRAS}]{{4,}})", r" - \1", texto)

    # Espaço depois de pontuação colada à próxima palavra.
    texto = re.sub(rf"([,.;:!?])([{LETRAS}])", r"\1 \2", texto)
    texto = re.sub(rf"(?<=[{LETRAS}])\"([^\"\n]{{1,120}})\"(?=[{LETRAS}])", r' "\1" ', texto)
    texto = re.sub(rf"(?<=[{LETRAS}])\"([^\"\n]{{1,120}})\"", r' "\1"', texto)
    texto = re.sub(rf"\"([^\"\n]{{1,120}})\"(?=[{LETRAS}])", r'"\1" ', texto)
    texto = re.sub(rf"([{LETRAS}0-9])\"([^\"\n]{{1,120}})\"", r'\1 "\2"', texto)
    texto = re.sub(rf"\"([^\"\n]{{1,120}})\"([{LETRAS}0-9])", r'"\1" \2', texto)
    texto = re.sub(r'"[ \t]+([^"\n]+?)"', r'"\1"', texto)
    texto = re.sub(r'"([^"\n]+?)[ \t]+"', r'"\1"', texto)
    texto = re.sub(
        rf"(?<=[a-zà-öø-ÿ])(?=[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ][a-zà-öø-ÿ]{{2,}})",
        " ",
        texto,
    )
    texto = re.sub(r"(?<=[a-zà-öø-ÿ])(?=[A-Z]{2}\b)", " ", texto)
    texto = re.sub(r"\b[a-z]{18,}\b", separar_token_colado, texto)
    for original, corrigido in CORRECOES_FINAIS.items():
        texto = texto.replace(original, corrigido)
    texto = normalizar_dominios_e_emails(texto)

    # Evita mexer demais em tabelas, mas reduz espaços excessivos no texto corrido.
    linhas = []
    for linha in texto.split("\n"):
        linha = normalizar_aspas_linha(linha)
        if "|" in linha:
            linhas.append(linha.rstrip())
        else:
            linhas.append(re.sub(r"[ \t]{2,}", " ", linha).rstrip())

    texto = "\n".join(linhas)
    texto = remover_cabecalhos_repetidos(texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip() + "\n"


def motivos_revisao_ocr(bloco: str) -> list[str]:
    motivos = []
    texto = bloco.strip()
    if not texto or texto.startswith("<!-- REVISAR OCR:"):
        return motivos

    if "\ufffd" in texto or "�" in texto:
        motivos.append("caractere ilegível")

    if re.search(r"\b[a-zà-öø-ÿ]{22,}\b", texto):
        motivos.append("token longo possivelmente colado")

    if re.search(r"\b[bcdfghjklmnpqrstvwxyzç]{5,}\b", texto, flags=re.IGNORECASE):
        motivos.append("sequência consonantal incomum")

    tokens = re.findall(rf"\b[{LETRAS}]{{1,20}}\b", texto.lower())
    sequencia_curta = 0
    estranhas_na_sequencia = 0
    maior_suspeita = 0

    for token in tokens:
        if len(token) <= 4:
            sequencia_curta += 1
            if token not in PALAVRAS_CURTAS_COMUNS:
                estranhas_na_sequencia += 1
            if sequencia_curta >= 8 and estranhas_na_sequencia >= 4:
                maior_suspeita = max(maior_suspeita, sequencia_curta)
        else:
            sequencia_curta = 0
            estranhas_na_sequencia = 0

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


def dividir_chunks_ollama(texto: str, max_chars: int = 3000) -> list[str]:
    paragrafos = [p for p in re.split(r"\n{2,}", texto) if p.strip()]
    chunks = []
    atual = []
    tamanho = 0

    for paragrafo in paragrafos:
        p_tamanho = len(paragrafo)
        if atual and tamanho + p_tamanho + 2 > max_chars:
            chunks.append("\n\n".join(atual))
            atual = []
            tamanho = 0

        if p_tamanho > max_chars:
            frases = re.split(r"(?<=[.!?])\s+", paragrafo)
            bloco = []
            bloco_tamanho = 0
            for frase in frases:
                if bloco and bloco_tamanho + len(frase) + 1 > max_chars:
                    chunks.append(" ".join(bloco))
                    bloco = []
                    bloco_tamanho = 0
                bloco.append(frase)
                bloco_tamanho += len(frase) + 1
            if bloco:
                chunks.append(" ".join(bloco))
            continue

        atual.append(paragrafo)
        tamanho += p_tamanho + 2

    if atual:
        chunks.append("\n\n".join(atual))
    return chunks or [texto]


def chamar_ollama_limpeza(prompt: str, modelo: str, tentativas: int = 3) -> str:
    import requests

    payload = {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 8192,
            "num_predict": 2200,
        },
    }
    for tentativa in range(tentativas):
        try:
            resposta = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_OLLAMA)
            resposta.raise_for_status()
            return resposta.json().get("response", "").strip()
        except requests.RequestException as erro:
            if tentativa == tentativas - 1:
                raise RuntimeError(f"Ollama falhou na limpeza OCR: {erro}") from erro
            time.sleep(2 * (tentativa + 1))
    return ""


def limpar_texto_ollama(texto: str, modelo: str, max_chars: int) -> str:
    chunks = dividir_chunks_ollama(texto, max_chars=max_chars)
    print(f"Etapa Ollama: {len(chunks)} chunk(s), modelo {modelo}")

    limpos = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"  [{i}/{len(chunks)}] limpando...", flush=True)
        if "<!-- REVISAR OCR:" in chunk:
            limpos.append(chunk)
            continue
        prompt = f"{PROMPT_LIMPEZA_OLLAMA}\n\nTRECHO:\n{chunk}"
        limpo = chamar_ollama_limpeza(prompt, modelo=modelo)
        limpos.append(limpo or chunk)

    return "\n\n".join(limpos).strip() + "\n"


def limpar_texto_ollama_seletivo(texto: str, modelo: str,
                                 marcar_corrigidos: bool = False) -> str:
    blocos = re.split(r"(\n{2,})", texto.strip())
    suspeitos = [
        bloco for bloco in blocos
        if bloco.strip()
        and not re.fullmatch(r"\n{2,}", bloco)
        and motivos_revisao_ocr(bloco)
    ]
    print(f"Etapa Ollama seletiva: {len(suspeitos)} bloco(s) suspeito(s), modelo {modelo}")

    resultado = []
    processados = 0
    for bloco in blocos:
        if not bloco.strip() or re.fullmatch(r"\n{2,}", bloco):
            resultado.append(bloco)
            continue

        # Blocos já marcados com REVISAR OCR retornam sem motivos.
        motivos = motivos_revisao_ocr(bloco)
        if not motivos:
            resultado.append(bloco)
            continue

        processados += 1
        print(
            f"  [{processados}/{len(suspeitos)}] limpando bloco suspeito "
            f"({', '.join(motivos)})...",
            flush=True,
        )
        prompt = f"{PROMPT_LIMPEZA_OLLAMA}\n\nTRECHO:\n{bloco}"
        limpo = chamar_ollama_limpeza(prompt, modelo=modelo)
        saida = limpo or bloco
        if marcar_corrigidos:
            marcador = (
                "<!-- REVISAR OCR: bloco corrigido pelo Ollama seletivo; "
                f"conferir com o original; motivos: {', '.join(motivos)} -->"
            )
            saida = f"{marcador}\n{saida}"
        resultado.append(saida)

    return "".join(resultado).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Limpa OCR português em Markdown")
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--com-ollama", action="store_true", help="Executa segunda camada experimental com Ollama")
    parser.add_argument("--modelo-ollama", default="qwen2.5:7b", help="Modelo para --com-ollama")
    parser.add_argument("--ollama-chars", type=int, default=3000, help="Tamanho máximo de chunk para --com-ollama")
    parser.add_argument(
        "--ollama-seletivo",
        action="store_true",
        help="Com --com-ollama, envia ao modelo só parágrafos suspeitos",
    )
    parser.add_argument(
        "--marcar-duvidas",
        action="store_true",
        help="Marca parágrafos com sinais fortes de OCR corrompido para revisão humana",
    )
    parser.add_argument(
        "--limpeza-pdf",
        action="store_true",
        help="Aplica limpeza estrutural leve para textos acadêmicos extraídos de PDF",
    )
    args = parser.parse_args()

    if args.in_place and args.saida:
        parser.error("use --in-place ou --saida, não ambos")
    if args.ollama_seletivo and not args.com_ollama:
        parser.error("use --ollama-seletivo junto com --com-ollama")

    texto = args.arquivo.read_text(encoding="utf-8", errors="replace")
    limpo = limpar_texto(texto)
    if args.limpeza_pdf:
        limpo = limpar_estrutura_pdf(limpo)
    if args.com_ollama:
        if args.ollama_seletivo:
            limpo = limpar_texto_ollama_seletivo(
                limpo,
                modelo=args.modelo_ollama,
                marcar_corrigidos=args.marcar_duvidas,
            )
        else:
            if args.marcar_duvidas:
                limpo = marcar_duvidas_ocr(limpo)
            limpo = limpar_texto_ollama(limpo, modelo=args.modelo_ollama, max_chars=args.ollama_chars)
    if args.marcar_duvidas and not args.ollama_seletivo:
        limpo = marcar_duvidas_ocr(limpo)

    if args.com_ollama and args.ollama_seletivo:
        sufixo = "-limpo-ollama-seletivo"
    elif args.com_ollama:
        sufixo = "-limpo-ollama"
    else:
        sufixo = "-limpo"
    if args.marcar_duvidas:
        sufixo += "-revisar"
    if args.limpeza_pdf:
        sufixo += "-pdf"

    destino = args.arquivo if args.in_place else (
        args.saida or args.arquivo.with_name(
            f"{args.arquivo.stem}{sufixo}{args.arquivo.suffix}"
        )
    )
    destino.write_text(limpo, encoding="utf-8")
    print(f"Arquivo limpo salvo em: {destino}")


if __name__ == "__main__":
    main()
