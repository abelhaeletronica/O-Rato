#!/usr/bin/env python3
"""
Escavar — condições da abstração a partir de leituras brutas
============================================================
Uso:
    python3 rato/scripts/escavar.py --arquivo nome-do-texto.md
    python3 rato/scripts/escavar.py --leitura leituras-brutas/LEITURA_nome-do-texto.md

Este script não relê o documento original. Ele trabalha exclusivamente sobre
LEITURA_*.md, tratando a leitura bruta como vestígio produzido pelo roer.py.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import requests
import yaml


OLLAMA_URL = "http://localhost:11434/api/generate"
TIMEOUT_SEGUNDOS = 600


PROMPT_ESCAVAR = """\
Você está lendo rastros produzidos por outro agente a partir de um texto chamado "{titulo}".

Use exclusivamente a LEITURA-BRUTA abaixo.
Não releia nem reconstitua o documento original.
Não use conhecimento externo.
Não resuma o texto.
Não critique.
Não avalie.

A pergunta única deste exercício é:
"O que este texto precisou suspender para conseguir dizer o que diz?"

Trabalhe de modo fenomenológico: observe as condições que tornam o texto possível.
O objetivo é perceber escolhas de estabilização, suspensão, controle, escala e recorte.

Prefira formulações deste tipo:
- o estudo estabiliza...
- o modelo precisa reduzir...
- o experimento controla...
- o texto deixa fora do recorte...
- permanece suspenso...
- não entra nesta escala de observação...
- o experimento não acompanha...
- o modelo não torna visível...
- a leitura disponível não permite observar...

Não use formulações em que o texto aparece como se tivesse corpo ou agência material.
Evite especialmente expressões como "o texto deixa de resistir", "o texto resiste", "o artigo falha", "o estudo ignora" ou "é problemático".
Quando precisar nomear uma ausência, prefira: "fica fora do recorte", "não entra nesta escala", "não se torna visível nos rastros disponíveis" ou "permanece suspenso".

Observe especialmente:
- o que o texto precisou estabilizar;
- o que precisou suspender;
- o que precisou controlar;
- que escala escolheu;
- que corpo desapareceu;
- que resistências do material deixam de se tornar visíveis;
- que fenômeno precisou simplificar.

Separe o que aparece nos rastros e o que é inferência do leitor.
Quando algo for inferência, marque como hipótese e indique GRAU-DE-CONFIANÇA.
Não transforme suspensão em defeito; trate cada suspensão como condição de operação do texto.

Formato obrigatório:

CONDICOES-DA-ABSTRACAO:

ESTABILIZACOES:
elementos que o texto estabiliza para conseguir operar, com [Parte N]

SUSPENSOES:
dimensões materiais, corporais, ambientais, históricas, técnicas ou sensoriais que ficam fora do recorte ou não entram nesta escala de observação, com [Parte N]

CONTROLES-E-ESCALAS:
controles, escalas, modelos, materiais, corpus ou procedimentos que tornam o argumento ou experimento possível, com [Parte N]

CORPOS-E-MATERIAIS-SUSPENSOS:
corpos, gestos, resistências materiais, variações ambientais ou comportamentos do material que não se tornam plenamente visíveis nos rastros disponíveis, com [Parte N]

INFERENCIAS-DO-LEITOR:
aproximações que não estão explicitamente nos rastros, sempre marcadas como hipótese e com GRAU-DE-CONFIANÇA

CORRESPONDENCIAS-EXPERIMENTAIS:
experimentos concretos capazes de recolocar as abstrações observadas em contato com o mundo material.
Evite formulações genéricas.
Prefira variações materiais, técnicas ou corporais precisas, como alterar granulometria, variar umidade, retirar estabilizantes, comparar fabricação digital e manual, modificar composição do solo, mudar escala de impressão, observar comportamento sem aditivos ou testar condições ambientais diferentes.

