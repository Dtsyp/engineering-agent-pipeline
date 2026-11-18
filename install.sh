#!/usr/bin/env sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
target=${1:-opencode}
if [ "$#" -gt 0 ]; then shift; fi
exec python3 "$root/tools/pipeline.py" install --target "$target" "$@"
