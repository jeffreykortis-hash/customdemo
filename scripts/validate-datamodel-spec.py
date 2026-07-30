#!/usr/bin/env python3
"""Pre-POST static validation for a Sigma DATA MODEL spec.

The sibling of validate-spec.py, for `/v2/dataModels/spec` instead of
`/v2/workbooks/spec`.

POST-time validation on the data-model endpoint is SHALLOW. Verified against
staging: a column whose formula references a column that does not exist is
accepted with HTTP 200 and `{"success":true,...}` — it only shows up later as a
column of type `error` in `mcp-describe.sh datamodel-element`. Two columns
sharing a `name` are also accepted, and Sigma silently renames the second to
`Name (1)`, quietly breaking every downstream formula that said `[Name]`.

So HTTP 200 proves almost nothing here. These checks catch the failures the
endpoint waves through, before you publish.

Verified reference rules (probed live, not guessed):
  * A PASSTHROUGH column pulls from the element's source and is written
    `[<SourcePrefix>/<Friendly Column Name>]`, where SourcePrefix is the
    element's `name` for a `warehouse-table` source, or `Custom SQL` for a
    `sql` source.
  * A COMPUTED column, and every metric, references SIBLING columns BARE:
    `[Price] - [Cost]`. Metrics may reference computed columns.
  * `format` is rejected outright ("Missing \"kind\" field") — the same masked
    error validate-spec.py guards against on workbooks.
  * `metrics[].timeline.comparison` accepts `comparisonPeriod` but REJECTS
    `direction` ("Invalid value: string").

Run before every POST/PUT (publish-datamodel.sh does this for you):

    python3 scripts/validate-datamodel-spec.py <datamodel-spec.json>

Exits 0 on success, non-zero on any issue (one issue per line on stderr).
"""
from __future__ import annotations

import json
import re
import sys


CHECKS = [
    "bare-refs-resolve",
    "source-prefix-matches-element",
    "no-duplicate-column-names",
    "no-format-key",
    "no-timeline-direction",
    "timeline-refs-resolve",
    "no-self-reference",
    # Star-schema checks. Every one is a NO-OP on a spec with no relationships,
    # so single-element BYOD specs are unaffected.
    "no-duplicate-element-names",
    "relationship-targets-resolve",
    "relationship-keys-resolve",
    "relationship-key-ids-unambiguous",
    "no-orphan-element",
]

# Prefixes that are legitimate on the left of a `/` in a bracket reference but
# are not the element's own source name.
_SPECIAL_PREFIXES = {"Custom SQL", "Metrics"}

_STRING_LITERAL = re.compile(r'"[^"]*"')
_BRACKET_REF = re.compile(r"\[([^\[\]]+)\]")


def _refs(formula: str) -> list[str]:
    """Bracket references in a formula, with string literals stripped first.

    `If([Date] > Date("2024-01-01"), "Current Period", "Prior Year")` must not
    treat anything inside the quoted strings as a reference.
    """
    if not formula:
        return []
    return _BRACKET_REF.findall(_STRING_LITERAL.sub('""', formula))


def _elements(spec: dict):
    for pi, page in enumerate(spec.get("pages", []) or []):
        for ei, el in enumerate(page.get("elements", []) or []):
            yield pi, ei, el


def _source_prefixes(el: dict) -> set[str] | None:
    """Valid `X` in `[X/Y]` for this element, or None if we can't tell.

    None means "don't check" — join/unknown sources expose their legs under
    names this static pass can't resolve.
    """
    kind = (el.get("source") or {}).get("kind")
    if kind == "warehouse-table":
        return {el.get("name") or ""} | _SPECIAL_PREFIXES
    if kind == "sql":
        return _SPECIAL_PREFIXES
    return None


def issues_bare_refs_resolve(spec: dict) -> list[str]:
    """Every bare `[Name]` must match a sibling column or metric name.

    This is the check that matters most: an unresolvable ref is accepted by the
    API and becomes a column of type `error`.
    """
    issues = []
    for pi, ei, el in _elements(spec):
        cols = el.get("columns") or []
        metrics = el.get("metrics") or []
        known = {c.get("name") for c in cols if c.get("name")}
        known |= {m.get("name") for m in metrics if m.get("name")}
        for label, items in (("columns", cols), ("metrics", metrics)):
            for ii, item in enumerate(items):
                for ref in _refs(item.get("formula", "")):
                    if "/" in ref:
                        continue  # source-qualified; handled by the prefix check
                    if ref not in known:
                        issues.append(
                            f"pages[{pi}].elements[{ei}].{label}[{ii}] "
                            f"({item.get('id')}, name={item.get('name')!r}): "
                            f"formula references [{ref}], which is not a column or "
                            "metric on this element. Sigma ACCEPTS this (HTTP 200) "
                            "and renders the column with type `error`."
                        )
    return issues


