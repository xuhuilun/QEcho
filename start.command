#!/usr/bin/env bash
# Finder opens this wrapper in Terminal; keep the final result visible.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
bash "$SCRIPT_DIR/start.sh"
code=$?
if [[ -t 0 ]]; then
  printf '\nPress Enter to close this launcher.'
  IFS= read -r _
fi
exit "$code"
