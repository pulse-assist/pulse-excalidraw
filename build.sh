#!/usr/bin/env bash
# Сборка редактора: web/ → backend/excalidraw_plugin/static (index.html, assets/, fonts/).
# node_modules держим вне папки проекта (по умолчанию /tmp/pulse-excalidraw-build), чтобы не класть сотни МБ
# в каталог файлов. Сборку кладём в плагин готовой: при установке Пульсу не нужны node и сеть.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK="${XD_BUILD_DIR:-/tmp/pulse-excalidraw-build}"
OUT="$ROOT/backend/excalidraw_plugin/static"

mkdir -p "$WORK"
rsync -a --delete --exclude node_modules "$ROOT/web/" "$WORK/"
cd "$WORK"
if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
npm test
XD_OUT="$OUT" npx vite build
# шрифты Excalidraw — локально (EXCALIDRAW_ASSET_PATH), без unpkg/CDN
rm -rf "$OUT/fonts"
cp -r node_modules/@excalidraw/excalidraw/dist/prod/fonts "$OUT/fonts"
cp package-lock.json "$ROOT/web/package-lock.json"
echo "Готово: $OUT ($(du -sh "$OUT" | cut -f1))"
