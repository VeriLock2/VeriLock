#!/bin/bash
set -e

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR"

if [ -x "$VENV_PYTHON" ]; then
    exec "$VENV_PYTHON" "$PROJECT_DIR/main.py"
else
    exec /usr/bin/python3 "$PROJECT_DIR/main.py"
fi
