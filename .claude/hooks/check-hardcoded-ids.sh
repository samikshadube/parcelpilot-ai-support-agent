#!/usr/bin/env bash
# PostToolUse hook (Write|Edit): block hardcoded ParcelPilot example IDs/names
# outside tests/ and data/. Enforces JD constraint: "load and reason over the
# supplied data rather than hard-coding the example IDs or answers."
set -u

input="$(cat)"
file="$(printf '%s' "$input" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/^"file_path"[[:space:]]*:[[:space:]]*"(.*)"$/\1/')"
[ -z "$file" ] && exit 0

norm="${file//\\//}"

case "$norm" in
  */src/*) ;;
  *) exit 0 ;;
esac

case "$norm" in
  */tests/*|*/data/*) exit 0 ;;
esac

[ -f "$file" ] || exit 0

hit="$(grep -nE 'ORD-1001|Northstar|LumenWorks' "$file" 2>/dev/null | head -3)"
if [ -n "$hit" ]; then
  reason="Hardcoded ParcelPilot example ID/name found in ${norm}. The grader tests with other records from the same data pack, so this logic must be generic - see CLAUDE.md hard constraint #1 and specifications.md. Matching lines: ${hit}"
  reason="${reason//\\/\\\\}"
  reason="${reason//\"/\\\"}"
  reason="${reason//$'\n'/ | }"
  printf '{"decision":"block","reason":"%s"}\n' "$reason"
fi
exit 0
