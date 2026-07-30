#!/usr/bin/env bash
# List warehouse connections in the org, including write-access capability.
#
# Usage:
#   scripts/api/list-connections.sh              # all connections
#   scripts/api/list-connections.sh --writable   # only write-access-enabled ones
#   scripts/api/list-connections.sh --raw        # full unprojected entries
#
# Output: JSON array [{connectionId, name, type, writeAccess, writebacks}]
#   type        — warehouse dialect. Verified values seen in the wild:
#                 snowflake, databricks, postgres, mysql, bigQuery, sqlserver,
#                 clickHouse, azuresql, alloydb, starburst, emulator.
#   writeAccess — true when the admin has enabled write access, else false
#                 (the API returns null when off; normalized to false here).
#   writebacks  — [{database, schema}] write destinations, [] when off.
#
# WHY THIS MATTERS: input tables, warehouse views, materialization and CSV
# upload all require write access. A read-only connection can still be read
# from, so a dashboard's charts work while its input tables silently fail.
# Filter with --writable before building anything that writes back.
#
# Write access is an ADMIN-only toggle (Administration > Connections > Edit >
# Enable write access + a write destination). Nothing here can turn it on —
# if a connection isn't writable, tell the user to flip it in Sigma.
#
# Env: self-bootstrapped via _env.sh (loads .env, caches OAuth token)
set -euo pipefail
source "$(dirname "$0")/_env.sh"

mode="${1:-}"

sigma_curl "$SIGMA_BASE_URL/v2/connections?limit=200" \
  | MODE="$mode" python3 -c '
import sys, json, os
mode = os.environ.get("MODE", "")
entries = json.load(sys.stdin).get("entries", [])

if mode == "--raw":
    json.dump(entries, sys.stdout, indent=2)
    print()
    raise SystemExit(0)

out = []
for e in entries:
    # The API omits/nulls writeAccess when it is off; normalize to a bool so
    # callers can test it without three-way logic.
    write_access = e.get("writeAccess") is True
    if mode == "--writable" and not write_access:
        continue
    out.append({
        "connectionId": e.get("connectionId"),
        "name":         e.get("name"),
        "type":         e.get("type"),
        "writeAccess":  write_access,
        "writebacks":   e.get("writebacks") or [],
    })

json.dump(out, sys.stdout, indent=2)
print()
'
