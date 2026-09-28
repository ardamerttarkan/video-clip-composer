#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/streamlit ]]; then
  echo "Önce README.md içindeki sanal ortam kurulumunu tamamlayın." >&2
  exit 1
fi
exec .venv/bin/streamlit run app.py --server.address 127.0.0.1
