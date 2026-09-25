#!/usr/bin/env bash
set -euo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/locale.sh"
[[ -f "$SCRIPT" ]] || { printf 'FAIL: separate locale script missing\n' >&2; exit 1; }
bash -n "$SCRIPT"
"$SCRIPT" --help | grep -q -- '--locale'
"$SCRIPT" --help | grep -q -- '--timezone'
"$SCRIPT" --help | grep -q -- '--preset-fr'
if "$SCRIPT" --apply --locale 'fr_FR.UTF-8;true' --timezone Europe/Paris >/dev/null 2>&1; then
    printf 'FAIL: untrusted locale accepted\n' >&2; exit 1
fi
if "$SCRIPT" --apply --locale en_US.UTF-8 --timezone ../etc >/dev/null 2>&1; then
    printf 'FAIL: untrusted timezone accepted\n' >&2; exit 1
fi
if "$SCRIPT" --apply >/dev/null 2>&1; then
    printf 'FAIL: implicit French locale accepted\n' >&2; exit 1
fi
printf 'PASS: separate locale entry point\n'
