#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"

if [[ $# -eq 0 || "${1:-}" == "toca" ]]; then
  cd "$ROOT"
  python3 "$DIR/rato_toca.py"
  exit 0
fi

cmd="$1"
shift

case "$cmd" in
  ajuda|help|-h|--help)
    cat <<EOF
Uso:
  ./rato/scripts/rato.sh toca
  ./rato/scripts/rato.sh converter ARGS...
  ./rato/scripts/rato.sh limpar ARGS...
  ./rato/scripts/rato.sh catalogar ARGS...
  ./rato/scripts/rato.sh roer ARGS...
  ./rato/scripts/rato.sh farejar ARGS...
  ./rato/scripts/rato.sh digerir ARGS...

Sem argumentos, abre a toca interativa.
EOF
    ;;
  converter)
    exec python3 "$DIR/converter.py" "$@"
    ;;
  limpar)
    exec python3 "$DIR/limpar.py" "$@"
    ;;
  catalogar)
    exec python3 "$DIR/catalogar.py" "$@"
    ;;
  roer)
    exec python3 "$DIR/roer.py" "$@"
    ;;
  farejar)
    exec python3 "$DIR/farejar.py" "$@"
    ;;
  digerir)
    exec python3 "$DIR/digerir.py" "$@"
    ;;
  *)
    echo "Comando desconhecido: $cmd" >&2
    echo "Use: ./rato/scripts/rato.sh ajuda" >&2
    exit 2
    ;;
esac
