#!/bin/bash
# ================================================
#   Claude WeChat Bot (Tencent ilink API)
#   Start Script for Linux / macOS
# ================================================

set -e
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

echo "================================================"
echo "  Claude WeChat Bot (Tencent ilink API)"
echo "================================================"
echo ""

while true; do
    python3 main.py
    EXITCODE=$?
    if [ "$EXITCODE" -eq 42 ]; then
        echo ""
        echo "Scheduled restart triggered. Relaunching..."
        echo ""
        continue
    fi
    exit $EXITCODE
done
