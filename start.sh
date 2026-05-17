#!/bin/bash
# Grid MVP — Santa Ana 2
# Arranca el servidor en http://localhost:8000

cd "$(dirname "$0")"

echo ""
echo "⚡  GridViewer — Santa Ana 2"
echo "──────────────────────────────"
echo "  Instalando dependencias..."
pip install fastapi uvicorn pandapower utm openpyxl -q --break-system-packages 2>/dev/null

echo "  Arrancando servidor en http://localhost:8000"
echo "  (Ctrl+C para parar)"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
