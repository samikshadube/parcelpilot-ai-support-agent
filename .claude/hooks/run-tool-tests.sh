#!/usr/bin/env bash
# PostToolUse hook (Write|Edit): run pytest quietly whenever a file under
# src/tools/ changes, to catch access-control regressions immediately.
set -u

input="$(cat)"
file="$(printf '%s' "$input" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/^"file_path"[[:space:]]*:[[:space:]]*"(.*)"$/\1/')"
[ -z "$file" ] && exit 0

norm="${file//\\//}"
case "$norm" in
  */src/tools/*) ;;
  *) exit 0 ;;
esac

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$repo_root" || exit 0
[ -d tests ] || exit 0
command -v pytest >/dev/null 2>&1 || exit 0

out="$(pytest -q tests 2>&1)"
status=$?
# exit 5 = no tests collected yet; not a regression, don't block on it.
if [ $status -ne 0 ] && [ $status -ne 5 ]; then
  reason="pytest failed after editing ${norm}: ${out}"
  reason="${reason//\\/\\\\}"
  reason="${reason//\"/\\\"}"
  reason="${reason//$'\n'/ | }"
  printf '{"decision":"block","reason":"%s"}\n' "$reason"
fi
exit 0
