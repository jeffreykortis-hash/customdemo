#!/usr/bin/env python3
"""Profile a client's warehouse table so a Sigma data model can be shaped from it.

The measurement half of the BYOD flow. It reports what IS in the table and
which columns are PLAUSIBLE candidates for each role; it does not decide.
Ambiguity comes back as `candidates` for the agent to turn into a
human-friendly question — the same contract as scripts/sigma-resolve.py.

Usage:
  scripts/profile-table.py <connectionId> <DB> <SCHEMA> <TABLE> [options]

Options:
  --via {auto,workbook,columns-only}
        auto (default)  try `workbook`, fall back to `columns-only`
        workbook        create a scratch workbook running one aggregate query,
                        read it back, then leave it for you to delete
        columns-only    names/types/comments only — no SQL, no object created
  --folder <folderId>   where the scratch workbook goes. Optional — defaults to
                        YOUR home folder (/v2/whoami -> /v2/members/{id}.homeFolderId).
                        Pass it explicitly to put the scratch workbook somewhere
                        specific, e.g. alongside the deliverable.
  --top-k <n>           distinct values to list per low-cardinality column (default 10)
  --max-dim-card <n>    at or below this distinct count a column is a dimension
                        candidate (default 50)
  --out <file>          write JSON here instead of stdout

Output (JSON): {source, rowCount, columns[], candidates{}, warnings[], profiledVia,
                probeWorkbookId?, cleanup?}

Env: SIGMA_BASE_URL, SIGMA_API_TOKEN (run scripts/api/_env.sh or the sigma-api
skill first).

NOTE: `--via workbook` CREATES a workbook in the client's org. It is not deleted
automatically — deletion stays on the direct-curl path so it hits the DELETE ask
pattern. The id and a cleanup hint are in the output.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SIGMA_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("SIGMA_API_TOKEN", "")

# Column-name suffixes that mark an integer as an identifier rather than a
# measure. Without this, ORDER_NUMBER and STORE_KEY read as "numeric, high
# cardinality" and get proposed as things to Sum().
ID_SUFFIXES = ("_ID", "_KEY", "_NUMBER", "_NUM", "_CODE", "_UUID", "_GUID")
DATE_TYPES = {"date", "datetime", "timestamp", "timestamp_ntz", "timestamp_tz"}
NUMERIC_TYPES = {"number", "integer", "float", "double", "decimal", "numeric", "bigint", "int"}


def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    if not BASE or not TOKEN:
        sys.stderr.write("profile-table: SIGMA_BASE_URL and SIGMA_API_TOKEN required\n")
        sys.exit(2)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        return {"_error": True, "status": e.code, "body": e.read().decode()[:500]}


def resolve_home_folder() -> str | None:
    """The caller's own home folder, for the scratch profiling workbook.

    `/v2/whoami` → userId, `/v2/members/{userId}` → homeFolderId. Saves the
    caller from having to know a folder id just to read statistics, which was
    the difference between the stats path working and silently not running.
    """
    who = api("/v2/whoami")
    uid = who.get("userId") if isinstance(who, dict) else None
    if not uid:
        return None
    return (api(f"/v2/members/{uid}") or {}).get("homeFolderId")


def friendly(raw: str) -> str:
    """Warehouse column name -> the display name Sigma derives from it.

    Verified: PRODUCT_TYPE -> 'Product Type', SKU_NUMBER -> 'Sku Number',
    store_key -> 'Store Key'. This is the name that goes on the RIGHT of the
    slash in a passthrough formula: [<table>/<friendly>].
    """
    return " ".join(w.capitalize() if w.isalpha() else w for w in raw.split("_"))


def is_id_shaped(name: str) -> bool:
    up = name.upper()
    return up.endswith(ID_SUFFIXES) or up in ("ID", "KEY")


def norm_type(t) -> str:
    """Column type, flattened.

    /v2/connections/tables/{inode}/columns nests it as {"type": {"type": "number"}},
    so accept both the nested and the already-flat form.
    """
    if isinstance(t, dict):
        t = t.get("type")
    return (t or "").strip().lower()


def q(ident: str, dialect: str = "") -> str:
    """Quote a warehouse identifier for the given dialect.

    This has to be dialect-aware: Databricks treats "double quotes" as STRING
    LITERALS unless ANSI mode is on, so COUNT("PRICE") silently counts a
    constant instead of the column. Backticks are the Databricks form.
    """
    if dialect == "databricks":
        return "`" + ident.replace("`", "``") + "`"
    return '"' + ident.replace('"', '""') + '"'


def build_profile_sql(path: list[str], cols: list[dict],
                      dialect: str = "") -> tuple[str, list[str]]:
    """One row of aggregates over every column. Returns (sql, output_aliases).

    Kept to functions that exist on both Snowflake and Databricks:
    COUNT, APPROX_COUNT_DISTINCT, MIN, MAX, CAST(x AS STRING).

    Aliases are positional (N_0, D_0, ...) because warehouse column names are
    not safe to reuse as SQL aliases.
    """
    fqn = ".".join(q(p, dialect) for p in path)
    sel, aliases = [], []

    def add(expr: str, alias: str):
        sel.append(f"{expr} AS {alias}")
        aliases.append(alias)

    add("COUNT(*)", "ROW_COUNT")
    for i, c in enumerate(cols):
        name, t = c["name"], norm_type(c.get("type"))
        col = q(name, dialect)
        add(f"COUNT({col})", f"N_{i}")
        add(f"APPROX_COUNT_DISTINCT({col})", f"D_{i}")
        # MIN/MAX are meaningful for dates and numbers; cast to string so a
        # single result row can carry mixed types.
        if t in DATE_TYPES or t in NUMERIC_TYPES:
            add(f"CAST(MIN({col}) AS STRING)", f"MIN_{i}")
            add(f"CAST(MAX({col}) AS STRING)", f"MAX_{i}")
    return "SELECT " + ", ".join(sel) + f" FROM {fqn}", aliases


def run_via_workbook(conn_id: str, folder: str, sql: str, aliases: list[str],
                     name: str) -> tuple[dict, str]:
    """Create a scratch workbook whose one element is the profiling query, then
    export it. Returns (first_row_as_dict, workbookId).

    A custom-SQL table element MUST declare `columns` — omitting them is
    rejected as the masked error `Invalid kind: "table"`.
    """
    spec = {
        "name": name,
        "folderId": folder,
        "schemaVersion": 1,
        "pages": [{"id": "pg", "name": "Profile", "elements": [{
            "id": "prof", "kind": "table", "name": "Profile",
            "source": {"connectionId": conn_id, "kind": "sql", "statement": sql},
            "columns": [{"id": f"p-{al}", "formula": f"[Custom SQL/{al}]", "name": al}
                        for al in aliases],
            "order": [f"p-{al}" for al in aliases],
        }]}],
        "layout": ('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" '
                   'gridTemplateRows="auto" id="pg">\n'
                   '  <LayoutElement elementId="prof" gridColumn="1 / 25" gridRow="1 / 10"/>\n'
                   '</Page>'),
    }
    r = api("/v2/workbooks/spec", "POST", spec)
    if r.get("_error") or not r.get("workbookId"):
        raise RuntimeError(f"scratch workbook create failed: {json.dumps(r)[:300]}")
    wb = r["workbookId"]

    ex = api(f"/v2/workbooks/{wb}/export", "POST",
             {"elementId": "prof", "format": {"type": "json"}})
    if ex.get("_error") or not ex.get("queryId"):
        raise RuntimeError(f"export failed: {json.dumps(ex)[:300]}")
    qid = ex["queryId"]

    # The download endpoint returns a status payload until the job finishes, so
    # poll until it is either a bare array or reports jobComplete.
    for _ in range(90):
        d = api(f"/v2/query/{qid}/download")
        if not (isinstance(d, dict) and d.get("_error")):
            if isinstance(d, list):
                return (d[0] if d else {}), wb
            if d.get("jobComplete") is True:
                rows = d.get("rows") or d.get("entries") or d.get("data") or []
                return (rows[0] if rows else {}), wb
        time.sleep(1)
    raise RuntimeError("timed out waiting for the profiling query")


def main() -> None:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("connectionId")
    ap.add_argument("db")
    ap.add_argument("schema")
    ap.add_argument("table")
    ap.add_argument("--via", choices=["auto", "workbook", "columns-only"], default="auto")
    ap.add_argument("--folder")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--max-dim-card", type=int, default=50)
    ap.add_argument("--out")
    a = ap.parse_args()

    warnings: list[dict] = []
    path = [a.db, a.schema, a.table]

    # 1. Resolve the table, and pick up the connection's dialect + writeback state.
    lk = api(f"/v2/connection/{a.connectionId}/lookup", "POST", {"path": path})
    if lk.get("_error") or not lk.get("inodeId"):
        sys.stderr.write(
            f"profile-table: could not resolve {'.'.join(path)} on connection "
            f"{a.connectionId}: {json.dumps(lk)[:300]}\n")
        sys.exit(1)
    inode = lk["inodeId"]

    conns = api("/v2/connections?limit=200").get("entries", [])
    conn = next((c for c in conns if c.get("connectionId") == a.connectionId), {})
    conn_type = conn.get("type")
    write_access = conn.get("writeAccess") is True

    # 2. Columns: names + types always; comments when the warehouse has them.
    cr = api(f"/v2/connections/tables/{inode}/columns?pageSize=500")
    raw_cols = cr.get("entries", []) if isinstance(cr, dict) else (cr or [])
    # Columns the connection hides are not queryable; profiling them would
    # propose a data model that cannot be built.
    raw_cols = [c for c in raw_cols if c.get("visibility", "included") == "included"]
    if not raw_cols:
        sys.stderr.write(f"profile-table: no columns returned for {'.'.join(path)}\n")
        sys.exit(1)

    for c in raw_cols:
        if any(ch in c["name"] for ch in "/-"):
            warnings.append({
                "code": "unusable-column-name",
                "detail": (f"{c['name']} contains '/' or '-'; bracket references to it "
                           "are ambiguous. Alias it in the data model."),
            })

    # 3. Stats.
    stats: dict = {}
    via = "columns-only"
    probe_wb = None
    folder = a.folder
    if a.via in ("auto", "workbook"):
        if not folder:
            # Default to the caller's home folder rather than skipping stats.
            folder = resolve_home_folder()
            if folder:
                sys.stderr.write(
                    f"profile-table: no --folder given; using your home folder "
                    f"({folder}) for the scratch workbook.\n")
        if not folder:
            msg = ("could not resolve a folder for the scratch workbook "
                   "(/v2/whoami -> /v2/members/{id}.homeFolderId failed); "
                   "pass --folder <folderId>")
            if a.via == "workbook":
                # An explicit --via workbook must not quietly become columns-only.
                sys.stderr.write(f"profile-table: {msg}\n")
                sys.exit(2)
            sys.stderr.write(f"profile-table: WARNING — {msg}; "
                             "falling back to --via columns-only (no statistics).\n")
            warnings.append({"code": "no-folder", "detail": msg})
        else:
            try:
                sql, aliases = build_profile_sql(path, raw_cols, norm_type(conn_type))
                stats, probe_wb = run_via_workbook(
                    a.connectionId, folder, sql, aliases,
                    f"ZZ profile {a.table} — delete me")
                via = "workbook"
            except Exception as e:  # noqa: BLE001 - degrade, never hard-fail
                if a.via == "workbook":
                    sys.stderr.write(f"profile-table: {e}\n")
                    sys.exit(1)
                warnings.append({"code": "stats-unavailable", "detail": str(e)[:300]})

    # Column stats are keyed positionally (N_i / D_i / MIN_i / MAX_i) because
    # warehouse column names are not safe as SQL aliases.
    def stat(prefix: str, i: int):
        for k in (f"{prefix}_{i}", f"{prefix}_{i}".lower()):
            if k in stats:
                return stats[k]
        return None

    row_count = stats.get("ROW_COUNT", stats.get("row_count"))
    columns = []
    for i, c in enumerate(raw_cols):
        name, t = c["name"], norm_type(c.get("type"))
        non_null, distinct = stat("N", i), stat("D", i)
        col = {
            "name": name,
            "sigmaName": friendly(name),
            "warehouseType": t,
            "description": c.get("description") or None,
            "nonNull": non_null,
            "distinct": distinct,
            "nullRate": (round(1 - non_null / row_count, 6)
                         if row_count and non_null is not None else None),
            "cardinalityRatio": (round(distinct / row_count, 6)
                                 if row_count and distinct else None),
            "min": stat("MIN", i),
            "max": stat("MAX", i),
        }
        # Role inference. With no stats we can still use type + name shape.
        if t in DATE_TYPES:
            role = "date"
        elif t in NUMERIC_TYPES and is_id_shaped(name):
            role = "identifier"
        elif t in NUMERIC_TYPES:
            role = "measure"
        elif distinct is not None and distinct <= a.max_dim_card:
            role = "dimension"
        elif distinct is None:
            role = "dimension" if t == "text" else "unknown"
        else:
            role = "high-cardinality"
        col["role"] = role
        columns.append(col)

    def why(c: dict, extra: str) -> str:
        bits = [f"type={c['warehouseType']}"]
        if c["distinct"] is not None:
            bits.append(f"{c['distinct']} distinct")
        if c["nullRate"] is not None:
            bits.append(f"{c['nullRate']:.2%} null")
        return f"{extra}; " + ", ".join(bits)

    dates = [c for c in columns if c["role"] == "date"]
    dates.sort(key=lambda c: -(c["distinct"] or 0))
    grain = [c for c in columns
             if c["cardinalityRatio"] is not None and c["cardinalityRatio"] > 0.95]
    if not grain:
        grain = [c for c in columns if c["role"] == "identifier"]
    measures = [c for c in columns if c["role"] == "measure"]
    dims = [c for c in columns if c["role"] == "dimension"]
    dims.sort(key=lambda c: (c["distinct"] or 10**9))
    excluded = [c for c in columns if c["role"] == "high-cardinality"]

    candidates = {
        "dateColumn": [{"name": c["name"], "sigmaName": c["sigmaName"],
                        "why": why(c, "date/datetime type")} for c in dates],
        "grainKey": [{"name": c["name"], "sigmaName": c["sigmaName"],
                      "why": why(c, "near-unique" if c["cardinalityRatio"]
                                else "id-shaped name")} for c in grain],
        "measures": [{"name": c["name"], "sigmaName": c["sigmaName"],
                      "why": why(c, "numeric, not id-shaped")} for c in measures],
        "dimensions": [{"name": c["name"], "sigmaName": c["sigmaName"],
                        "distinct": c["distinct"]} for c in dims],
        "excluded": [{"name": c["name"], "sigmaName": c["sigmaName"],
                      "why": why(c, "too many distinct values to filter on")}
                     for c in excluded],
    }

    if not dates:
        warnings.append({"code": "no-date-column",
                         "detail": "no date/datetime column — trend charts and "
                                   "period comparison are not possible."})
    if not measures:
        warnings.append({"code": "no-measures",
                         "detail": "no non-identifier numeric column — KPIs would "
                                   "be counts only."})
    # A Today()-anchored period tag silently yields an EMPTY current period on
    # stale demo data, so say so up front.
    for c in dates:
        if c["max"]:
            try:
                mx = _dt.date.fromisoformat(str(c["max"])[:10])
                age = (_dt.date.today() - mx).days
                if age > 400:
                    warnings.append({
                        "code": "stale-max-date",
                        "detail": (f"max({c['name']})={mx} is {age}d ago — anchor "
                                   "period tagging to the table's max date, not Today()."),
                    })
            except ValueError:
                pass
    if not write_access:
        warnings.append({
            "code": "no-write-access",
            "detail": ("this connection has write access OFF, so it cannot host input "
                       "tables (scenario/planning pages). Reads are unaffected. An admin "
                       "enables it under Administration > Connections > Enable write access."),
        })

    out = {
        "source": {
            "connectionId": a.connectionId, "connectionName": conn.get("name"),
            "connectionType": conn_type, "writeAccess": write_access,
            "writebacks": conn.get("writebacks") or [],
            "path": path, "inodeId": inode,
            "suggestedElementName": a.table,
        },
        "rowCount": row_count,
        "columns": columns,
        "candidates": candidates,
        "warnings": warnings,
        "profiledVia": via,
    }
    if probe_wb:
        out["probeWorkbookId"] = probe_wb
        out["probeFolderId"] = folder
        out["probeFolderWasAutoResolved"] = not a.folder
        out["cleanup"] = f"DELETE {BASE}/v2/files/{probe_wb}"

    text = json.dumps(out, indent=2)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
        print(f"profile-table: wrote {a.out} ({len(columns)} columns, via {via})")
    else:
        print(text)


if __name__ == "__main__":
    main()
