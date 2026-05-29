#!/usr/bin/env python3
"""
Aplica as correções do arquivo correcoes-memoria.json no memoria-conceitos.json.

Uso:
    python3 rato/scripts/aplicar_correcoes.py
    python3 rato/scripts/aplicar_correcoes.py --memoria outro-arquivo.json --correcoes outras-correcoes.json
"""

import json
import argparse
import shutil
from pathlib import Path
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="Aplica correções ao memoria-conceitos.json")
    parser.add_argument("--memoria",   default="rato/memoria/memoria-conceitos.json", help="Arquivo de memória a corrigir")
    parser.add_argument("--correcoes", default="rato/memoria/correcoes-memoria.json", help="Arquivo com as correções")
    args = parser.parse_args()

    memoria_path   = Path(args.memoria)
    correcoes_path = Path(args.correcoes)

    if not memoria_path.exists():
        print(f"❌ Arquivo de memória não encontrado: {memoria_path}")
        return

    if not correcoes_path.exists():
        print(f"❌ Arquivo de correções não encontrado: {correcoes_path}")
        return

    # Backup automático antes de alterar
    backup_path = memoria_path.with_suffix(f".backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy2(memoria_path, backup_path)
    print(f"📦 Backup salvo em: {backup_path}")

    with open(memoria_path, encoding="utf-8") as f:
        memoria = json.load(f)

    with open(correcoes_path, encoding="utf-8") as f:
        correcoes = json.load(f)

    conceitos = memoria.setdefault("conceitos", {})
    alteracoes = 0

    # ── 1. Aplica correções de conceitos ─────────────────────────────────────
    for nome, dados in correcoes.get("conceitos_corrigidos", {}).items():
        if nome in conceitos:
            # Conceito existente — atualiza autores e relações
            entrada = conceitos[nome]

            autores_novos  = dados.get("autores", [])
            relacoes_novas = dados.get("relacoes", [])

            # Substitui autores sujos pelos corretos
            entrada["autores"] = list(dict.fromkeys(autores_novos))[:12]

            # Mescla relações sem duplicar
            relacoes_atuais = entrada.get("relacoes", [])
            entrada["relacoes"] = list(dict.fromkeys(relacoes_atuais + relacoes_novas))[:12]

            print(f"  ✏️  Conceito atualizado: {nome}")
        else:
            # Conceito novo — cria entrada completa
            conceitos[nome] = {
                "autores":    list(dict.fromkeys(dados.get("autores", [])))[:12],
                "relacoes":   list(dict.fromkeys(dados.get("relacoes", [])))[:12],
                "ocorrencias": 1,
                "arquivos":   ["bergson-materia-e-memoria.md"],
                "historico":  [],
            }
            print(f"  ➕ Conceito novo criado: {nome}")

        alteracoes += 1

    # ── 2. Atualiza conceitos_recorrentes ────────────────────────────────────
    if "conceitos_recorrentes_sugeridos" in correcoes:
        memoria["conceitos_recorrentes"] = correcoes["conceitos_recorrentes_sugeridos"]
        print(f"\n  ✏️  conceitos_recorrentes atualizado ({len(memoria['conceitos_recorrentes'])} itens)")

    # ── 3. Atualiza perguntas_abertas ─────────────────────────────────────────
    if "perguntas_abertas_sugeridas" in correcoes:
        memoria["perguntas_abertas"] = correcoes["perguntas_abertas_sugeridas"]
        print(f"  ✏️  perguntas_abertas atualizado ({len(memoria['perguntas_abertas'])} perguntas)")

    # ── 4. Atualiza autores_recorrentes ──────────────────────────────────────
    if "autores_recorrentes_sugeridos" in correcoes:
        memoria["autores_recorrentes"] = correcoes["autores_recorrentes_sugeridos"]
        print(f"  ✏️  autores_recorrentes atualizado ({len(memoria['autores_recorrentes'])} autores)")

    # ── 5. Salva ──────────────────────────────────────────────────────────────
    with open(memoria_path, "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"\n✅ {alteracoes} conceito(s) processado(s)")
    print(f"📁 Memória atualizada: {memoria_path.resolve()}")
    print(f"\nPodes apagar o arquivo de correções quando quiseres:")
    print(f"    rm {correcoes_path}")


if __name__ == "__main__":
    main()
