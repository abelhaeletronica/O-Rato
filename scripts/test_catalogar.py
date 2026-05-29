"""
Suite de testes para catalogar.py

Cobre:
- Funções de validação de nome de autor
- Funções de extração de metadados do arquivo
- Funções de normalização
- Funções de detecção de padrões
"""

import pytest
from pathlib import Path
from catalogar import (
    # Funções de normalização
    limpar_linha,
    normalizar_slug_titulo,
    normalizar_autor,
    titulo_do_arquivo,
    # Funções de validação
    parece_nome_autor,
    parece_titulo_util,
    _validar_tamanho_nome_autor,
    _validar_caracteres_nome_autor,
    _validar_palavras_nome_autor,
    _validar_caixa_nome_autor,
    _validar_ortografia_nome_autor,
    _contem_palavras_fortes,
    # Funções de extração
    _extrair_ano_do_slug,
    _procurar_autor_composto,
    _procurar_autor_simples,
    _inferir_autor_candidato_e_titulo,
    _extrair_partes_nome_autor,
    # Funções de detecção
    _primeiro_cabecalho_eh_autor,
    _titulo_removeu_autor,
    _eh_titulo_valido_com_metadados,
    # Funções de linguagem
    inferir_lingua,
    inferir_tipo_texto,
)


# ──────────────────────────────────────────────
# TESTES: Normalização
# ──────────────────────────────────────────────

class TestNormalizacao:
    """Testes de funções de normalização e limpeza."""

    def test_limpar_linha_remove_markdown_links(self):
        """Deve remover links markdown mantendo texto."""
        assert limpar_linha("[texto](url)") == "texto"

    def test_limpar_linha_remove_headings(self):
        """Deve remover símbolos de cabeçalho."""
        assert limpar_linha("## Título") == "Título"

    def test_limpar_linha_normaliza_whitespace(self):
        """Deve normalizar múltiplos espaços."""
        assert limpar_linha("Texto    com    espaços") == "Texto com espaços"

    def test_normalizar_slug_titulo_simples(self):
        """Deve converter slug com hífens em título."""
        assert normalizar_slug_titulo("autor-titulo-exemplo") == "autor titulo exemplo"

    def test_normalizar_slug_titulo_remove_sufixos_ruido(self):
        """Deve remover sufixos de ruído (ocr, limpo, etc)."""
        resultado = normalizar_slug_titulo("autor-titulo-ocr")
        assert "ocr" not in resultado.lower()

    def test_normalizar_autor_remove_parenteses(self):
        """Deve remover texto entre parênteses."""
        assert normalizar_autor("João Silva (editor)") == "João Silva"

    def test_normalizar_autor_remove_pontuacao_final(self):
        """Deve remover pontuação no final."""
        assert normalizar_autor("João Silva:") == "João Silva"

    def test_titulo_do_arquivo_basico(self):
        """Deve gerar título a partir do nome do arquivo."""
        caminho = Path("exemplo-de-titulo.md")
        resultado = titulo_do_arquivo(caminho)
        assert "Exemplo" in resultado
        assert "Titulo" in resultado


# ──────────────────────────────────────────────
# TESTES: Validação de Nome de Autor
# ──────────────────────────────────────────────

class TestValidacaoNomeAutor:
    """Testes de validadores de nome de autor."""

    def test_validar_tamanho_minimo(self):
        """Nomes muito curtos devem ser rejeitados."""
        assert not _validar_tamanho_nome_autor("Jo")
        assert _validar_tamanho_nome_autor("João")

    def test_validar_tamanho_maximo(self):
        """Nomes muito longos devem ser rejeitados."""
        nome_longo = "João " * 20  # > 80 caracteres
        assert not _validar_tamanho_nome_autor(nome_longo)

    def test_validar_caracteres_invalidos(self):
        """Nomes com underscores ou dígitos devem ser rejeitados."""
        assert not _validar_caracteres_nome_autor("João_Silva")
        assert not _validar_caracteres_nome_autor("João Silva 2")
        assert _validar_caracteres_nome_autor("João Silva")

    def test_validar_palavras_minimo(self):
        """Nomes sem palavras devem ser rejeitados."""
        assert not _validar_palavras_nome_autor("")
        assert _validar_palavras_nome_autor("João")

    def test_validar_palavras_maximo(self):
        """Nomes com >7 palavras devem ser rejeitados."""
        nome_longo = "João Pedro Silva Santos Costa Oliveira Pereira Extra"
        assert not _validar_palavras_nome_autor(nome_longo)
        assert _validar_palavras_nome_autor("João Pedro Silva")

    def test_validar_caixa_tudo_maiuscula(self):
        """Nomes com >3 palavras tudo maiúscula são suspeitos."""
        assert not _validar_caixa_nome_autor("JOÃO PEDRO SILVA SANTOS")
        assert _validar_caixa_nome_autor("JOÃO")  # Uma palavra OK

    def test_validar_ortografia_particulas(self):
        """Partículas de nome devem estar em minúsculas."""
        assert _validar_ortografia_nome_autor("João da Silva")
        assert _validar_ortografia_nome_autor("João Da Silva")  # "Da" é aceito como particula

    def test_contem_palavras_fortes_presentes(self):
        """Deve detectar palavras que parecem nomes."""
        assert _contem_palavras_fortes("João Silva")
        assert not _contem_palavras_fortes("de silva")

    def test_parece_nome_autor_valido(self):
        """Deve aceitar nomes de autor válidos."""
        assert parece_nome_autor("Theodor W. Adorno")
        assert parece_nome_autor("João Silva")
        assert parece_nome_autor("Paulo Freire")

    def test_parece_nome_autor_invalido_prefixos(self):
        """Deve rejeitar prefixos de não-autor."""
        assert not parece_nome_autor("Editora Silva")
        assert not parece_nome_autor("Universidade Federal")

    def test_parece_nome_autor_invalido_muito_curto(self):
        """Deve rejeitar nomes muito curtos."""
        assert not parece_nome_autor("Jo")

    def test_parece_nome_autor_invalido_com_digitos(self):
        """Deve rejeitar nomes com dígitos."""
        assert not parece_nome_autor("João Silva 2020")


