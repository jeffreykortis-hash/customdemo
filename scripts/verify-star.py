#!/usr/bin/env python3
"""Post-publish verification for a multi-element star-schema data model.

    python3 scripts/verify-star.py <dataModelId> <submitted-spec.json>

`publish-datamodel.sh` runs this automatically whenever the spec contains
relationships. It assumes SIGMA_BASE_URL / SIGMA_API_TOKEN are already exported
(i.e. `_env.sh` has been sourced).

WHY THIS EXISTS. `publish-datamodel.sh` already describes every element and fails
on `error`-typed columns, which catches broken formulas. It cannot see the
failure mode that matters most in a star:

    A dimension with DUPLICATE primary keys silently FANS OUT the fact and
    multiplies every measure.

HTTP 200, a clean GET-back, a returned row set and a beautifully rendered chart
are all four compatible with 3x inflated revenue. Every other failure here
announces itself — an error column, an empty chart, a 502 on mismatched key
types. This one doesn't. On synthetic data, where the numbers are fabricated
anyway, nobody would catch it by eye.

Checks, in order:
  1. every submitted element still exists after POST
  2. every submitted relationship round-trips with both key ids intact
  3. per relationship, four numbers proving the join is sound
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("SIGMA_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("SIGMA_API_TOKEN", "")
HERE = os.path.dirname(os.path.abspath(__file__))


def api(path: str) -> dict:
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def query(dm: str, sql: str, attempts: int = 3):
    """Run SQL across the model's elements. Returns rows, or None on failure.

    Retries, because the MCP query endpoint returns a transient 502 often enough
    to matter — observed succeeding on the very next attempt with an identical
    query. A false "your star is broken" would train people to ignore this check,
    which would defeat the one thing it exists to catch.
    """
    last = ""
    for n in range(attempts):
        out = subprocess.run(
            [os.path.join(HERE, "api", "mcp-query.sh"), "--json", "datamodel", dm, sql],
            capture_output=True, text=True)
        if out.returncode == 0:
            try:
                return json.loads(out.stdout).get("rows") or []
            except json.JSONDecodeError:
                last = "unparseable response"
                continue
        last = out.stderr.strip()[:200]
        if n + 1 < attempts:
            time.sleep(1.5 * (n + 1))
    sys.stderr.write(f"  query failed after {attempts} attempts: {last}\n")
    return None


def main() -> None:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: verify-star.py <dataModelId> <submitted-spec.json>\n")
        sys.exit(2)
    dm, spec_path = sys.argv[1], sys.argv[2]
    spec = json.load(open(spec_path))
    submitted = [e for p in spec.get("pages", []) for e in p.get("elements", [])]
    if not any(e.get("relationships") for e in submitted):
        print("verify-star: no relationships in spec — nothing to verify")
        sys.exit(0)

    problems = 0
    back = api(f"/v2/dataModels/{dm}/spec")
    got = {e.get("id"): e for p in back.get("pages", []) for e in p.get("elements", [])}

    # 1. elements survived
    for e in submitted:
        if e["id"] not in got:
            print(f"  MISSING element {e['id']} — dropped on POST"); problems += 1
    if not problems:
        print(f"verify-star: all {len(submitted)} elements present")

    # 2. relationships round-tripped
    for e in submitted:
        gr = {r.get("id"): r for r in (got.get(e["id"], {}).get("relationships") or [])}
        for rel in e.get("relationships") or []:
            g = gr.get(rel["id"])
            if not g:
                print(f"  RELATIONSHIP {rel['id']} was SWALLOWED — accepted but not stored")
                problems += 1
            elif g.get("keys") != rel.get("keys"):
                print(f"  RELATIONSHIP {rel['id']} keys changed: "
                      f"sent {rel.get('keys')} got {g.get('keys')}")
                problems += 1
    if not problems:
        n = sum(len(e.get("relationships") or []) for e in submitted)
        print(f"verify-star: all {n} relationships round-tripped intact")

    # 3. the four numbers
    for e in submitted:
        for rel in e.get("relationships") or []:
            fact, dim = e["id"], rel["targetElementId"]
            k = (rel.get("keys") or [{}])[0]
            fk, pk = k.get("sourceColumnId"), k.get("targetColumnId")
            rows = query(dm, (
                f'SELECT (SELECT count(*) FROM "datamodel"."{fact}") AS fact_rows,'
                f' (SELECT count(*) FROM "datamodel"."{dim}") AS dim_rows,'
                f' (SELECT count(DISTINCT "{pk}") FROM "datamodel"."{dim}") AS dim_keys,'
                f' (SELECT count(*) FROM "datamodel"."{fact}" f'
                f'  JOIN "datamodel"."{dim}" d ON f."{fk}" = d."{pk}") AS joined'))
            if not rows:
                print(f"  {rel['id']}: could not run the join probe after retries. "
                      "Check both key column types via `mcp-describe.sh "
                      "datamodel-element` — a join on MISMATCHED KEY TYPES 502s "
                      "rather than erroring cleanly. If the types match, the endpoint "
                      "is likely just unavailable; re-run.")
                problems += 1
                continue
            fr, dr, dk, jn = (int(x) for x in rows[0][:4])
            label = f"  {rel['id']}: fact={fr} dim={dr} keys={dk} joined={jn}"
            if jn == 0:
                print(label + "  JOIN PRODUCES NO ROWS — every FK is an orphan")
                problems += 1
            elif dr != dk:
                print(label + f"  DUPLICATE DIMENSION KEYS ({dr - dk} dupes) — this FANS "
                              "OUT the fact and MULTIPLIES every measure")
                problems += 1
            elif jn != fr:
                d = jn - fr
                print(label + (f"  {'fan-out' if d > 0 else 'orphan FKs'}: joined differs "
                               f"from fact rows by {d:+d}"))
                problems += 1
            else:
                print(label + "  OK")

    if problems:
        sys.stderr.write(f"\nverify-star: {problems} problem(s) — the model is published "
                         "but its star is not sound.\n")
        sys.exit(1)
    print("verify-star: star is sound")


if __name__ == "__main__":
    main()
