#!/usr/bin/env bash
# Call the Sigma MCP server's `query` tool and print the result rows.
#
# This is the ONLY headless way to run a JOIN across two elements of a data
# model — which is what proves a star schema's relationships actually produce
# rows. `mcp-describe.sh` tells you a column exists; only a query tells you the
# join works.
#
# Elements are addressed as "datamodel"."<elementId>" and columns by their
# COLUMN ID (not their name) — get both from `mcp-describe.sh datamodel-element`.
# SQL is Postgres syntax; all identifiers must be double-quoted.
#
# Usage:
#   scripts/api/mcp-query.sh datamodel  <dataModelId> "<sql>"
#   scripts/api/mcp-query.sh workbook   <workbookId>  "<sql>"
#   scripts/api/mcp-query.sh connection <connectionId> "<sql>"
#
# Options (before the kind):
#   --json    emit the raw {columns, rows} JSON instead of a text table
#
# Env:    self-bootstrapped via _env.sh (loads .env, caches OAuth token)
# Output: a text table (default) or JSON. Exit 1 on a query error.
#
# ⚠ A join on mismatched key types (text = number) does not return a clean
#   error — it 502s the server. Check key types via describe first.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

as_json=0
if [ "${1:-}" = "--json" ]; then as_json=1; shift; fi

if [ "$#" -ne 3 ]; then
  cat >&2 <<'USAGE'
usage:
  mcp-query.sh [--json] datamodel  <dataModelId>  "<sql>"
  mcp-query.sh [--json] workbook   <workbookId>   "<sql>"
  mcp-query.sh [--json] connection <connectionId> "<sql>"
USAGE
  exit 2
fi

python3 - "$SIGMA_BASE_URL" "$SIGMA_API_TOKEN" "$as_json" "$@" <<'PY'
import json, re, sys, urllib.request

base, tok, as_json, kind, ident, sql = sys.argv[1:]
as_json = as_json == "1"

id_field = {"datamodel": "dataModelId", "workbook": "workbookId",
            "connection": "connectionId"}.get(kind)
if not id_field:
    sys.stderr.write(f"mcp-query: unknown kind '{kind}'\n")
    sys.exit(2)

body = {
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "query",
               "arguments": {"query": {"type": kind, id_field: ident, "sql": sql}}},
}
req = urllib.request.Request(
    f"{base}/mcp/v2", data=json.dumps(body).encode(),
    headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"},
)
try:
    raw = urllib.request.urlopen(req).read().decode()
except urllib.error.HTTPError as e:
    detail = e.read().decode()[:300]
    sys.stderr.write(f"mcp-query: HTTP {e.code}. A {e.code} here often means a join on\n"
                     f"  MISMATCHED KEY TYPES (e.g. text = number) rather than a bad query.\n"
                     f"  {detail}\n")
    sys.exit(1)

m = re.search(r"data:\s*(\{.+\})", raw, re.DOTALL)
if not m:
    sys.stderr.write(f"mcp-query: unexpected response shape:\n{raw[:400]}\n")
    sys.exit(1)
env = json.loads(m.group(1))
if env.get("error"):
    sys.stderr.write(f"mcp-query: server error: {env['error']}\n")
    sys.exit(1)
result = env["result"]
if result.get("isError"):
    for c in result.get("content", []):
        if c.get("type") == "text":
            sys.stderr.write(c["text"] + "\n")
    sys.exit(1)

for c in result.get("content", []):
    if c.get("type") != "text":
        continue
    try:
        payload = json.loads(c["text"])
    except json.JSONDecodeError:
        print(c["text"]); break
    if as_json:
        json.dump(payload, sys.stdout, indent=2); print()
        break
    cols = payload.get("columns") or []
    rows = payload.get("rows") or []
    names = [c.get("name", c) if isinstance(c, dict) else str(c) for c in cols]
    widths = [max(len(str(n)), *(len(str(r[i])) for r in rows)) if rows else len(str(n))
              for i, n in enumerate(names)]
    print("  ".join(str(n).ljust(w) for n, w in zip(names, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    print(f"\n({len(rows)} rows)")
    break
PY
