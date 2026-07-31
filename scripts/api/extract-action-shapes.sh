#!/usr/bin/env bash
# Harvest REAL action-effect shapes out of workbooks that already use them.
#
# WHY THIS EXISTS. Every wrong action effect fails as the same masked
# `Invalid kind: "button"` — an unknown effect NAME and a known effect with a
# MISSING REQUIRED FIELD are indistinguishable. So you cannot discover an
# effect's shape by guessing: verified live, `{"effect":"navigate","page":"pg"}`
# and `{"effect":"zzz-not-real"}` return byte-identical errors. The only reliable
# method is to clone the shape from a workbook that already works — which is what
# this does, across a whole org, in one command.
#
# Usage:
#   scripts/api/extract-action-shapes.sh                 # scan all workbooks
#   scripts/api/extract-action-shapes.sh --effect call-api
#   scripts/api/extract-action-shapes.sh --workbook <id>
#   scripts/api/extract-action-shapes.sh --limit 40
#
# Output: one canonical JSON example per distinct effect, plus which workbook it
# came from. Paste the shape straight into a generator.
#
# Scanning a whole org costs one GET per workbook, so use --limit or --workbook
# when you only need a spot check.
#
# Env: self-bootstrapped via _env.sh (loads .env, caches OAuth token)
set -euo pipefail
_here="$(cd "$(dirname "$0")" && pwd)"
source "$_here/_env.sh"

want_effect=""
only_wb=""
limit=500
while [ "$#" -gt 0 ]; do
  case "$1" in
    --effect)   want_effect="${2:?}"; shift 2 ;;
    --workbook) only_wb="${2:?}";     shift 2 ;;
    --limit)    limit="${2:?}";       shift 2 ;;
    *) echo "extract-action-shapes: unknown flag $1" >&2; exit 2 ;;
  esac
done

if [ -n "$only_wb" ]; then
  wb_json="{\"entries\":[{\"workbookId\":\"$only_wb\",\"name\":\"(requested)\"}]}"
else
  wb_json="$(sigma_curl "$SIGMA_BASE_URL/v2/workbooks?limit=$limit")"
fi

WB_JSON="$wb_json" WANT="$want_effect" python3 - <<'PYEOF'
import json, os, sys, urllib.request, urllib.error

BASE = os.environ["SIGMA_BASE_URL"]
TOK  = os.environ["SIGMA_API_TOKEN"]
WANT = os.environ.get("WANT") or ""
H = {"Authorization": "Bearer " + TOK, "Accept": "application/json"}

wbs = json.loads(os.environ["WB_JSON"]).get("entries", [])
found, counts = {}, {}

def walk(o, wbname):
    if isinstance(o, dict):
        eff = o.get("effect")
        if isinstance(eff, str):
            counts[eff] = counts.get(eff, 0) + 1
            found.setdefault(eff, (o, wbname))
        for v in o.values():
            walk(v, wbname)
    elif isinstance(o, list):
        for v in o:
            walk(v, wbname)

scanned = 0
for w in wbs:
    wid = w.get("workbookId")
    if not wid:
        continue
    try:
        r = urllib.request.Request(f"{BASE}/v2/workbooks/{wid}/spec", headers=H)
        body = urllib.request.urlopen(r, timeout=90).read().decode()
    except Exception:
        continue
    scanned += 1
    if '"effect"' not in body:
        continue
    try:
        d = json.loads(body)
    except Exception:
        continue
    spec = d.get("spec", d)
    if isinstance(spec, str):
        try: spec = json.loads(spec)
        except Exception: continue
    walk(spec, w.get("name", wid))

print(f"scanned {scanned} workbook(s); {len(found)} distinct effect(s)\n")
for eff in sorted(found, key=lambda e: -counts[e]):
    if WANT and eff != WANT:
        continue
    shape, wbname = found[eff]
    print(f"### {eff}   ({counts[eff]} occurrence(s), e.g. in {wbname!r})")
    print(json.dumps(shape, indent=2))
    print()
if WANT and WANT not in found:
    print(f"NOT FOUND: no workbook in this scan uses the {WANT!r} effect.")
    print("There is nothing to clone. Build ONE in the Sigma UI, then re-run")
    print("this with --workbook <that workbook id> to capture its shape.")
PYEOF
