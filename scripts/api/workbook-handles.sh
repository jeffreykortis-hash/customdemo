#!/usr/bin/env bash
# Emit the CALLABLE HANDLES for a workbook — everything an API client or an
# agent needs to query the dashboard you just built, in one JSON manifest.
#
# A built dashboard is not just a thing to look at: every element is queryable
# over REST. This turns "here's a URL" into "here's how to call it".
#
# Usage:
#   scripts/api/workbook-handles.sh <workbookId> [--verify] [--sql]
#
#   --verify  additionally EXPORT one data element and report its row count, so
#             the manifest proves the workbook answers queries rather than just
#             claiming it should. Costs one export round-trip (~10s).
#   --sql     include the warehouse SQL Sigma generates per element
#             (from /v2/workbooks/{id}/queries) — useful for handing an agent
#             the exact query behind a number.
#
# Output: JSON on stdout —
#   {workbookId, name, url, pages:[{pageId,name}],
#    elements:[{elementId, type, name, pageId, columns:[{id,name}], sql?}],
#    verified?:{elementId, rows}}
#
# Env: self-bootstrapped via _env.sh (loads .env, caches OAuth token)
#
# ⚠ MCP vs REST: the Sigma MCP server may be bound to a DIFFERENT ORG than your
# REST credentials, in which case it cannot see this workbook at all and
# `describe`/`query` return "No matching record". That is a tenancy mismatch,
# not an indexing lag — check `GET /v2/whoami`.organizationId against the org in
# the MCP's own result URLs before concluding the workbook is broken. REST
# querying (below) works regardless.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

wb_id="${1:?usage: workbook-handles.sh <workbookId> [--verify] [--sql]}"
shift || true
do_verify=false
do_sql=false
for a in "$@"; do
  case "$a" in
    --verify) do_verify=true ;;
    --sql)    do_sql=true ;;
    *) echo "workbook-handles: unknown flag $a" >&2; exit 2 ;;
  esac
done

meta="$(sigma_curl "$SIGMA_BASE_URL/v2/workbooks/$wb_id")"
pages="$(sigma_curl "$SIGMA_BASE_URL/v2/workbooks/$wb_id/pages")"
elements=""
# /elements is per-page; concatenate across pages.
for pid in $(printf '%s' "$pages" | python3 -c \
      'import sys,json;print(" ".join(p["pageId"] for p in json.load(sys.stdin).get("entries",[])))'); do
  one="$(sigma_curl "$SIGMA_BASE_URL/v2/workbooks/$wb_id/pages/$pid/elements" 2>/dev/null || true)"
  # Older/!per-page deployments expose a flat /elements instead.
  if [ -z "$one" ] || ! printf '%s' "$one" | grep -q '"entries"'; then
    one="$(sigma_curl "$SIGMA_BASE_URL/v2/workbooks/$wb_id/elements")"
    elements="$elements|ALL:$one"
    break
  fi
  elements="$elements|$pid:$one"
done

spec="$(sigma_curl "$SIGMA_BASE_URL/v2/workbooks/$wb_id/spec")"
queries=""
if $do_sql; then
  queries="$(sigma_curl "$SIGMA_BASE_URL/v2/workbooks/$wb_id/queries" || true)"
fi

manifest="$(python3 - "$wb_id" "$meta" "$pages" "$elements" "$spec" "$queries" <<'PY'
import json, sys

wb_id, meta_s, pages_s, elements_s, spec_s, queries_s = sys.argv[1:7]

def j(s, default):
    try:
        return json.loads(s) if s.strip() else default
    except Exception:
        return default

meta = j(meta_s, {})
pages = j(pages_s, {}).get("entries", [])

# elements arrive as "|<pageId>:<json>|<pageId>:<json>" or a single "|ALL:<json>"
by_el_page = {}
els = []
for chunk in elements_s.split("|"):
    if not chunk.strip():
        continue
    pid, _, body = chunk.partition(":")
    for e in j(body, {}).get("entries", []):
        els.append(e)
        if pid != "ALL":
            by_el_page[e.get("elementId")] = pid

# Column ids live in the SPEC, not in /elements — an API caller filtering or
# reading a specific measure needs them, so fold them in.
spec = j(spec_s, {})
inner = spec.get("spec", spec)
if isinstance(inner, str):
    inner = j(inner, {})
cols, page_of = {}, {}
for p in inner.get("pages", []) or []:
    for e in p.get("elements", []) or []:
        eid = e.get("id")
        if not eid:
            continue
        page_of[eid] = p.get("id")
        cs = e.get("columns") or []
        if cs and isinstance(cs[0], dict):
            cols[eid] = [{"id": c.get("id"), "name": c.get("name")}
                         for c in cs if c.get("id")]

sql_by_el = {}
for q in j(queries_s, {}).get("entries", []) or []:
    if q.get("elementId"):
        sql_by_el[q["elementId"]] = q.get("sql")

out_els = []
for e in els:
    eid = e.get("elementId")
    rec = {"elementId": eid, "type": e.get("type"), "name": e.get("name"),
           "pageId": by_el_page.get(eid) or page_of.get(eid)}
    if cols.get(eid):
        rec["columns"] = cols[eid]
    if sql_by_el.get(eid):
        rec["sql"] = sql_by_el[eid]
    out_els.append(rec)

# Data-bearing elements first — those are what a caller actually queries.
DATA = {"table", "pivot-table", "input-table", "kpi-chart", "bar-chart",
        "line-chart", "area-chart", "combo-chart", "donut-chart", "pie-chart",
        "scatter-chart", "region-map"}
out_els.sort(key=lambda r: (r.get("type") not in DATA, str(r.get("pageId")),
                            str(r.get("elementId"))))

print(json.dumps({
    "workbookId": wb_id,
    "name": meta.get("name"),
    "url": meta.get("url"),
    "pages": [{"pageId": p.get("pageId"), "name": p.get("name")} for p in pages],
    "queryableElements": sum(1 for r in out_els if r.get("type") in DATA),
    "elements": out_els,
}, indent=2))
PY
)"

if $do_verify; then
  # Prove it answers: export the first data-bearing element and count rows.
  target="$(printf '%s' "$manifest" | python3 -c '
import sys, json
els = json.load(sys.stdin)["elements"]
print(next((e["elementId"] for e in els if e.get("type") in ("table","pivot-table")), ""))')"
  if [ -n "$target" ]; then
    rows="$("$(dirname "$0")/query-element.sh" "$wb_id" "$target" csv 120 2>/dev/null \
            | tail -n +2 | wc -l | tr -d ' ')" || rows="ERROR"
    manifest="$(printf '%s' "$manifest" | python3 -c '
import sys, json
m = json.load(sys.stdin)
m["verified"] = {"elementId": sys.argv[1], "rows": sys.argv[2]}
print(json.dumps(m, indent=2))' "$target" "$rows")"
  fi
fi

printf '%s\n' "$manifest"
