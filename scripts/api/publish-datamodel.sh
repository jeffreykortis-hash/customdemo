#!/usr/bin/env bash
# Publish data model specs to Sigma — POST a new data model, PUT an update to
# an existing one, GET the spec back, or VERIFY that a published model's
# columns actually resolve. Mirrors publish-workbook.sh; there is no upstream
# sigma-data-models wrapper for this in this environment, so this is a
# project-local equivalent for the CREATE/UPDATE/GET round-trip.
#
# Driven by the `sigma-byod-data-model` skill.
#
# Usage:
#   scripts/api/publish-datamodel.sh post     <spec-file>
#   scripts/api/publish-datamodel.sh put      <dataModelId> <spec-file>
#   scripts/api/publish-datamodel.sh get-spec <dataModelId>
#   scripts/api/publish-datamodel.sh verify   <dataModelId> <elementId>
#
# post/put also run scripts/verify-star.py when the spec contains relationships:
# element inventory, relationship round-trip, and the join numbers that catch a
# duplicate dimension key silently multiplying every measure.
#
# post/put run scripts/validate-datamodel-spec.py first and abort on any issue,
# exactly as publish-workbook.sh runs validate-spec.py.
#
# post/put then AUTO-VERIFY. This is not belt-and-braces: the data-model
# endpoint accepts a formula referencing a column that does not exist and
# returns {"success":true}, and the broken column only appears later as type
# `error`. HTTP 200 is not evidence. Set SKIP_DM_VERIFY=1 to opt out.
#
# Auth, Accept: application/json header, and 401 auto-retry are all handled
# by the sigma_curl helper in _env.sh. No `delete` subcommand — deletion
# stays on the direct-curl path so it always hits the DELETE ask pattern in
# .claude/settings.json.
set -euo pipefail
_here="$(cd "$(dirname "$0")" && pwd)"
source "$_here/_env.sh"
_repo_root="$(cd "$_here/../.." && pwd)"

# Fail loudly if any column in the element resolved to type `error`.
verify_element() {
  local dm_id="$1" el_id="$2" ddl broken
  ddl="$("$_here/mcp-describe.sh" datamodel-element "$dm_id" "$el_id" 2>/dev/null || true)"
  if [ -z "$ddl" ]; then
    echo "publish-datamodel: WARNING — could not describe $dm_id/$el_id to verify." >&2
    return 0
  fi
  # Column lines look like:  "col-id" error -- "Name" | Formula: ...
  broken="$(printf '%s\n' "$ddl" | grep -E '^\s+"[^"]+" error' || true)"
  if [ -n "$broken" ]; then
    echo "publish-datamodel: BROKEN COLUMNS in $dm_id element $el_id —" >&2
    printf '%s\n' "$broken" >&2
    echo "  These resolved to type \`error\`: their formulas reference something that" >&2
    echo "  does not exist. The model was created but is not usable as-is." >&2
    return 1
  fi
  echo "publish-datamodel: verified — no error columns in $el_id"
}

verify_spec_elements() {
  local dm_id="$1" spec="$2" rc=0
  local ids
  ids="$(python3 -c '
import json, sys
spec = json.load(open(sys.argv[1]))
for page in spec.get("pages", []) or []:
    for el in page.get("elements", []) or []:
        if el.get("id"):
            print(el["id"])
' "$spec")"
  [ -z "$ids" ] && return 0
  while read -r el_id; do
    [ -z "$el_id" ] && continue
    verify_element "$dm_id" "$el_id" || rc=1
  done <<< "$ids"
  return $rc
}

# A star schema needs more than per-element checks: a dimension with duplicate
# primary keys fans out the fact and multiplies every measure, with no error
# anywhere. Only runs when the spec actually has relationships.
verify_star_if_any() {
  local dm_id="$1" spec="$2"
  python3 - "$spec" <<'PYEOF' || return 0
import json, sys
spec = json.load(open(sys.argv[1]))
has = any(e.get("relationships")
          for p in spec.get("pages", []) for e in p.get("elements", []))
sys.exit(0 if has else 1)
PYEOF
  python3 "$_repo_root/scripts/verify-star.py" "$dm_id" "$spec"
}

cmd="${1:-}"
case "$cmd" in
  post)
    spec="${2:?usage: publish-datamodel.sh post <spec-file>}"
    if [ ! -f "$spec" ]; then
      echo "publish-datamodel: spec file not found: $spec" >&2
      exit 2
    fi
    python3 "$_repo_root/scripts/validate-datamodel-spec.py" "$spec"
    resp="$(sigma_curl -X POST \
      -H "Content-Type: application/json" \
      --data-binary "@$spec" \
      "$SIGMA_BASE_URL/v2/dataModels/spec")"
    printf '%s\n' "$resp"
    dm_id="$(printf '%s' "$resp" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("dataModelId","") or "")
except Exception: print("")')"
    if [ -n "$dm_id" ] && [ -z "${SKIP_DM_VERIFY:-}" ]; then
      verify_spec_elements "$dm_id" "$spec"
      verify_star_if_any "$dm_id" "$spec"
    fi
    ;;
  put)
    dm_id="${2:?usage: publish-datamodel.sh put <dataModelId> <spec-file>}"
    spec="${3:?usage: publish-datamodel.sh put <dataModelId> <spec-file>}"
    if [ ! -f "$spec" ]; then
      echo "publish-datamodel: spec file not found: $spec" >&2
      exit 2
    fi
    python3 "$_repo_root/scripts/validate-datamodel-spec.py" "$spec"
    sigma_curl -X PUT \
      -H "Content-Type: application/json" \
      --data-binary "@$spec" \
      "$SIGMA_BASE_URL/v2/dataModels/$dm_id/spec"
    printf '\n'
    if [ -z "${SKIP_DM_VERIFY:-}" ]; then
      verify_spec_elements "$dm_id" "$spec"
      verify_star_if_any "$dm_id" "$spec"
    fi
    ;;
  get-spec)
    dm_id="${2:?usage: publish-datamodel.sh get-spec <dataModelId>}"
    sigma_curl "$SIGMA_BASE_URL/v2/dataModels/$dm_id/spec"
    ;;
  verify)
    dm_id="${2:?usage: publish-datamodel.sh verify <dataModelId> <elementId>}"
    el_id="${3:?usage: publish-datamodel.sh verify <dataModelId> <elementId>}"
    verify_element "$dm_id" "$el_id"
    ;;
  *)
    cat >&2 <<'USAGE'
usage:
  publish-datamodel.sh post     <spec-file>
  publish-datamodel.sh put      <dataModelId> <spec-file>
  publish-datamodel.sh get-spec <dataModelId>
  publish-datamodel.sh verify   <dataModelId> <elementId>
USAGE
    exit 2
    ;;
esac
