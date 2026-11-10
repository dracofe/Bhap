#!/usr/bin/env sh
# Build an air-gapped install bundle: wheels for Bhap and its optional extras.
# Run on an ONLINE machine with the same OS, CPU architecture and Python version as the target.
set -eu
PYTHON="${PYTHON:-python3}"
OUT="${1:-bhap-offline-bundle}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$OUT/wheels"
"$PYTHON" -m pip wheel --wheel-dir "$OUT/wheels" "$ROOT[all]"
cp "$ROOT/docs/installation.md" "$OUT/INSTALL.md"
( cd "$OUT/wheels" && sha256sum ./*.whl > ../SHA256SUMS 2>/dev/null || shasum -a 256 ./*.whl > ../SHA256SUMS )
echo "Bundle ready in $OUT/ — on the offline machine:"
echo "  pip install --no-index --find-links $OUT/wheels \"bhap-ai[all]\""
echo "Models are separate: copy your Ollama model directory as described in INSTALL.md."