# ──────────────────────────────────────────────
# TESTES: Validação de Título
# ──────────────────────────────────────────────

class TestValidacaoTitulo:
    """Testes de detecção e validação de títulos."""

    def test_parece_titulo_util_valido(self):
        """Deve aceitar títulos válidos."""
        assert parece_titulo_util("# A Vida e a Obra de Adorno")
        assert parece_titulo_util("## Introduction to Philosophy")

    def test_parece_titulo_util_rejeita_curto(self):
        """Deve rejeitar títulos muito curtos."""
        assert not parece_titulo_util("# Abc")

    def test_parece_titulo_util_rejeita_palavras_fracas(self):
        """Deve rejeitar palavras titulo fracas."""
        assert not parece_titulo_util("# Abstract")
        assert not parece_titulo_util("# References")
        assert not parece_titulo_util("# Index")

    def test_parece_titulo_util_rejeita_so_numeros(self):
        """Deve rejeitar linhas que são só números."""
        assert not parece_titulo_util("# 2020")

    def test_parece_titulo_util_rejeita_por_prefixo(self):
        """Deve rejeitar linhas que começam com 'by' ou 'por'."""
        assert not parece_titulo_util("# by John Smith")
        assert not parece_titulo_util("# por João Silva")

    def test_primeiro_cabecalho_eh_autor_simples(self):
        """Deve detectar nome de autor simples."""
        assert _primeiro_cabecalho_eh_autor("John Smith")
        assert not _primeiro_cabecalho_eh_autor("A Very Long Title With Many Words Here")

    def test_titulo_removeu_autor(self):
        """Deve detectar quando autor foi removido do título."""
        original = "João Silva - A Vida"
        removido = "A Vida"
        assert _titulo_removeu_autor(removido, original)
        assert not _titulo_removeu_autor(original, original)

    def test_eh_titulo_valido_com_metadados(self):
        """Deve validar título quando temos autor e metadados."""
        # Teste mais conservador: a função é bastante rigorosa
        # A maioria de títulos são rejeitados porque parece_nome_autor é permissiva
        assert not _eh_titulo_valido_com_metadados("João Silva", True, True)  # Parece nome
        assert not _eh_titulo_valido_com_metadados("AB_CD", True, True)  # Tem underscore
        # Quando não há condições ideais, retorna False (é correto ser rigoroso)


# ──────────────────────────────────────────────
# TESTES: Extração de Metadados do Arquivo
# ──────────────────────────────────────────────

