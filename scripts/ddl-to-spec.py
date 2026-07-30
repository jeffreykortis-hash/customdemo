#!/usr/bin/env python3
"""Pasted DDL (or a structured file) -> a synthetic schema spec.

    scripts/ddl-to-spec.py <file|-> [--rows N] [--days N] [--anchor YYYY-MM-DD]
        [--name NAME] [--out spec.json]

Accepts:
  * `CREATE TABLE` statements, Snowflake or Databricks flavour (multiple ok)
  * a lite map      {"FCT_ORDERS": {"ORDER_ID": "string", "REVENUE": "decimal"}}
  * a columns list  [{"name","type","description"}]  — which is exactly what
    `scripts/profile-table.py` emits, so you can profile a real table you're
    allowed to see but not share, and generate a shareable synthetic twin.

DDL describes SHAPE, not BEHAVIOUR. Row counts, date windows, vocabularies and
the narrative (which measures grow, which decline, which category wins) are not
inferable and never guessed silently — they come back in `needsInput[]` for the
agent to put to a human, the same contract `profile-table.py` uses for its
`candidates`. Exit code is always 0: unanswered questions are data, not errors.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _synth as S  # noqa: E402

TYPE_MAP = [
    (r"^(VARCHAR|STRING|TEXT|CHAR|NVARCHAR|NCHAR)", "string"),
    (r"^(BOOLEAN|BOOL)", "boolean"),
    (r"^(DATE)$", "date"),
    (r"^(TIMESTAMP|DATETIME)", "timestamp"),
    (r"^(TINYINT|SMALLINT|INTEGER|INT|BIGINT)", "integer"),
    (r"^(DECIMAL|NUMERIC)", "decimal"),
    (r"^(FLOAT|DOUBLE|REAL)", "float"),
    (r"^(VARIANT|OBJECT|ARRAY|MAP|STRUCT|BINARY|GEOGRAPHY|GEOMETRY)", "unsupported"),
]
ID_SUFFIXES = ("_ID", "_KEY", "_NUMBER", "_NUM", "_CODE", "_UUID", "_GUID")
CURRENCY = {"REVENUE", "AMOUNT", "SALES", "PRICE", "COST", "GMV", "SPEND", "TOTAL",
            "FEE", "BALANCE", "ARR", "MRR", "CHARGE", "PREMIUM", "MARGIN"}
COUNTS = {"QTY", "QUANTITY", "UNITS", "COUNT", "VISITS", "SESSIONS", "ORDERS",
          "CLICKS", "IMPRESSIONS"}
AMBIGUOUS = {"LAT", "LATITUDE", "LON", "LNG", "LONGITUDE", "ZIP", "POSTAL", "AGE",
             "YEAR", "DURATION"}
CATEGORICAL_HINT = {"STATUS", "TYPE", "CATEGORY", "SEGMENT", "CHANNEL", "REGION",
                    "STATE", "TIER", "PLAN", "PRIORITY", "DIVISION", "METHOD"}


def canon_type(raw: str) -> str:
    t = raw.strip().upper()
    if t.startswith("NUMBER"):
        m = re.match(r"NUMBER\s*\(\s*\d+\s*,\s*(\d+)\s*\)", t)
        return "decimal" if m and int(m.group(1)) > 0 else "integer"
    for pat, out in TYPE_MAP:
        if re.match(pat, t):
            return out
    return "string"


def strip_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    return re.sub(r"--[^\n]*", " ", s)


def split_top_level(body: str) -> list[str]:
    """Split on commas at depth zero, tracking BOTH () and <>.

    Databricks `STRUCT<a:INT, b:STRING>` and `MAP<STRING,INT>` put commas inside
    angle brackets; a paren-only scanner splits them wrongly. This is the
    parser's most likely silent bug, so it's handled explicitly.
    """
    out, depth, cur, instr = [], 0, [], None
    for ch in body:
        if instr:
            cur.append(ch)
            if ch == instr:
                instr = None
            continue
        if ch in "'\"":
            instr = ch; cur.append(ch); continue
        if ch in "(<":
            depth += 1
        elif ch in ")>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def parse_ddl(text: str) -> list[dict]:
    tables = []
    for m in re.finditer(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:EXTERNAL\s+|TEMP\w*\s+|TRANSIENT\s+)?TABLE\s+"
            r"(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_.\"`\[\]]+)\s*\(",
            text, re.I):
        name = m.group(1).split(".")[-1].strip('"`[]')
        i, depth = m.end(), 1
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        body = text[m.end():i - 1]
        cols, pk, fks = [], None, {}
        for item in split_top_level(body):
            up = item.upper()
            if up.startswith(("PRIMARY KEY", "CONSTRAINT", "FOREIGN KEY", "UNIQUE",
                              "CHECK")):
                p = re.search(r"PRIMARY\s+KEY\s*\(\s*([A-Za-z0-9_\"`]+)", item, re.I)
                if p:
                    pk = p.group(1).strip('"`')
                f = re.search(r"FOREIGN\s+KEY\s*\(\s*([A-Za-z0-9_\"`]+)\s*\)\s*"
                              r"REFERENCES\s+([A-Za-z0-9_.\"`]+)\s*\(\s*([A-Za-z0-9_\"`]+)",
                              item, re.I)
                if f:
                    fks[f.group(1).strip('"`')] = (f.group(2).split(".")[-1].strip('"`'),
                                                   f.group(3).strip('"`'))
                continue
            cm = re.match(r"([A-Za-z0-9_]+|\"[^\"]+\"|`[^`]+`)\s+(.+)", item, re.S)
            if not cm:
                continue
            cname = cm.group(1).strip('"`')
            rest = cm.group(2)
            tm = re.match(r"([A-Za-z0-9_]+(?:\s*\([^)]*\))?(?:\s*<[^>]*>)?)", rest)
            ctype = canon_type(tm.group(1) if tm else "string")
            col = {"name": cname, "type": ctype, "raw": (tm.group(1) if tm else "")}
            if re.search(r"\bNOT\s+NULL\b", rest, re.I):
                col["notNull"] = True
            if re.search(r"\bPRIMARY\s+KEY\b", rest, re.I):
                pk = cname
            r = re.search(r"REFERENCES\s+([A-Za-z0-9_.\"`]+)\s*\(\s*([A-Za-z0-9_\"`]+)",
                          rest, re.I)
            if r:
                fks[cname] = (r.group(1).split(".")[-1].strip('"`'), r.group(2).strip('"`'))
            ck = re.search(r"CHECK\s*\([^)]*IN\s*\(([^)]*)\)", rest, re.I)
            if ck:
                col["checkValues"] = [v.strip().strip("'\"")
                                      for v in ck.group(1).split(",")]
            cm2 = re.search(r"COMMENT\s+'([^']*)'", rest, re.I)
            if cm2:
                col["description"] = cm2.group(1)
            cols.append(col)
        tables.append({"name": name, "columns": cols, "primaryKey": pk, "fks": fks})
    return tables


def infer(col: dict, table: dict, all_tables: list[dict], needs: list) -> dict:
    """First match wins. Mirrors profile-table.py's role heuristics so the two
    skills agree about what an _ID is."""
    n, t = col["name"], col["type"]
    up = n.upper()
    tn = table["name"]

    if t == "unsupported":
        needs.append({"code": "unsupported-type", "table": tn, "column": n,
                      "question": f"{n} is {col.get('raw','')} — omitted from generation. "
                                  "Give a concrete type if you want it fabricated."})
        return {}

    if n in table.get("fks", {}):
        ref_t, ref_c = table["fks"][n]
        return {"kind": "fk", "table": ref_t, "column": ref_c, "dimRows": 8,
                "inferred": True, "why": f"REFERENCES {ref_t}({ref_c})"}
    if table.get("primaryKey") == n:
        # Respect the DECLARED type: a VARCHAR primary key must not be filled
        # with bare integers just because it's a key.
        if t == "integer":
            return {"kind": "sequence", "start": 1, "inferred": True,
                    "why": "PRIMARY KEY, integer"}
        return {"kind": "id", "prefix": tn.split("_")[-1][:3].upper() + "-", "pad": 6,
                "start": 1, "inferred": True, "why": "PRIMARY KEY, non-integer"}
    if up.endswith(ID_SUFFIXES) or up == "ID":
        if t == "integer":
            return {"kind": "sequence", "start": 1, "inferred": True,
                    "why": "id-shaped name, integer"}
        return {"kind": "id", "prefix": tn.split("_")[-1][:3].upper() + "-", "pad": 6,
                "start": 1, "inferred": True, "why": "id-shaped name"}
    if t in ("date", "timestamp"):
        return {"kind": "date", "days": 730, "inferred": True, "why": f"{t} column"}
    if t == "boolean" or up.startswith(("IS_", "HAS_", "FLAG_")):
        needs.append({"code": "boolean-probability", "table": tn, "column": n,
                      "question": f"What share of rows should {n} be true?",
                      "default": 0.1})
        return {"kind": "boolean", "probability": 0.1, "inferred": True,
                "why": "boolean type or is_/has_ prefix"}
    if col.get("checkValues"):
        return {"kind": "categorical", "inferred": True, "why": "CHECK (... IN ...)",
                "values": [{"value": v} for v in col["checkValues"]]}
    if t in ("integer", "decimal", "float"):
        if any(w in up for w in AMBIGUOUS):
            needs.append({"code": "ambiguous-numeric", "table": tn, "column": n,
                          "question": f"{n} is numeric but looks like a coordinate, "
                                      "code or age rather than a measure. Measure, "
                                      "dimension, or omit?"})
        base = 100.0 if any(w in up for w in CURRENCY) else 3.0
        rnd = 2 if any(w in up for w in CURRENCY) else 0
        unit = "currency" if any(w in up for w in CURRENCY) else (
            "count" if any(w in up for w in COUNTS) else "number")
        return {"kind": "measure", "base": base, "round": rnd, "floor": 0,
                "unit": unit, "noise": {"amplitude": 0.08},
                "inferred": True, "why": f"numeric, name suggests {unit}"}
    # string
    needs.append({"code": "vocabulary", "table": tn, "column": n,
                  "question": f"What values should {n} take? Hand-written, "
                              "domain-plausible names are the single biggest "
                              "realism lever — generic 'Value 1..N' kills a demo."})
    kind = "categorical" if any(w in up for w in CATEGORICAL_HINT) else "vocabulary"
    return {"kind": kind, "inferred": True, "why": "string column, vocabulary unknown",
            "values": [{"value": f"{S.friendly(n)} A"}, {"value": f"{S.friendly(n)} B"},
                       {"value": f"{S.friendly(n)} C"}]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--rows", type=int, default=5000)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--anchor")
    ap.add_argument("--name")
    ap.add_argument("--out")
    a = ap.parse_args()

    text = sys.stdin.read() if a.input == "-" else open(a.input).read()
    needs: list = []

    parsed = None
    try:
        j = json.loads(text)
        if isinstance(j, dict) and "tables" in j:
            print(json.dumps(j, indent=2)); return          # already a spec
        if isinstance(j, dict) and "columns" in j:          # profile-table.py output
            parsed = [{"name": j.get("source", {}).get("path", ["T"])[-1],
                       "columns": [{"name": c["name"], "type": c.get("warehouseType", "string"),
                                    "description": c.get("description")}
                                   for c in j["columns"]],
                       "primaryKey": None, "fks": {}}]
        elif isinstance(j, dict):                            # lite map
            parsed = [{"name": t, "columns": [{"name": k, "type": v}
                                              for k, v in cols.items()],
                       "primaryKey": None, "fks": {}} for t, cols in j.items()]
        elif isinstance(j, list):
            parsed = [{"name": a.name or "FCT_MAIN",
                       "columns": [{"name": c["name"], "type": c.get("type", "string")}
                                   for c in j], "primaryKey": None, "fks": {}}]
    except json.JSONDecodeError:
        parsed = parse_ddl(strip_comments(text))

    if not parsed:
        sys.stderr.write("ddl-to-spec: found no CREATE TABLE statements or usable JSON\n")
        sys.exit(2)

    # The largest table is the fact; the rest are dimensions. Stated, not silent.
    fact = max(parsed, key=lambda t: len(t["columns"]))
    tables = []
    for t in parsed:
        is_fact = t is fact
        cols = []
        for c in t["columns"]:
            g = infer(c, t, parsed, needs)
            if not g:
                continue
            entry = {"name": c["name"], "type": c["type"], "generator": g}
            if c.get("description"):
                entry["sourceComment"] = c["description"]
            cols.append(entry)
        tables.append({
            "name": t["name"],
            "role": "fact" if is_fact else "dimension",
            "rows": a.rows if is_fact else 8,
            **({"primaryKey": t["primaryKey"]} if t.get("primaryKey") else {}),
            "columns": cols,
        })

    needs.insert(0, {"code": "volume-and-window", "table": fact["name"],
                     "question": f"Row count and date window? Defaulted to {a.rows} rows "
                                 f"over {a.days} days from {a.anchor or 'today-2y'}."})
    needs.append({"code": "narrative", "table": fact["name"],
                  "question": "Which measures grow and which decline, how strong is "
                              "seasonality, and which category should win? The story "
                              "lives in these constants. Default: +15%/yr, 16% annual "
                              "seasonality, weekend lift."})

    anchor = a.anchor or (_dt.date.today() - _dt.timedelta(days=a.days)).isoformat()
    spec = {"specVersion": 1, "name": a.name or "Synthetic dataset", "synthetic": True,
            "generatedAt": _dt.date.today().isoformat(), "anchorDate": anchor,
            "needsInput": needs, "tables": tables}

    text_out = json.dumps(spec, indent=2)
    if a.out:
        open(a.out, "w").write(text_out + "\n")
        print(f"ddl-to-spec: wrote {a.out} — {len(tables)} table(s), "
              f"{len(needs)} question(s) in needsInput")
        for q in needs:
            print(f"  ? [{q['code']}] {q.get('table','')}.{q.get('column','')}: "
                  f"{q['question'][:96]}")
    else:
        print(text_out)


if __name__ == "__main__":
    main()
