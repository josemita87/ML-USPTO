#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=== Step 1: Fetch data ==="
uv run python scripts/01_fetch_data.py

echo "=== Step 2: Preprocess ==="
uv run python scripts/02_preprocess.py

echo "=== Step 3: Train models ==="
uv run python scripts/03_train.py

echo "=== Step 4: Evaluate ==="
uv run python scripts/04_evaluate.py

echo "=== Done! Check data/models/ for results ==="
