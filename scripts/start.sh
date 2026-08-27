#!/bin/sh
set -e
mkdir -p /app/data
exec uvicorn src.app:app --host 0.0.0.0 --port "${PORT:-8000}"
