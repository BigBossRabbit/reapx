#!/bin/bash
# run_smoke.sh — run the ReapX CI smoke test.
# Installs requirements only if needed, then executes tests/smoke_test.py.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "[run_smoke] ReapX repo: $REPO_DIR"

# Install requirements if the runtime deps (jinja2, yaml) aren't already present.
if ! python3 -c "import jinja2, yaml" >/dev/null 2>&1; then
    echo "[run_smoke] Installing requirements.txt ..."
    python3 -m pip install -r requirements.txt
else
    echo "[run_smoke] Runtime deps already installed; skipping pip install."
fi

echo "[run_smoke] Running smoke test ..."
python3 tests/smoke_test.py
