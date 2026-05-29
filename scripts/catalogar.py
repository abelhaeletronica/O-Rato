#!/usr/bin/env python3
"""
Catalogar metadados bibliográficos em arquivos Markdown.

Uso:
    python3 catalogar.py arquivo.md
    python3 catalogar.py arquivo.md --aplicar
    python3 catalogar.py . --limite 20
    python3 catalogar.py . --aplicar --forcar
    python3 catalogar.py . --autores minha-biblioteca/autores.yaml

Por padrão, o script apenas mostra uma prévia. Use --aplicar para escrever.
O ficheiro autores.yaml é procurado automaticamente na pasta do script;
passe --autores para indicar outro caminho.
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml


# ──────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────

PASTAS_IGNORADAS = {
    ".cache_indexador",
    ".embeddings",
    ".git",
    "__pycache__",
    "fichas",
    "leituras-brutas",
    "rato",
    "relacoes",
}

SUFIXOS_RUIDO_ARQUIVO = {"docling", "limpo", "ocr", "revisar", "ollama", "seletivo"}
PARTICULAS_NOME_ARQUIVO = {"filho", "junior", "júnior", "neto", "sobrinho"}

PALAVRAS_TITULO_FRACAS = {
    "abstract", "contents", "copyright", "index", "references",
    "sumario", "sumário", "conselho editorial", "conselho consultivo",
    "instituto de filosofia artes e cultura", "revisão da tradução",
    "tessitura editora ltda", "tradução", "universidade federal de ouro preto",
}

PREFIXOS_AUTOR_FRACOS = (
    "colecao", "coleção", "conselho editorial", "copyright", "editora",
    "conselho", "instituto", "livraria", "organizacao", "organização",
    "traducao", "tradução", "translated", "universidade",
)

PREFIXOS_NAO_AUTOR = {
    "applications", "art", "bbc", "culebra", "en", "impressao",
    "manifest", "ocr", "tecnicas", "tomo", "zealand",
}

PALAVRAS_CURTAS_COMUNS = {
    "a", "o", "as", "os", "um", "uma", "e", "ou", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "por", "com", "sem", "que", "se", "ao", "aos",
    "me", "te", "lhe", "já", "não", "mas", "sim", "tal", "sob", "sua", "seu",
}

# Limites de tamanho para inferência de tipo de texto
_LIMITE_LIVRO = 80_000
_LIMITE_ARTIGO = 20_000
_LIMITE_CAPITULO = 5_000


# ──────────────────────────────────────────────
# Padrões Regex Pré-Compilados
# ──────────────────────────────────────────────

class _RE:
    """Registry de expressões regulares pré-compiladas para desempenho."""
    
    # Limpeza de linha
    HEADING = re.compile(r"^#+\s*")
    MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
    MARKUP_PREFIX = re.compile(r"^[>*\-\s]+")
    WHITESPACE = re.compile(r"\s+")
    QUOTES = re.compile(r"^ *[\"']+|[\"']+$")
    PUNCTUATION_END = re.compile(r"[,.;:]+$")
    PARENTHESES = re.compile(r"\s*\([^)]*\)\s*$")
    
    # Ano
    ANO_ARQUIVO = re.compile(r"(1[89]\d{2}|20\d{2})")
    ANO_CORPO = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
    
    # Nome do arquivo
    SPLIT_SLUG = re.compile(r"[-_\s]+")
    
    # Autor
    DIGITOS_LINHA = re.compile(r"\d{1,4}")
    
    # Título
    PREFIXO_AUTOR = re.compile(r"^(by|por|traduzido por|translated by)\b", re.IGNORECASE)
    
    # Autor detalhado
    AUTOR_NOME_CHARS = re.compile(r"[^A-Za-zÀ-ÖØ-öø-ÿ]")
    AUTOR_MAIUSCULAS = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ]")
    
    # Inferência de autor (padrões no corpo)
    AUTOR_HEADING_PT = re.compile(r"(?im)^#+\s*por\s+(.+?)\s*$")
    AUTOR_HEADING_EN = re.compile(r"(?im)^#+\s*by\s+(.+?)\s*$")
    AUTOR_INLINE_PT = re.compile(r"(?im)^por\s+(.+?)\s*$")
    AUTOR_INLINE_EN = re.compile(r"(?im)^by\s+(.+?)\s*$")
    AUTOR_FIELD_EN = re.compile(r"(?im)^author:\s*(.+?)\s*$")
    AUTOR_FIELD_PT = re.compile(r"(?im)^autor(?:a|es)?\s*:\s*(.+?)\s*$")
    
    # Tipo de texto
    REFERENCIAS = re.compile(
        r"^#+\s*(referências|references|bibliography|bibliografia)\s*$",
        re.IGNORECASE | re.MULTILINE
    )
    
    # Excerto semântico
    SPLIT_PARAGRAFOS = re.compile(r"\n{2,}")
    
    # Palavras funcionais por língua
    PALAVRAS_LINGUA = {
        "pt": re.compile(r"\b(que|não|para|uma|com|por|como|numa|este|esta|são|foi|ser|mas|também|quando|sobre)\b"),
        "en": re.compile(r"\b(the|and|that|with|for|this|from|have|been|their|which|they|what|more|also)\b"),
        "fr": re.compile(r"\b(les|des|une|dans|est|qui|pas|sur|par|que|mais|cette|sont|comme|plus|aussi)\b"),
        "de": re.compile(r"\b(die|der|das|und|ist|ein|nicht|auch|sich|sie|mit|dem|den|wie|aber|wird)\b"),
    }


# ──────────────────────────────────────────────
# Carregamento de autores externos
# ──────────────────────────────────────────────

def carregar_autores(caminho_yaml: Path | None = None) -> tuple[dict[str, str], dict[tuple, str]]:
    """Carrega autores.yaml e devolve (autores_simples, autores_compostos)."""
    simples: dict[str, str] = {}
    compostos: dict[tuple, str] = {}

    candidatos = []
    if caminho_yaml:
        candidatos.append(caminho_yaml)
    candidatos.append(Path(__file__).parent / "autores.yaml")
    candidatos.append(Path("autores.yaml"))

    dados: dict = {}
    for c in candidatos:
        if c.exists():
            try:
                dados = yaml.safe_load(c.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                pass
            break

    for chave, valor in dados.items():
        if chave == "_compostos":
            if isinstance(valor, dict):
                for par, nome in valor.items():
                    tokens = tuple(par.strip().lower().split())
                    compostos[tokens] = nome
        elif isinstance(chave, str) and isinstance(valor, str):
            simples[chave.lower()] = valor

    return simples, compostos


# ──────────────────────────────────────────────
# Frontmatter YAML
# ──────────────────────────────────────────────

def extrair_frontmatter(texto: str) -> tuple[dict, str, bool]:
    if texto.startswith("---\n"):
        partes = texto.split("---", 2)
        if len(partes) >= 3:
            try:
                meta = yaml.safe_load(partes[1]) or {}
                if isinstance(meta, dict):
                    return meta, partes[2].lstrip("\n"), True
            except yaml.YAMLError:
                pass
    return {}, texto, False


def despejar_frontmatter(meta: dict) -> str:
    return yaml.dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).strip()


def escrever_com_frontmatter(meta: dict, corpo: str) -> str:
    return f"---\n{despejar_frontmatter(meta)}\n---\n\n{corpo.lstrip()}"


# ──────────────────────────────────────────────
# Utilitários de texto
# ──────────────────────────────────────────────

def limpar_linha(linha: str) -> str:
    linha = _RE.HEADING.sub("", linha).strip()
    linha = _RE.MARKDOWN_LINK.sub(r"\1", linha)
    linha = _RE.MARKUP_PREFIX.sub("", linha).strip()
    linha = _RE.WHITESPACE.sub(" ", linha)
    return _RE.QUOTES.sub("", linha.strip())


def limpar_tokens_arquivo(tokens: list[str]) -> list[str]:
    limpos = [t for t in tokens if t]
    while limpos and limpos[-1].lower() in SUFIXOS_RUIDO_ARQUIVO:
        limpos.pop()
    return limpos


def normalizar_slug_titulo(slug: str) -> str:
    tokens = limpar_tokens_arquivo(_RE.SPLIT_SLUG.split(slug))
    return " ".join(tokens)


def titulo_tokens(tokens: list[str]) -> str | None:
    tokens = limpar_tokens_arquivo(tokens)
    if not tokens:
        return None
    titulo = re.sub(r"\s+", " ", " ".join(tokens)).strip()
    return titulo.title() if titulo else None


def titulo_do_arquivo(caminho: Path) -> str:
    texto = normalizar_slug_titulo(caminho.stem)
    return re.sub(r"[-_]+", " ", texto).title()


# ──────────────────────────────────────────────
# Metadados do nome do ficheiro
# ──────────────────────────────────────────────

def _extrair_ano_do_slug(tokens: list[str]) -> tuple[int | None, list[str]]:
    """Extrai ano do slug e retorna (ano, tokens_restantes)."""
    anos = [int(t) for t in tokens if _RE.ANO_ARQUIVO.fullmatch(t)]
    if not anos:
        return None, tokens
    ano = anos[-1]
    tokens_sem_ano = [t for t in tokens if not _RE.ANO_ARQUIVO.fullmatch(t)]
    return ano, tokens_sem_ano


def _procurar_autor_composto(tokens: list[str], autores_compostos: dict[tuple, str]) -> tuple[str | None, list[str]]:
    """Procura autor em padrão composto (dois primeiros tokens)."""
    if len(tokens) >= 3:
        par = (tokens[0].lower(), tokens[1].lower())
        if par in autores_compostos:
            return autores_compostos[par], tokens[2:]
    return None, tokens


def _procurar_autor_simples(tokens: list[str], autores_simples: dict[str, str]) -> tuple[str | None, list[str]]:
    """Procura autor no primeiro token."""
    if tokens:
        primeiro = tokens[0].lower()
        if primeiro in autores_simples:
            return autores_simples[primeiro], tokens[1:]
    return None, tokens


def _inferir_autor_candidato_e_titulo(tokens: list[str]) -> tuple[str | None, str | None]:
    """Infere autor candidato e título quando não há match direto."""
    if tokens and tokens[0].lower() not in PREFIXOS_NAO_AUTOR and len(tokens) >= 3:
        # Detecta partículas de nome (neto, júnior, etc.)
        fim = 2 if len(tokens) >= 4 and tokens[1].lower() in PARTICULAS_NOME_ARQUIVO else 1
        autor_candidato = " ".join(t.title() for t in tokens[:fim])
        titulo = titulo_tokens(tokens[fim:])
        return autor_candidato, titulo
    
    titulo = titulo_tokens(tokens) if tokens else None
    return None, titulo


def metadados_do_arquivo(
    caminho: Path,
    autores_simples: dict[str, str],
    autores_compostos: dict[tuple, str],
) -> dict:
    tokens = limpar_tokens_arquivo(_RE.SPLIT_SLUG.split(caminho.stem))
    dados: dict = {"slug": caminho.stem, "autor": None, "autor_candidato": None,
                   "titulo": None, "ano": None}
    if not tokens:
        return dados

    # Extrai ano do slug
    ano, tokens = _extrair_ano_do_slug(tokens)
    if ano:
        dados["ano"] = ano

    # Procura autor composto (dois tokens)
    autor, tokens = _procurar_autor_composto(tokens, autores_compostos)
    if autor:
        dados["autor"] = autor
        dados["titulo"] = titulo_tokens(tokens)
        return dados

    # Procura autor simples (um token)
    autor, tokens = _procurar_autor_simples(tokens, autores_simples)
    if autor:
        dados["autor"] = autor
        dados["titulo"] = titulo_tokens(tokens)
        return dados

    # Infere autor candidato se disponível
    autor_candidato, titulo = _inferir_autor_candidato_e_titulo(tokens)
    dados["autor_candidato"] = autor_candidato
    dados["titulo"] = titulo
    
    return dados


# ──────────────────────────────────────────────
# Inferência: título
# ──────────────────────────────────────────────

def parece_titulo_util(linha: str) -> bool:
    texto = limpar_linha(linha)
    texto_norm = _RE.WHITESPACE.sub(" ", texto.lower()).strip(":")
    if not texto or len(texto) < 4:
        return False
    if texto_norm in PALAVRAS_TITULO_FRACAS:
        return False
    if texto_norm.startswith(PREFIXOS_AUTOR_FRACOS):
        return False
    if _RE.DIGITOS_LINHA.fullmatch(texto):
        return False
    if _RE.PREFIXO_AUTOR.match(texto):
        return False
    return True


def _extrair_partes_nome_autor(autor: str) -> list[str]:
    """Extrai palavras significativas do nome do autor (evita preposições)."""
    return [p for p in _RE.WHITESPACE.split(autor) if len(p) > 1 and "." not in p]


def remover_autor_do_titulo(titulo: str, autor: str | None) -> str:
    if not autor:
        return titulo
    partes_autor = _extrair_partes_nome_autor(autor)
    restante = titulo
    for parte in partes_autor:
        restante = _RE.WHITESPACE.sub(
            " ",
            re.sub(rf"^\s*{re.escape(parte)}\s+", "", restante, flags=re.IGNORECASE)
        )
    return restante.strip() or titulo


def _primeiro_cabecalho_eh_autor(primeiro_cabecalho: str) -> bool:
    """Detecta se o primeiro cabeçalho parece ser um nome de autor."""
    return len(primeiro_cabecalho.split()) <= 4 and parece_nome_autor(primeiro_cabecalho)


def _titulo_removeu_autor(titulo_limpo: str, titulo_original: str) -> bool:
    """Verifica se a remoção do autor removeu texto do título."""
    return titulo_limpo != titulo_original


def _eh_titulo_valido_com_metadados(titulo: str, autor: bool, titulo_meta: bool) -> bool:
    """Valida um título quando temos autor e título nos metadados do arquivo."""
    return (
        autor and titulo_meta
        and "_" not in titulo
        and len(titulo.split()) >= 3
        and not parece_nome_autor(titulo)
    )


def inferir_titulo(corpo: str, caminho: Path, dados_arquivo: dict) -> tuple[str, str]:
    autor_arquivo = dados_arquivo.get("autor")
    titulo_arquivo = dados_arquivo.get("titulo")
    cabecalhos = [
        limpar_linha(l)
        for l in corpo.splitlines()[:80]
        if l.strip().startswith("#") and parece_titulo_util(l.strip())
    ]
    
    if cabecalhos:
        # Se houver múltiplos cabeçalhos e o primeiro parece ser autor
        if len(cabecalhos) >= 2 and _primeiro_cabecalho_eh_autor(cabecalhos[0]):
            return cabecalhos[1], "media"
        
        primeiro = remover_autor_do_titulo(cabecalhos[0], autor_arquivo)
        
        # Primeiro cabeçalho removeu autor mas parece nome de autor
        if titulo_arquivo and parece_nome_autor(primeiro):
            return titulo_arquivo, "baixa"
        
        # Remoção bem-sucedida e resultado é válido
        if _titulo_removeu_autor(primeiro, cabecalhos[0]) and parece_titulo_util(primeiro):
            return primeiro, "media"
        
        # Temos todos os metadados e o título passou na validação
        if _eh_titulo_valido_com_metadados(primeiro, autor_arquivo, titulo_arquivo):
            return primeiro, "media"
        
        # Não há autor no arquivo e o título é válido
        if not autor_arquivo and parece_titulo_util(primeiro):
            return primeiro, "media"
    
    # Fallback: usar título dos metadados se disponível
    if titulo_arquivo:
        return titulo_arquivo, "media" if autor_arquivo else "baixa"
    
    # Procurar em outras linhas do corpo
    for linha in corpo.splitlines()[:40]:
        if parece_titulo_util(linha):
            texto = limpar_linha(linha)
            eh_texto_valido = len(texto.split()) >= 2 and not texto.lower().startswith(PREFIXOS_AUTOR_FRACOS)
            if eh_texto_valido:
                return texto, "baixa"
    
    return titulo_do_arquivo(caminho), "baixa"


# ──────────────────────────────────────────────
# Inferência: autor
# ──────────────────────────────────────────────

def normalizar_autor(nome: str) -> str:
    nome = limpar_linha(nome)
    nome = _RE.PARENTHESES.sub("", nome).strip()
    return _RE.PUNCTUATION_END.sub("", nome).strip()


def _validar_tamanho_nome_autor(nome: str) -> bool:
    """Nome deve ter entre 3 e 80 caracteres."""
    return 3 <= len(nome) <= 80


def _validar_caracteres_nome_autor(nome: str) -> bool:
    """Nome não pode ter underscores ou dígitos."""
    return not ("_" in nome or any(c.isdigit() for c in nome))


def _validar_palavras_nome_autor(nome: str) -> bool:
    """Nome deve ter entre 1 e 7 palavras."""
    palavras = [p for p in _RE.WHITESPACE.split(nome) if p]
    return 1 <= len(palavras) <= 7


def _validar_caixa_nome_autor(nome: str) -> bool:
    """Detecta se é tudo maiúsculo (indicativo de acrônimo/erro)."""
    palavras = [p for p in _RE.WHITESPACE.split(nome) if p]
    letras = _RE.AUTOR_NOME_CHARS.sub("", nome)
    # Tudo maiúsculo com >3 palavras é suspeito
    return not (len(palavras) > 3 and letras and letras.upper() == letras)


def _validar_ortografia_nome_autor(nome: str) -> bool:
    """Verifica se as palavras seguem padrão de capitalização normal."""
    palavras = [p for p in _RE.WHITESPACE.split(nome) if p]
    minusculas_ok = {"de", "da", "do", "dos", "das", "e", "la", "van", "von", "di", "del"}
    for palavra in palavras:
        # Palavras pequenas não-autorizadas devem estar em maiúscula
        if palavra.islower() and palavra.lower() not in minusculas_ok and len(palavra) > 1:
            return False
    return True


def _contem_palavras_fortes(nome: str) -> bool:
    """Verifica se há palavras que parecem nomes próprios."""
    palavras = [p for p in _RE.WHITESPACE.split(nome) if p]
    minusculas_ok = {"de", "da", "do", "dos", "das", "e", "la", "van", "von", "di", "del"}
    fortes = [
        p for p in palavras
        if p.lower() not in minusculas_ok and _RE.AUTOR_MAIUSCULAS.match(p)
    ]
    return bool(fortes) and len(" ".join(fortes)) >= 3


def parece_nome_autor(nome: str) -> bool:
    nome = normalizar_autor(nome)
    
    # Validações progressivas (fail-fast)
    if not _validar_tamanho_nome_autor(nome):
        return False
    if not _validar_caracteres_nome_autor(nome):
        return False
    if nome.lower().startswith(PREFIXOS_AUTOR_FRACOS):
        return False
    if not _validar_palavras_nome_autor(nome):
        return False
    if not _validar_caixa_nome_autor(nome):
        return False
    if not _validar_ortografia_nome_autor(nome):
        return False
    
    return _contem_palavras_fortes(nome)


def inferir_autor(corpo: str, dados_arquivo: dict) -> tuple[str | None, str]:
    inicio = "\n".join(corpo.splitlines()[:120])
    padroes = [
        _RE.AUTOR_HEADING_PT,
        _RE.AUTOR_HEADING_EN,
        _RE.AUTOR_INLINE_PT,
        _RE.AUTOR_INLINE_EN,
        _RE.AUTOR_FIELD_EN,
        _RE.AUTOR_FIELD_PT,
    ]
    for padrao in padroes:
        match = padrao.search(inicio)
        if match:
            autor = normalizar_autor(match.group(1))
            if parece_nome_autor(autor):
                return autor, "alta"

    autor_arquivo = dados_arquivo.get("autor")
    if autor_arquivo:
        return autor_arquivo, "media"
    return None, "baixa"


# ──────────────────────────────────────────────
# Inferência: ano
# ──────────────────────────────────────────────

def inferir_ano(corpo: str, dados_arquivo: dict) -> tuple[int | None, str]:
    ano_arquivo = dados_arquivo.get("ano")
    if ano_arquivo:
        return int(ano_arquivo), "media"
    ano_atual = datetime.now().year + 1
    anos = [
        int(a)
        for a in _RE.ANO_CORPO.findall(corpo[:6000])
        if 1800 <= int(a) <= ano_atual
    ]
    if not anos:
        return None, "baixa"
    confianca = "media" if len(set(anos[:3])) == 1 else "baixa"
    return anos[0], confianca


# ──────────────────────────────────────────────
# Inferência: língua
# ──────────────────────────────────────────────

def inferir_lingua(corpo: str) -> str:
    amostra = corpo[:3000].lower()
    scores = {
        lingua: len(padrao.findall(amostra))
        for lingua, padrao in _RE.PALAVRAS_LINGUA.items()
    }
    melhor = max(scores, key=scores.get)
    # só declara se a margem for convincente
    total = sum(scores.values())
    if total == 0 or scores[melhor] / total < 0.4:
        return "und"  # undetermined
    return melhor


# ──────────────────────────────────────────────
# Inferência: tipo de texto
# ──────────────────────────────────────────────

def inferir_tipo_texto(corpo: str) -> str:
    chars = len(corpo)
    tem_referencias = bool(_RE.REFERENCIAS.search(corpo))
    if chars > _LIMITE_LIVRO:
        return "livro"
    if chars > _LIMITE_ARTIGO:
        return "artigo" if tem_referencias else "capitulo"
    if chars > _LIMITE_CAPITULO:
        return "capitulo" if not tem_referencias else "artigo"
    return "fragmento"


# ──────────────────────────────────────────────
# Excerto semântico
# ──────────────────────────────────────────────

def extrair_excerto(corpo: str, max_chars: int = 400) -> str:
    """Primeiro parágrafo substancial — âncora semântica para o Ollama."""
    paragrafos = [
        p.strip() for p in _RE.SPLIT_PARAGRAFOS.split(corpo)
        if len(p.strip()) > 80
        and not p.strip().startswith("#")
        and not p.strip().startswith("<!--")
        and not p.strip().startswith("---")
    ]
    if not paragrafos:
        return ""
    excerto = _RE.WHITESPACE.sub(" ", paragrafos[0])
    return excerto[:max_chars].rsplit(" ", 1)[0] + "…" if len(excerto) > max_chars else excerto


# ──────────────────────────────────────────────
# Confiança agregada
# ──────────────────────────────────────────────

def combinar_confiancas(*valores: str) -> str:
    peso = {"baixa": 0, "media": 1, "alta": 2}
    menor = min((peso.get(v, 0) for v in valores), default=0)
    for nome, valor in peso.items():
        if valor == menor:
            return nome
    return "baixa"


def campos_faltantes(meta: dict) -> set[str]:
    faltando = set()
    if not meta.get("title") and not meta.get("titulo"):
        faltando.add("title")
    if not meta.get("author") and not meta.get("authors"):
        faltando.add("author")
    if not meta.get("year") and not meta.get("ano"):
        faltando.add("year")
    return faltando


# ──────────────────────────────────────────────
# Pipeline principal por ficheiro
# ──────────────────────────────────────────────

def catalogar_arquivo(
    caminho: Path,
    forcar: bool,
    autores_simples: dict[str, str],
    autores_compostos: dict[tuple, str],
) -> tuple[str, dict, dict, list[str]]:
    texto = caminho.read_text(encoding="utf-8", errors="replace")
    meta, corpo, _ = extrair_frontmatter(texto)
    original = dict(meta)
    alterados: list[str] = []

    dados_arquivo = metadados_do_arquivo(caminho, autores_simples, autores_compostos)

    titulo, confianca_titulo = inferir_titulo(corpo, caminho, dados_arquivo)
    autor, confianca_autor = inferir_autor(corpo, dados_arquivo)
    ano, confianca_ano = inferir_ano(corpo, dados_arquivo)
    lingua = inferir_lingua(corpo)
    tipo = inferir_tipo_texto(corpo)
    excerto = extrair_excerto(corpo)

    def atualizar(campo: str, valor, condicao: bool = True) -> None:
        if condicao and valor and (forcar or not meta.get(campo)):
            meta[campo] = valor
            alterados.append(campo)

    atualizar("title", titulo)
    atualizar("author", autor)
    atualizar("year", ano)
    atualizar("lingua", lingua)
    atualizar("tipo", tipo)
    atualizar("excerto", excerto)

    # Campos de gestão — sempre presentes
    meta.setdefault("palavras-chave", [])
    meta.setdefault("metadados-fonte", "catalogar.py")

    if forcar or not meta.get("metadados-arquivo"):
        meta["metadados-arquivo"] = {
            k.replace("_", "-"): v
            for k, v in dados_arquivo.items()
            if v
        }

    faltando = campos_faltantes(meta)
    confianca = combinar_confiancas(
        confianca_titulo,
        confianca_autor if autor else "baixa",
        confianca_ano if ano else "baixa",
    )
    if not faltando and confianca == "baixa":
        confianca = "media"

    meta["metadados-confianca"] = confianca
    meta["metadados-revisao"] = bool(faltando or confianca != "alta")

    return corpo, original, meta, alterados


# ──────────────────────────────────────────────
# Listagem de ficheiros
# ──────────────────────────────────────────────

def listar_markdowns(alvo: Path, recursivo: bool) -> list[Path]:
    if alvo.is_file():
        return [alvo] if alvo.suffix.lower() == ".md" else []
    padrao = "**/*.md" if recursivo else "*.md"
    return [
        c for c in sorted(alvo.glob(padrao))
        if not any(parte in PASTAS_IGNORADAS for parte in c.parts)
    ]


def salvar_backup(caminho: Path, raiz_backup: Path) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = raiz_backup / f"{caminho.stem}.bak-catalogar-{timestamp}{caminho.suffix}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(caminho, destino)


def mostrar_previa(caminho: Path, meta: dict, alterados: list[str]) -> None:
    print(f"\n--- {caminho}")
    print(f"campos novos: {', '.join(alterados) if alterados else 'nenhum'}")
    print(despejar_frontmatter(meta))


# ──────────────────────────────────────────────
# Entrada
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera ou atualiza YAML bibliográfico em Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("alvo", type=Path, help="Arquivo .md ou pasta")
    parser.add_argument("--aplicar", action="store_true", help="Escreve as alterações")
    parser.add_argument("--forcar", action="store_true", help="Recalcula metadados existentes")
    parser.add_argument("--recursivo", action="store_true", help="Procura .md recursivamente")
    parser.add_argument("--limite", type=int, default=0, help="Processa apenas N arquivos")
    parser.add_argument("--sem-backup", action="store_true", help="Não cria backup")
    parser.add_argument("--autores", type=Path, default=None,
                        help="Caminho para autores.yaml (padrão: junto ao script)")
    args = parser.parse_args()

    autores_simples, autores_compostos = carregar_autores(args.autores)
    print(f"Autores carregados: {len(autores_simples)} simples, {len(autores_compostos)} compostos")

    arquivos = listar_markdowns(args.alvo, recursivo=args.recursivo)
    if args.limite:
        arquivos = arquivos[: args.limite]
    if not arquivos:
        raise SystemExit(f"Nenhum arquivo .md encontrado em: {args.alvo}")

    backup_dir = Path("rato/backups/catalogar")
    escritos = previews = 0

    for caminho in arquivos:
        corpo, original, meta, alterados = catalogar_arquivo(
            caminho, forcar=args.forcar,
            autores_simples=autores_simples,
            autores_compostos=autores_compostos,
        )
        if meta == original:
            continue

        if args.aplicar:
            if not args.sem_backup:
                salvar_backup(caminho, backup_dir)
            caminho.write_text(escrever_com_frontmatter(meta, corpo), encoding="utf-8")
            escritos += 1
            print(f"catalogado: {caminho}")
        else:
            mostrar_previa(caminho, meta, alterados)
            previews += 1

    if args.aplicar:
        print(f"\nArquivos atualizados: {escritos}")
        if escritos and not args.sem_backup:
            print(f"Backups em: {backup_dir.resolve()}")
    else:
        print(f"\nPrévia: {previews} arquivo(s) com alterações possíveis. Use --aplicar para escrever.")


if __name__ == "__main__":
    main()
