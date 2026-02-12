#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT_DIR/dist"
STAGE_DIR="$OUT_DIR/deploy_src"
ZIP_PATH="$OUT_DIR/tg-bot-render-ready.zip"

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR" "$OUT_DIR"

# Minimal deploy payload: only runtime code and deployment configs.
rsync -a --prune-empty-dirs \
  --exclude '**/__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '.DS_Store' \
  --include '/botapp/***' \
  --include '/run_local.py' \
  --include '/requirements.txt' \
  --include '/render.yaml' \
  --include '/README.md' \
  --include '/.env.example' \
  --include '/.env.render.example' \
  --include '/.gitignore' \
  --exclude '*' \
  "$ROOT_DIR/" "$STAGE_DIR/"

rm -f "$ZIP_PATH"
(
  cd "$STAGE_DIR"
  zip -rq "$ZIP_PATH" .
)

echo "Deploy zip created: $ZIP_PATH"