def issues_source_prefix(spec: dict) -> list[str]:
    issues = []
    for pi, ei, el in _elements(spec):
        allowed = _source_prefixes(el)
        if allowed is None:
            continue
        for ci, col in enumerate(el.get("columns") or []):
            for ref in _refs(col.get("formula", "")):
                if "/" not in ref:
                    continue
                prefix = ref.split("/", 1)[0]
                if prefix not in allowed:
                    kind = (el.get("source") or {}).get("kind")
                    expected = (
                        f"the element's name {el.get('name')!r}"
                        if kind == "warehouse-table"
                        else "'Custom SQL'"
                    )
                    issues.append(
                        f"pages[{pi}].elements[{ei}].columns[{ci}] ({col.get('id')}): "
                        f"formula prefix [{prefix}/...] does not match a {kind} "
                        f"source — expected {expected}."
                    )
    return issues


def issues_duplicate_column_names(spec: dict) -> list[str]:
    issues = []
    for pi, ei, el in _elements(spec):
        seen: dict[str, str] = {}
        for col in el.get("columns") or []:
            name = col.get("name")
            if not name:
                continue
            if name in seen:
                issues.append(
                    f"pages[{pi}].elements[{ei}]: columns {seen[name]} and "
                    f"{col.get('id')} share the name {name!r}. Sigma accepts this "
                    f"and silently renames the second to '{name} (1)', so every "
                    f"formula saying [{name}] binds to the FIRST one."
                )
            else:
                seen[name] = col.get("id")
    return issues


def issues_no_format(spec: dict) -> list[str]:
    issues = []
    for pi, ei, el in _elements(spec):
        for label in ("columns", "metrics"):
            for ii, item in enumerate(el.get(label) or []):
                if "format" in item:
                    issues.append(
                        f"pages[{pi}].elements[{ei}].{label}[{ii}] ({item.get('id')}): "
                        'has `format` — rejected with \'Missing "kind" field\'. '
                        "Set formatting in the UI, or on the consuming workbook element."
                    )
    return issues


def issues_timeline_direction(spec: dict) -> list[str]:
    issues = []
    for pi, ei, el in _elements(spec):
        for mi, m in enumerate(el.get("metrics") or []):
            comparison = ((m.get("timeline") or {}).get("comparison")) or {}
            if "direction" in comparison:
                issues.append(
                    f"pages[{pi}].elements[{ei}].metrics[{mi}] ({m.get('id')}): "
                    "timeline.comparison.direction is rejected "
                    "('Invalid value: string'). Use only `comparisonPeriod`."
                )
    return issues


def issues_timeline_refs(spec: dict) -> list[str]:
    issues = []
    for pi, ei, el in _elements(spec):
        col_ids = {c.get("id") for c in el.get("columns") or []}
        for mi, m in enumerate(el.get("metrics") or []):
            date_col = (m.get("timeline") or {}).get("dateColumnId")
            if date_col and date_col not in col_ids:
                issues.append(
                    f"pages[{pi}].elements[{ei}].metrics[{mi}] ({m.get('id')}): "
                    f"timeline.dateColumnId {date_col!r} is not a column id on "
                    "this element."
                )
    return issues


def issues_self_reference(spec: dict) -> list[str]:
    issues = []
    for pi, ei, el in _elements(spec):
        for ci, col in enumerate(el.get("columns") or []):
            name = col.get("name")
            if name and name in _refs(col.get("formula", "")):
                issues.append(
                    f"pages[{pi}].elements[{ei}].columns[{ci}] ({col.get('id')}): "
                    f"formula references its own name [{name}] — circular."
                )
    return issues



# --------------------------------------------------------------------------
# Star-schema checks
# --------------------------------------------------------------------------
def _has_relationships(spec: dict) -> bool:
    return any(el.get("relationships") for _, _, el in _elements(spec))


def issues_duplicate_element_names(spec: dict) -> list[str]:
    """An element's `name` is the formula prefix a WORKBOOK uses to reach its
    columns (`[<ElementName>/<Col>]`). Two elements sharing a name makes that
    reference ambiguous."""
    issues, seen = [], {}
    for pi, ei, el in _elements(spec):
        name = el.get("name")
        if not name:
            continue
        if name in seen:
            issues.append(
                f"pages[{pi}].elements[{ei}] ({el.get('id')}): element name {name!r} "
                f"is already used by {seen[name]}. The element name is the prefix a "
                "workbook uses to reference its columns, so this is ambiguous.")
        else:
            seen[name] = el.get("id")
    return issues


def issues_relationship_targets(spec: dict) -> list[str]:
    issues = []
    for pi, page in enumerate(spec.get("pages", []) or []):
        ids = {el.get("id") for el in page.get("elements", []) or []}
        for ei, el in enumerate(page.get("elements", []) or []):
            for ri, rel in enumerate(el.get("relationships") or []):
                tgt = rel.get("targetElementId")
                where = (f"pages[{pi}].elements[{ei}].relationships[{ri}] "
                         f"({rel.get('id')})")
                if not tgt:
                    issues.append(f"{where}: missing targetElementId.")
                elif tgt == el.get("id"):
                    issues.append(f"{where}: targets its own element.")
                elif tgt not in ids:
                    issues.append(
                        f"{where}: targetElementId {tgt!r} is not an element on this "
                        "page. Both ends of a relationship must be on the same page.")
    return issues


