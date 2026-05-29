#!/usr/bin/env python3
"""Higieniza, normaliza e corrige memoria-conceitos.json."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


AUTORES_FALSOS = {
    "ARGUMENTO-DO-AUTOR",
    "AUTORES-OBRAS",
    "Apple",
    "Apple's",
    "Arcades",
    "Assessment",
    "Asexplored",
    "Bangladesh",
    "Base",
}


def autor_suspeito(nome: str) -> bool:
    nome = str(nome).strip()
    if not nome:
        return True
    if nome in AUTORES_FALSOS or nome.isupper() or nome.endswith("'s"):
        return True
    if nome.lower().startswith(("#", "capítulo", "capitulo", "textos originais")):
        return True
    if any(c in nome for c in ['"', "“", "”", "‘", "’"]):
        return True
    if len(nome) > 80:
        return True
    if nome.endswith(".") and len(nome.split()) > 3:
        return True
    indicadores_frase = (" may be ", " pode ser ", " é ", " são ", " foram ", " is ", " are ")
    if any(indicador in f" {nome.lower()} " for indicador in indicadores_frase):
        return True
    if len(nome.split()) > 5:
        return True
    return False


def normalizar_entrada(entrada: dict) -> None:
    arquivos = list(dict.fromkeys(entrada.get("arquivos", [])))
    historico = entrada.get("historico", [])

    historico_unico = []
    vistos = set()
    for item in historico:
        chave = (item.get("data"), item.get("arquivo"))
        if chave in vistos:
            continue
        vistos.add(chave)
        item.setdefault("grau_de_rastreabilidade", entrada.get("grau_de_rastreabilidade", "médio"))
        historico_unico.append(item)

    entrada["arquivos"] = arquivos[:20]
    entrada["historico"] = historico_unico[-12:]
    entrada["ocorrencias_documentos"] = int(entrada.get("ocorrencias_documentos") or len(arquivos))
    entrada["ocorrencias_chunks"] = int(entrada.get("ocorrencias_chunks") or entrada.get("ocorrencias", 0))
    entrada["ocorrencias"] = entrada["ocorrencias_documentos"]
    entrada.setdefault("grau_de_rastreabilidade", "médio")


def higienizar_memoria(memoria: dict) -> list[str]:
    alterados = []

    for conceito, entrada in memoria.get("conceitos", {}).items():
        autores = entrada.get("autores", [])
        novos = []
        for autor in autores:
            if autor_suspeito(autor):
                continue
            if autor not in novos:
                novos.append(autor)
        if novos != autores:
            entrada["autores"] = novos[:12]
            alterados.append(conceito)
        normalizar_entrada(entrada)

    return alterados


def valor_aplicavel(correcoes: dict, chave: str, aplicar_vazios: bool) -> bool:
    if chave not in correcoes:
        return False
    valor = correcoes[chave]
    return aplicar_vazios or bool(valor)


def aplicar_correcoes(memoria: dict, correcoes_path: Path,
                      aplicar_vazios: bool = False) -> int:
    if not correcoes_path.exists():
        print(f"Arquivo de correções não encontrado: {correcoes_path}")
        return 0

    correcoes = json.loads(correcoes_path.read_text(encoding="utf-8"))
    conceitos = memoria.setdefault("conceitos", {})
    alteracoes = 0

    for nome, dados in correcoes.get("conceitos_corrigidos", {}).items():
        if nome in conceitos:
            entrada = conceitos[nome]
            autores_novos = dados.get("autores", [])
            relacoes_novas = dados.get("relacoes", [])

            if aplicar_vazios or autores_novos:
                entrada["autores"] = list(dict.fromkeys(autores_novos))[:12]
            if aplicar_vazios or relacoes_novas:
                relacoes_atuais = entrada.get("relacoes", [])
                entrada["relacoes"] = list(dict.fromkeys(relacoes_atuais + relacoes_novas))[:12]
            normalizar_entrada(entrada)
            print(f"Conceito corrigido: {nome}")
        else:
            conceitos[nome] = {
                "autores": list(dict.fromkeys(dados.get("autores", [])))[:12],
                "relacoes": list(dict.fromkeys(dados.get("relacoes", [])))[:12],
                "ocorrencias": 1,
                "ocorrencias_documentos": 1,
                "ocorrencias_chunks": 0,
                "arquivos": list(dict.fromkeys(dados.get("arquivos", [])))[:20],
                "historico": [],
                "grau_de_rastreabilidade": dados.get("grau_de_rastreabilidade", "médio"),
            }
            print(f"Conceito criado: {nome}")
        alteracoes += 1

    campos_lista = {
        "conceitos_recorrentes_sugeridos": "conceitos_recorrentes",
        "perguntas_abertas_sugeridas": "perguntas_abertas",
        "autores_recorrentes_sugeridos": "autores_recorrentes",
    }
    for origem, destino in campos_lista.items():
        if valor_aplicavel(correcoes, origem, aplicar_vazios):
            memoria[destino] = correcoes[origem]
            alteracoes += 1
            print(f"Campo atualizado: {destino} ({len(memoria[destino])} itens)")

    return alteracoes


def main() -> None:
    parser = argparse.ArgumentParser(description="Cuida da memória conceitual")
    parser.add_argument("--memoria", default="rato/memoria/memoria-conceitos.json")
    parser.add_argument("--correcoes", default="rato/memoria/correcoes-memoria.json")
    parser.add_argument("--aplicar-correcoes", action="store_true")
    parser.add_argument("--aplicar-vazios", action="store_true")
    parser.add_argument("--sem-backup", action="store_true")
    args = parser.parse_args()

    memoria_path = Path(args.memoria)
    correcoes_path = Path(args.correcoes)
    if not memoria_path.exists():
        raise SystemExit(f"Arquivo não encontrado: {memoria_path}")

    if not args.sem_backup:
        backup = memoria_path.with_suffix(
            f".backup-cuidar-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
        shutil.copy2(memoria_path, backup)
        print(f"Backup salvo em: {backup}")

    memoria = json.loads(memoria_path.read_text(encoding="utf-8"))
    alterados = higienizar_memoria(memoria)
    correcoes_aplicadas = 0

    if args.aplicar_correcoes:
        correcoes_aplicadas = aplicar_correcoes(
            memoria,
            correcoes_path,
            aplicar_vazios=args.aplicar_vazios,
        )

    memoria_path.write_text(
        json.dumps(memoria, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Conceitos alterados: {len(alterados)}")
    for conceito in alterados:
        print(f"- {conceito}")
    if args.aplicar_correcoes:
        print(f"Correções aplicadas: {correcoes_aplicadas}")


if __name__ == "__main__":
    main()
