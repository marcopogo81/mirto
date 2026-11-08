#!/usr/bin/env bash
# Rigenera il file dei blocchi (lock) delle dipendenze per QUESTO sistema, con le impronte SHA-256.
#
#   tools/aggiorna-lock.sh
#
# Produce requirements-<sistema>.lock (macos, linux, windows): l'elenco esatto delle versioni installate
# e le loro impronte, così una build è riproducibile e un pacchetto manomesso non passa.
# Le ruote disponibili cambiano da un sistema all'altro (pyobjc solo su Mac, DirectML solo su Windows):
# per questo c'è un file per sistema, ognuno generato sul suo.
set -euo pipefail
cd "$(dirname "$0")/.."

case "$(uname -s 2>/dev/null || echo Windows)" in
  Darwin)  SISTEMA=macos ;;
  Linux)   SISTEMA=linux ;;
  *)       SISTEMA=windows ;;
esac
USCITA="requirements-$SISTEMA.lock"

python3 -m pip install --quiet --upgrade pip pip-tools
echo "Risolvo le dipendenze per $SISTEMA…"
python3 -m piptools compile \
  --generate-hashes \
  --strip-extras \
  --output-file "$USCITA" \
  pyproject.toml

echo
echo "Scritto $USCITA ($(grep -c '^[a-zA-Z]' "$USCITA") pacchetti)."
echo "Controllalo con  git diff  e committalo: la CI e il workflow di release lo useranno al posto"
echo "di requirements.txt quando è presente."