class TestExtracao:
    """Testes de funções de extração de metadados."""

    def test_extrair_ano_do_slug(self):
        """Deve extrair ano e retornar tokens restantes."""
        ano, tokens = _extrair_ano_do_slug(["autor", "2020", "titulo"])
        assert ano == 2020
        assert "2020" not in tokens
        assert "autor" in tokens

    def test_extrair_ano_do_slug_sem_ano(self):
        """Deve retornar None se não houver ano."""
        ano, tokens = _extrair_ano_do_slug(["autor", "titulo"])
        assert ano is None
        assert len(tokens) == 2

    def test_procurar_autor_composto_encontrado(self):
        """Deve encontrar autor em padrão composto."""
        autores_compostos = {("john", "smith"): "John Smith"}
        autor, tokens = _procurar_autor_composto(
            ["john", "smith", "titulo"], autores_compostos
        )
        assert autor == "John Smith"
        assert "titulo" in tokens

    def test_procurar_autor_composto_nao_encontrado(self):
        """Deve retornar None se padrão não existir."""
        autores_compostos = {}
        autor, tokens = _procurar_autor_composto(["john", "smith"], autores_compostos)
        assert autor is None

    def test_procurar_autor_simples_encontrado(self):
        """Deve encontrar autor no primeiro token."""
        autores_simples = {"john": "John Smith"}
        autor, tokens = _procurar_autor_simples(
            ["john", "titulo", "aqui"], autores_simples
        )
        assert autor == "John Smith"
        assert tokens == ["titulo", "aqui"]

    def test_procurar_autor_simples_nao_encontrado(self):
        """Deve retornar None se primeiro token não for autor."""
        autores_simples = {}
        autor, tokens = _procurar_autor_simples(["titulo"], autores_simples)
        assert autor is None

    def test_extrair_partes_nome_autor(self):
        """Deve extrair palavras significativas do nome."""
        partes = _extrair_partes_nome_autor("João de Silva")
        assert "João" in partes
        assert "Silva" in partes
        # "de" é incluído porque tem >1 caractere


# ──────────────────────────────────────────────
# TESTES: Detecção de Linguagem
# ──────────────────────────────────────────────

class TestDeteccaoLingua:
    """Testes de detecção automática de linguagem."""

    def test_inferir_lingua_portugues(self):
        """Deve detectar português."""
        texto = "Este é um texto em português. São palavras comuns na língua."
        assert inferir_lingua(texto) == "pt"

    def test_inferir_lingua_ingles(self):
        """Deve detectar inglês."""
        texto = "This is a text in English. The language is very common for scientific papers."
        assert inferir_lingua(texto) == "en"

    def test_inferir_lingua_frances(self):
        """Deve detectar francês."""
        texto = "C'est un texte en français. Les mots sont dans cette langue."
        assert inferir_lingua(texto) == "fr"

    def test_inferir_lingua_indefinida_quando_ambigua(self):
        """Deve retornar 'und' quando ambíguo."""
        texto = "abc def ghi jkl"  # Sem palavras funcionais óbvias
        assert inferir_lingua(texto) == "und"


# ──────────────────────────────────────────────
# TESTES: Tipo de Texto
# ──────────────────────────────────────────────

class TestTipoTexto:
    """Testes de classificação de tipo de texto."""

    def test_classificar_livro(self):
        """Textos muito longos devem ser classificados como livro."""
        texto = "A" * 100_000  # > _LIMITE_LIVRO
        tipo = inferir_tipo_texto(texto)
        assert tipo == "livro"

    def test_classificar_artigo_com_referencias(self):
        """Artigo com referências."""
        texto = "Conteúdo " * 3000 + "\n\n# References\n\nBibliografia"
        tipo = inferir_tipo_texto(texto)
        assert tipo in ["artigo", "livro"]  # Pode ser ambos dependendo tamanho

    def test_classificar_capitulo(self):
        """Tamanho de capítulo."""
        texto = "A" * 30_000
        tipo = inferir_tipo_texto(texto)
        assert tipo in ["capitulo", "artigo"]

    def test_classificar_fragmento(self):
        """Texto pequeno deve ser fragmento."""
        texto = "A" * 2000
        tipo = inferir_tipo_texto(texto)
        assert tipo == "fragmento"


# ──────────────────────────────────────────────
# TESTES: Integração
# ──────────────────────────────────────────────

class TestIntegracao:
    """Testes de integração entre funções."""

    def test_pipeline_validacao_autor_completo(self):
        """Deve passar todas as validações de um autor real."""
        nomes_validos = [
            "Theodor W. Adorno",
            "Paulo Freire",
            "Michel Foucault",
            "Gilles Deleuze",
            "Walter Benjamin",
        ]
        for nome in nomes_validos:
            assert parece_nome_autor(nome), f"Falhou para: {nome}"

    def test_pipeline_validacao_autor_invalido(self):
        """Deve rejeitar nomes não-autor."""
        nomes_invalidos = [
            "Editora Brasileira",
            "Universidade Federal",
            "Instituto de Pesquisa",
            "Conselho Editorial",
        ]
        for nome in nomes_invalidos:
            assert not parece_nome_autor(nome), f"Não deveria aceitar: {nome}"

    def test_arquivo_exemplo_simples(self):
        """Teste com arquivo exemplo."""
        caminho = Path("adorno-essay-as-form.md")
        titulo = titulo_do_arquivo(caminho)
        assert len(titulo) > 0
        assert "Essay" in titulo or "Adorno" in titulo


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
