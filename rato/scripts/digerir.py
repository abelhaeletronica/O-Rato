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
COSTURAS_DIR = FICHAS_DIR / "costuras"

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


def gerar_prompt_costura(
    temas_persistentes: str,
    perguntas_orfas: str,
    recortes_texto: str,
) -> str:
    """
    Monta o prompt para o Ollama em modo Costura.
    """
    return f"""Você é o Rato, operando no modo COSTURA.
Você NÃO é um resumidor.

Você é um pesquisador que acaba de incorporar um novo texto à sua biblioteca.
Seu trabalho é registrar o efeito dessa leitura sobre a pesquisa.
Pense como alguém que escreve um diário intelectual.

A pergunta central é:
"O que mudou depois desta leitura?"

Não responda:
"Com o que este texto se parece?"

A Costura deve registrar o encontro entre um texto e uma pesquisa.
Ela não deve produzir um resumo do artigo.

REGRAS DE INTERPRETAÇÃO:
- Nunca tente relacionar o texto com toda a biblioteca.
- Prefira poucas relações, mas profundamente justificadas.
- É melhor registrar três relações fortes do que quinze aproximações superficiais.
- Nunca estabeleça relações apenas por coincidência de palavras.
- Toda aproximação deve ser filosoficamente justificável.
- Sempre explique por que uma relação existe.
- Se não existir relação suficiente, diga explicitamente que ela ainda não apareceu.
- Evite listas extensas de conceitos.
- Priorize deslocamentos conceituais.
- Não invente relações.
- Não conecte automaticamente todos os eixos da biblioteca.
- Evite linguagem burocrática.
- Prefira parágrafos curtos.
- Sempre privilegie qualidade interpretativa em vez de quantidade.
- Escreva como um pesquisador registrando um deslocamento intelectual.

---
SINAIS DA BIBLIOTECA, APENAS COMO CONTEXTO:

Temas persistentes já detectados:
{temas_persistentes}

Perguntas órfãs ou recorrentes:
{perguntas_orfas}
---

NOVO TEXTO A INCORPORAR:
{recortes_texto}

Use os sinais da biblioteca apenas quando houver relação forte com o novo texto.
Não use esses sinais como lista obrigatória de eixos a conectar.

Gere a costura exatamente com a estrutura abaixo:

## O que este texto muda?
Explique em um pequeno texto qual deslocamento intelectual este artigo provoca.
Qual pergunta passa a ser mais importante depois da leitura?
Não resuma o artigo.
Descreva a reorganização produzida pela leitura.

---

## Ressonâncias
Liste apenas entre 3 e 5 autores, conceitos ou fichas da biblioteca que passam a ser vistos de outra maneira.

Para cada item explique:
- por que houve ressonância;
- o que mudou na leitura desses autores.

Não use similaridade superficial.

---

## Tensões
Quais problemas permanecem abertos?
O que o artigo não resolve?
Onde ele encontra seus próprios limites?
Se possível formule essas tensões como perguntas de pesquisa.

---

## Contribuição para a pesquisa
Explique por que este texto merece continuar na biblioteca.
Que contribuição específica ele oferece ao projeto?
Evite frases genéricas.

---

## Grau de perturbação
Avalie de ★☆☆☆☆ até ★★★★★.
Não significa qualidade do artigo.
Significa quanto este texto reorganizou a pesquisa.
Justifique em poucas linhas.

---

## Vestígio
Finalize com apenas uma frase.
Essa frase deve responder:
"Se daqui a dois anos eu esquecer este artigo, o que não posso esquecer sobre ele?"

Essa frase deve ser memorável.
Não faça um slogan.
Não faça um resumo.
Registre apenas o principal vestígio intelectual deixado pela leitura."""


def gerar_prompt_comparacao_costuras(costuras_texto: str) -> str:
    """
    Monta o prompt para comparar costuras de um mesmo artigo ao longo do tempo.
    """
    return f"""Você é o Rato, operando no modo COMPARAR COSTURAS.

Você não está comparando versões de um artigo.
Você está comparando versões de um pesquisador.
Seu trabalho é reconstruir a evolução da pesquisa.

O artigo permaneceu o mesmo.
Quem mudou foi o pesquisador.

Nunca diga apenas que "uma versão acrescentou detalhes".
Procure deslocamentos conceituais.
Observe mudanças de perguntas.
Observe mudanças de interesses.
Observe mudanças de problemas.
Observe mudanças de linguagem.
Observe mudanças de prioridades.

REGRAS:
- Nunca compare a qualidade das costuras.
- Nunca julgue versões antigas.
- Toda interpretação deve considerar que a mudança pode representar amadurecimento da pesquisa.
- Evite frases genéricas.
- Valorize mudanças de perguntas mais do que mudanças de respostas.
- Escreva como um pesquisador reconstruindo sua própria trajetória intelectual.
- Não interprete o artigo diretamente; interprete a evolução das leituras.

---
COSTURAS A COMPARAR:
{costuras_texto}
---

Gere a comparação exatamente com a estrutura abaixo:

# Trajetória interpretativa
Descreva como a leitura evoluiu ao longo do tempo.
Não faça uma lista cronológica.
Conte uma pequena história intelectual.

---

# Permanências
Quais ideias permaneceram constantes em todas as costuras?
O que nunca deixou de ser importante?

---

# Deslocamentos
Quais conceitos perderam importância?
Quais conceitos ganharam importância?
Quais perguntas desapareceram?
Quais perguntas surgiram?

---

# Momentos de inflexão
Houve alguma mudança significativa de direção?
Identifique os momentos em que a interpretação mudou.
Explique por quê.

---

# Evolução da pesquisa
O que essa sequência revela sobre a evolução da pesquisa?
Não apenas sobre o artigo.

---

# Próximo passo
Qual parece ser a próxima pergunta natural da pesquisa?

---

# Vestígio da trajetória
Escreva um pequeno parágrafo respondendo:
"O que mudou em mim depois de reler este texto tantas vezes?"

Não responda como um resumo.
Responda como um diário intelectual."""


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
modo: digestao
modelo: {OLLAMA_MODEL}
---

