#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# uv is the recommended installer (avoids macOS 15 pip/truststore issues)
# Install uv if not present: curl -LsSf https://astral.sh/uv/install.sh | sh
if command -v uv &>/dev/null; then
  uv venv venv --python 3.12
  uv pip install --python venv/bin/python -r requirements.txt
else
  python3.12 -m venv venv
  venv/bin/pip install -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — fill in SCHWAB_APP_KEY and SCHWAB_APP_SECRET"
fi

echo "Setup complete."
echo "Test: venv/bin/python server.py"
