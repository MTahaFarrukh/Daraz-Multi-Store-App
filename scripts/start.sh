#!/bin/sh
set -e
mkdir -p "${DARAZ_DATA_DIR:-/var/data}"
exec uvicorn src.app:app --host 0.0.0.0 --port "${PORT:-8000}"