---
LEITURA-BRUTA:
{leitura_bruta}
"""


def extrair_frontmatter(texto: str) -> tuple[dict, str]:
    if texto.startswith("---"):
        partes = texto.split("---", 2)
        if len(partes) >= 3:
            try:
                return yaml.safe_load(partes[1]) or {}, partes[2].strip()
            except yaml.YAMLError:
                pass
    return {}, texto.strip()


def resolver_leitura(args: argparse.Namespace) -> Path:
    if args.leitura:
        return Path(args.leitura)

    nome = args.arquivo
    if not nome:
        raise ValueError("informe --leitura ou --arquivo")

    stem = Path(nome).stem
    if stem.startswith("LEITURA_"):
        arquivo = f"{stem}.md"
    else:
        arquivo = f"LEITURA_{stem}.md"
    return Path(args.leituras) / arquivo


def verificar_ollama(modelo: str) -> bool:
    try:
        resposta = requests.get("http://localhost:11434/api/tags", timeout=5)
        modelos = [m["name"] for m in resposta.json().get("models", [])]
        if not any(modelo in m for m in modelos):
            print(f"⚠️  Modelo '{modelo}' não encontrado. Modelos disponíveis: {modelos}")
            print(f"   Execute: ollama pull {modelo}")
            return False
        return True
    except requests.ConnectionError:
        print("❌ Ollama não está rodando. Execute: ollama serve")
        return False


def chamar_ollama(prompt: str, modelo: str) -> str:
    payload = {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": 2200,
            "num_ctx": 16384,
        },
    }
    resposta = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_SEGUNDOS)
    resposta.raise_for_status()
    return resposta.json().get("response", "").strip()


def montar_saida(meta: dict, leitura_path: Path, conteudo: str, modelo: str) -> str:
    titulo = meta.get("titulo") or meta.get("title") or leitura_path.stem.replace("LEITURA_", "").replace("-", " ").title()
    frontmatter = {
        "titulo": titulo,
        "arquivo-leitura-bruta": leitura_path.name,
        "tipo": "condicoes-abstracao",
        "data-escavacao": datetime.now().strftime("%Y-%m-%d"),
        "modelo-ollama": modelo,
        "revisao-humana": False,
    }
    for campo in ("arquivo-original", "author", "authors", "year", "doi", "url", "journal", "instituicao"):
        if campo in meta:
            frontmatter[campo] = meta[campo]

    yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"""---
{yaml_str}---

# Condições da abstração · {titulo}

> Leitura experimental gerada a partir da leitura bruta · {datetime.now().strftime("%Y-%m-%d")}
> Leitura bruta: `{leitura_path.name}`

---

{conteudo.strip()}
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera condições da abstração exclusivamente a partir de uma leitura bruta do roer.py"
    )
    parser.add_argument("--leitura", default="", help="Caminho para um arquivo LEITURA_*.md")
    parser.add_argument("--arquivo", default="", help="Nome original ou stem usado apenas para localizar LEITURA_*.md")
    parser.add_argument("--leituras", default="leituras-brutas", help="Pasta das leituras brutas")
    parser.add_argument("--saida", default="escavacoes", help="Pasta de saída")
    parser.add_argument("--modelo", default="qwen3:14b", help="Modelo Ollama")
    parser.add_argument("--forcar", action="store_true", help="Sobrescreve escavação existente")
    args = parser.parse_args()

    leitura_path = resolver_leitura(args)
    if not leitura_path.exists():
        print(f"❌ Leitura bruta não encontrada: {leitura_path}")
        return

    if not verificar_ollama(args.modelo):
        return

    texto = leitura_path.read_text(encoding="utf-8", errors="replace")
    meta, corpo = extrair_frontmatter(texto)
    titulo = meta.get("titulo") or meta.get("title") or leitura_path.stem.replace("LEITURA_", "").replace("-", " ").title()

    pasta_saida = Path(args.saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    stem = leitura_path.stem.replace("LEITURA_", "")
    saida_path = pasta_saida / f"ESCAVACAO_{stem}.md"
    if saida_path.exists() and not args.forcar:
        print(f"⏭️  Escavação já existe: {saida_path}")
        return

    prompt = PROMPT_ESCAVAR.format(titulo=titulo, leitura_bruta=corpo)
    conteudo = chamar_ollama(prompt, args.modelo)
    saida_path.write_text(montar_saida(meta, leitura_path, conteudo, args.modelo), encoding="utf-8")

    print(f"✅ Escavação salva em: {saida_path.resolve()}")


if __name__ == "__main__":
    main()