def issues_relationship_keys(spec: dict) -> list[str]:
    """Source keys resolve on the CARRYING element, target keys on the TARGET —
    the two sides resolve against different elements."""
    issues = []
    for pi, page in enumerate(spec.get("pages", []) or []):
        by_id = {el.get("id"): el for el in page.get("elements", []) or []}
        cols = {eid: {c.get("id") for c in (el.get("columns") or [])}
                for eid, el in by_id.items()}
        for ei, el in enumerate(page.get("elements", []) or []):
            for ri, rel in enumerate(el.get("relationships") or []):
                where = (f"pages[{pi}].elements[{ei}].relationships[{ri}] "
                         f"({rel.get('id')})")
                keys = rel.get("keys") or []
                if not keys:
                    issues.append(f"{where}: `keys` is empty — nothing to join on.")
                    continue
                tgt = rel.get("targetElementId")
                for ki, k in enumerate(keys):
                    src_id, tgt_id = k.get("sourceColumnId"), k.get("targetColumnId")
                    if src_id not in cols.get(el.get("id"), set()):
                        issues.append(
                            f"{where}.keys[{ki}]: sourceColumnId {src_id!r} is not a "
                            f"column on {el.get('id')!r} (the element carrying it).")
                    if tgt in cols and tgt_id not in cols[tgt]:
                        issues.append(
                            f"{where}.keys[{ki}]: targetColumnId {tgt_id!r} is not a "
                            f"column on the target element {tgt!r}.")
    return issues


def issues_relationship_key_ids_unambiguous(spec: dict) -> list[str]:
    """Column ids are NOT unique across elements in Sigma — a live 16-element
    model reuses the same id on two elements. Relationship keys resolve BY ID, so
    a colliding id can silently join the wrong table and return plausible rows."""
    if not _has_relationships(spec):
        return []
    owners: dict[str, list[str]] = {}
    for _, _, el in _elements(spec):
        for c in el.get("columns") or []:
            owners.setdefault(c.get("id"), []).append(el.get("id"))
    issues = []
    for _, _, el in _elements(spec):
        for rel in el.get("relationships") or []:
            for k in rel.get("keys") or []:
                for role in ("sourceColumnId", "targetColumnId"):
                    cid = k.get(role)
                    who = owners.get(cid, [])
                    if len(who) > 1:
                        issues.append(
                            f"relationship {rel.get('id')!r} {role}={cid!r} exists on "
                            f"MORE THAN ONE element ({', '.join(who)}). Relationship "
                            "keys resolve by id, so this can join the wrong table and "
                            "still return rows. Prefix column ids per element.")
    return issues


def issues_orphan_element(spec: dict) -> list[str]:
    """Only meaningful once a spec is a star — skipped entirely otherwise."""
    if not _has_relationships(spec):
        return []
    issues = []
    for pi, page in enumerate(spec.get("pages", []) or []):
        els = page.get("elements", []) or []
        if len(els) < 2:
            continue
        connected = set()
        for el in els:
            for rel in el.get("relationships") or []:
                connected.add(el.get("id"))
                connected.add(rel.get("targetElementId"))
        for ei, el in enumerate(els):
            if el.get("id") not in connected:
                issues.append(
                    f"pages[{pi}].elements[{ei}] ({el.get('id')}): not connected by any "
                    "relationship — either a forgotten relationship or dead weight.")
    return issues


def main() -> None:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: validate-datamodel-spec.py <datamodel-spec.json>\n")
        sys.exit(2)
    with open(sys.argv[1]) as f:
        spec = json.load(f)

    all_issues: list[tuple[str, str]] = []
    for tag, fn in [
        ("bare-refs-resolve",             lambda: issues_bare_refs_resolve(spec)),
        ("source-prefix-matches-element", lambda: issues_source_prefix(spec)),
        ("no-duplicate-column-names",     lambda: issues_duplicate_column_names(spec)),
        ("no-format-key",                 lambda: issues_no_format(spec)),
        ("no-timeline-direction",         lambda: issues_timeline_direction(spec)),
        ("timeline-refs-resolve",         lambda: issues_timeline_refs(spec)),
        ("no-self-reference",             lambda: issues_self_reference(spec)),
        ("no-duplicate-element-names",    lambda: issues_duplicate_element_names(spec)),
        ("relationship-targets-resolve",  lambda: issues_relationship_targets(spec)),
        ("relationship-keys-resolve",     lambda: issues_relationship_keys(spec)),
        ("relationship-key-ids-unambiguous",
                                          lambda: issues_relationship_key_ids_unambiguous(spec)),
        ("no-orphan-element",             lambda: issues_orphan_element(spec)),
    ]:
        for msg in fn():
            all_issues.append((tag, msg))

    if not all_issues:
        print(
            f"validate-datamodel-spec: {sys.argv[1]} — all {len(CHECKS)} checks passed"
        )
        sys.exit(0)

    for tag, msg in all_issues:
        sys.stderr.write(f"[{tag}] {msg}\n")
    sys.stderr.write(
        f"\nvalidate-datamodel-spec: {len(all_issues)} issue(s) found in {sys.argv[1]}\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
