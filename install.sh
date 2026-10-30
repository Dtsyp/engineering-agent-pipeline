#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/pipeline.py install --target "${1:-opencode}"
