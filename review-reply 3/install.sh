#!/usr/bin/env bash
# One-command setup for Review Reply (macOS / Linux).
set -euo pipefail
cd "$(dirname "$0")"
MODEL="${MODEL:-qwen2.5:7b}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama isn't installed. Get it from https://ollama.com/download, then re-run this script."
  exit 1
fi

if ! ollama list >/dev/null 2>&1; then
  echo "Ollama is installed but not running. Open the Ollama app (or run 'ollama serve') and re-run."
  exit 1
fi

echo "Downloading the model once ($MODEL). After this, everything works offline."
ollama pull "$MODEL"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

echo "Starting Review Reply at http://localhost:8501"
streamlit run app.py --server.headless true --browser.gatherUsageStats false
