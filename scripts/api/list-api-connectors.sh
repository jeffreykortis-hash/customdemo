#!/usr/bin/env bash
# List the org's API CONNECTORS — the prerequisite for a workbook "Call API"
# action (button → external HTTP endpoint).
#
# Usage:
#   scripts/api/list-api-connectors.sh              # id + name + description
#   scripts/api/list-api-connectors.sh --detail <apiConnectorId>
#   scripts/api/list-api-connectors.sh --grep <substr>
#
# `--detail` returns the full definition — method, url, headers, pathParams,
# queryParams, body template and bodyParams with each param's `mode`
# (`static` | `dynamic`) and type. Dynamic params are the ones a workbook action
# maps to a control, column, or formula at call time.
#
# NOTE the endpoint is `/v2/api-connectors` (hyphenated). `/v2/apiConnectors`
# returns EMPTY rather than 404, so the camelCase guess looks like "this org has
# no connectors" instead of a wrong URL.
#
# Env: self-bootstrapped via _env.sh (loads .env, caches OAuth token)
set -euo pipefail
source "$(dirname "$0")/_env.sh"

mode="${1:-list}"

case "$mode" in
  --detail)
    cid="${2:?usage: list-api-connectors.sh --detail <apiConnectorId>}"
    sigma_curl "$SIGMA_BASE_URL/v2/api-connectors/$cid" | python3 -m json.tool
    ;;
  --grep)
    pat="${2:?usage: list-api-connectors.sh --grep <substr>}"
    sigma_curl "$SIGMA_BASE_URL/v2/api-connectors?limit=500" | python3 -c '
import sys, json
pat = sys.argv[1].lower()
for e in json.load(sys.stdin).get("entries", []):
    hay = (e.get("name","") + " " + (e.get("description") or "")).lower()
    if pat in hay:
        print(f'"'"'{e["apiConnectorId"]}  {e.get("name","")}'"'"')
' "$pat"
    ;;
  list|"")
    sigma_curl "$SIGMA_BASE_URL/v2/api-connectors?limit=500" | python3 -c '
import sys, json
es = json.load(sys.stdin).get("entries", [])
print(f"{len(es)} API connectors")
for e in es:
    d = (e.get("description") or "")[:60]
    print(f'"'"'  {e["apiConnectorId"]}  {e.get("name","")}{"  — " + d if d else ""}'"'"')
'
    ;;
  *)
    echo "usage: list-api-connectors.sh [--detail <id> | --grep <substr>]" >&2
    exit 2
    ;;
esac