"""

    arquivo.write_text(frontmatter + conteudo, encoding="utf-8")
    return arquivo


def salvar_costura(conteudo: str, arquivos: list[str]) -> Path:
    """
    Salva a costura em fichas/costuras/costura_AAAA-MM-DD_stem.md
    com frontmatter apropriado.
    """
    COSTURAS_DIR.mkdir(parents=True, exist_ok=True)

    data_hoje = datetime.now().strftime("%Y-%m-%d")
    sufixo = f"_{Path(arquivos[0]).stem}" if arquivos else ""
    arquivo = COSTURAS_DIR / f"costura_{data_hoje}{sufixo}.md"

    titulo = f"Costura: {data_hoje}"
    if arquivos:
        titulo = f"{titulo} - {Path(arquivos[0]).stem}"

    frontmatter = f"""---
titulo: "{titulo}"
tipo: costura
data: "{data_hoje}"
modo: costura
modelo: {OLLAMA_MODEL}
---

"""

    arquivo.write_text(frontmatter + conteudo, encoding="utf-8")
    return arquivo


def salvar_comparacao_costuras(conteudo: str, arquivos: list[str]) -> Path:
    """
    Salva a comparação de costuras em fichas/costuras/.
    """
    COSTURAS_DIR.mkdir(parents=True, exist_ok=True)

    data_hoje = datetime.now().strftime("%Y-%m-%d")
    sufixo = f"_{Path(arquivos[0]).stem}" if arquivos else ""
    arquivo = COSTURAS_DIR / f"comparacao-costuras_{data_hoje}{sufixo}.md"

    titulo = f"Comparação de costuras: {data_hoje}"
    if arquivos:
        titulo = f"{titulo} - {Path(arquivos[0]).stem}"

    frontmatter = f"""---
titulo: "{titulo}"
tipo: comparacao-costuras
data: "{data_hoje}"
modo: comparar-costuras
modelo: {OLLAMA_MODEL}
---

"""

    arquivo.write_text(frontmatter + conteudo, encoding="utf-8")
    return arquivo


def main() -> None:
    """
    Orquestra o fluxo completo de digestão, costura ou comparação de costuras.
    """
    parser = argparse.ArgumentParser(
        description="Rato em modo Digestão, Costura ou Comparação de Costuras."
    )
    parser.add_argument(
        "--modo",
        choices=["digestao", "costura", "comparar-costuras"],
        default="digestao",
        help=(
            "Modo de operação: digestao, costura ou comparar-costuras "
            "(padrão: digestao)"
        ),
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
        help="Salvar resultado em fichas/digestoes/ ou fichas/costuras/",
    )

    args = parser.parse_args()

    if args.modo == "comparar-costuras" and not args.arquivos:
        parser.error("--modo comparar-costuras exige --arquivos")

    if args.modo == "comparar-costuras":
        print("🧵 Rato em Modo Comparar Costuras...", file=sys.stderr)
    elif args.modo == "costura":
        print("🧵 Rato em Modo Costura...", file=sys.stderr)
        if not args.arquivos:
            print(
                "⚠ Aviso: modo costura funciona melhor com --arquivos",
                file=sys.stderr,
            )
    else:
        print("🐭 Rato em Modo Digestão...", file=sys.stderr)

    if args.modo == "comparar-costuras":
        print("   [1/3] Carregando costuras...", file=sys.stderr)
        recortes_texto = carregar_arquivos(args.arquivos)

        print("   [2/3] Montando comparação interpretativa...", file=sys.stderr)
        prompt = gerar_prompt_comparacao_costuras(recortes_texto)

        print("   [3/3] Consultando Ollama qwen3:14b...", file=sys.stderr)
        resultado = chamar_ollama(prompt)

        if resultado is None:
            sys.exit(1)

        if args.salvar:
            arquivo = salvar_comparacao_costuras(resultado, args.arquivos)
            print(f"\n✓ Comparação de costuras salva em: {arquivo}", file=sys.stderr)
        else:
            delim = "═" * 80
            print(f"\n{delim}")
            print(resultado)
            print(f"{delim}\n")
        return

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
    if args.modo == "costura":
        prompt = gerar_prompt_costura(
            temas_persistentes,
            perguntas_orfas,
            recortes_texto,
        )
    else:
        prompt = gerar_prompt_digestao(
            temas_persistentes,
            perguntas_orfas,
            recortes_texto,
        )

    # Chama Ollama
    print("   [4/4] Consultando Ollama qwen3:14b...", file=sys.stderr)
    resultado = chamar_ollama(prompt)

    if resultado is None:
        sys.exit(1)

    # Output e salvamento
    if args.salvar:
        if args.modo == "costura":
            arquivo = salvar_costura(resultado, args.arquivos)
            print(f"\n✓ Costura salva em: {arquivo}", file=sys.stderr)
        else:
            arquivo = salvar_digestao(resultado)
            print(f"\n✓ Digestão salva em: {arquivo}", file=sys.stderr)
    else:
        # Print com delimitadores
        delim = "═" * 80
        print(f"\n{delim}")
        print(resultado)
        print(f"{delim}\n")


if __name__ == "__main__":
    main()
