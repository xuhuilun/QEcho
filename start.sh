#!/usr/bin/env bash
# Install the locked Python environment, then run QEcho from this directory.
set -u
set -o pipefail

fail() {
  local code="$1"
  shift
  printf '%s\n' "$*" >&2
  exit "$code"
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
cd -- "$SCRIPT_DIR" || fail 1 'Cannot open the QEcho project directory.'
CONFIG_PATH="$SCRIPT_DIR/qecho.toml"

if [[ ! -e "$CONFIG_PATH" ]]; then
  (umask 077; cp -- "$SCRIPT_DIR/qecho.example.toml" "$CONFIG_PATH") ||
    fail 1 'Cannot create qecho.toml. Check that qecho.example.toml is present and the directory is writable.'
  chmod 600 "$CONFIG_PATH" || fail 1 'Cannot restrict access to qecho.toml.'
  printf '%s\n' \
    'Created qecho.toml from qecho.example.toml.' \
    'Edit qecho.toml (model, QQ connection, friends and prompt), then run this launcher again.' \
    'QEcho was not started.'
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  fail 1 'uv was not found. Install it from https://docs.astral.sh/uv/getting-started/installation/ and reopen your terminal.'
fi

uv python install 3.12 || fail "$?" 'Python 3.12 installation failed. QEcho was not started.'
uv sync --locked --no-dev || fail "$?" 'Python dependency installation failed. QEcho was not started.'
uv run --no-sync qecho --config "$CONFIG_PATH" || fail "$?" 'QEcho stopped with an error. Check the message above.'
