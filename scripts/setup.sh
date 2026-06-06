#!/bin/bash
# ================================================
#   Claude WeChat Bot - Setup (Linux / macOS)
# ================================================

set -e
cd "$(dirname "$0")/.."

echo ""
echo "================================================"
echo "  Claude WeChat Bot - Setup"
echo "================================================"
echo ""

# Check Python 3.11+
echo "[1/4] Checking Python version..."
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.11+"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])")
if [ "$PYVER" -lt 311 ]; then
    echo "ERROR: Python 3.11+ required. Current: $PYVER"
    exit 1
fi
echo "  Python version OK."

# Create virtual environment
echo ""
echo "[2/4] Creating virtual environment..."
if [ -d ".venv" ]; then
    echo "  .venv already exists, skipping."
else
    python3 -m venv .venv
    echo "  Virtual environment created."
fi

# Activate and install dependencies
source .venv/bin/activate
echo ""
echo "[3/4] Installing dependencies..."
pip install -r requirements.txt -q
echo "  Dependencies installed."

# Setup .env
echo ""
echo "[4/4] Setting up configuration..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  Created .env from .env.example"
    echo "  IMPORTANT: Edit .env to set your ANTHROPIC_API_KEY !"
else
    echo "  .env already exists, skipping."
fi

# Create data directories and sample stickers
mkdir -p data
python3 src/generate_stickers.py 2>/dev/null || {
    echo "  (Sticker generation skipped - Pillow may not be installed)"
    for em in happy sad angry love surprised neutral; do
        mkdir -p "data/stickers/$em"
    done
}

echo ""
echo "================================================"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit .env and set your ANTHROPIC_API_KEY"
echo "     (or configure it later in the admin panel)"
echo "  2. Run: ./start.sh"
echo "  3. Open http://localhost:8080 for admin panel"
echo "================================================"
echo ""
