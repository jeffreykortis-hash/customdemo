#!/usr/bin/env python3
"""Pre-POST static validation for a Sigma workbook spec.

The Sigma POST/PUT endpoints accept structurally broken specs and silently
rewrite the layout — most notably, per-page `pages[].layout` fields are
discarded, container children stack into a 1/13-wide single column when not
nested in their `<GridContainer>` in the layout XML, and `format` on columns
returns a misleading "Missing 'kind' field" error.

Run before every POST/PUT:

    python3 scripts/validate-spec.py workbooks/<name>/spec.json

Exits 0 on success, non-zero on any issue (one issue per line on stderr).
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET


CHECKS = [
    "no-per-page-layout",
    "elements-placed-in-layout",
    "containers-have-children",
    "control-id-unique",
]


def issues_per_page_layout(spec: dict) -> list[str]:
    issues = []
    for i, p in enumerate(spec.get("pages", [])):
        if p.get("layout"):
            issues.append(
                f"pages[{i}] ({p.get('id')}): has a per-page `layout` field. "
                "Sigma silently discards it — move to the top-level `layout` "
                "string with all <Page> elements as siblings."
            )
    return issues


def _parse_layout(layout: str) -> ET.Element | None:
    if not layout:
        return None
    # Multi-page layout is multiple <Page> siblings under one <?xml ... ?> decl —
    # not a valid single-root XML doc. Wrap to parse.
    cleaned = re.sub(r"<\?xml[^?]*\?>", "", layout).strip()
    wrapped = f"<root>{cleaned}</root>"
    try:
        return ET.fromstring(wrapped)
    except ET.ParseError as e:
        sys.stderr.write(f"validate-spec: layout XML failed to parse: {e}\n")
        return None


def issues_elements_placed(spec: dict, root: ET.Element | None) -> list[str]:
    if root is None:
        return ["no top-level `layout` field — workbook will have an auto-generated layout"]
    # TabbedContainer places elements too — omitting it flagged every tabbed
    # command-center layout as broken.
    placed_ids = {
        el.get("elementId")
        for el in root.iter()
        if el.tag in ("LayoutElement", "GridContainer", "TabbedContainer", "Tab")
    }
    issues = []
    for pi, p in enumerate(spec.get("pages", [])):
        for el in p.get("elements", []):
            eid = el.get("id")
            # `visibleAsSource` tables are hidden feeds for other elements and are
            # SUPPOSED to be unplaced; flagging them is a false positive.
            if el.get("visibleAsSource"):
                continue
            if eid and eid not in placed_ids:
                issues.append(
                    f"pages[{pi}].elements ({eid}, kind={el.get('kind')}): "
                    "not placed in the layout XML — will render at the page bottom or not at all."
                )
    return issues


def issues_containers_have_children(spec: dict, root: ET.Element | None) -> list[str]:
    if root is None:
        return []
    container_ids = [
        el.get("id")
        for p in spec.get("pages", [])
        for el in p.get("elements", [])
        if el.get("kind") == "container"
    ]
    issues = []
    for cid in container_ids:
        gc = next((el for el in root.iter("GridContainer") if el.get("elementId") == cid), None)
        if gc is None:
            issues.append(
                f"container element `{cid}`: no matching <GridContainer> in layout XML."
            )
        elif len(list(gc)) == 0:
            issues.append(
                f"container element `{cid}`: <GridContainer> has no nested children. "
                "Children must be nested INSIDE the <GridContainer>, not flat siblings."
            )
    return issues


# REMOVED: `no-column-format`. It asserted that a column `format` field is
# rejected with 'Missing "kind" field'. That is STALE — this repo's own
# api-cheatsheet.md says "Column format (POSTS FINE — the 'format is rejected'
# doc is stale)", and a two-page workbook carrying 43 formatted columns POSTed
# 200 on 2026-07-30. The check was emitting dozens of false errors per real
# spec and hard-failing publish-workbook.sh on perfectly valid dashboards.


def issues_control_id_unique(spec: dict) -> list[str]:
    seen: dict[str, str] = {}
    issues = []
    for p in spec.get("pages", []):
        for el in p.get("elements", []):
            if el.get("kind") != "control":
                continue
            cid = el.get("controlId")
            if not cid:
                continue
            if cid in seen:
                issues.append(
                    f"controlId `{cid}` duplicated on elements {seen[cid]} and {el.get('id')}. "
                    "controlId is workbook-wide unique."
                )
            else:
                seen[cid] = el.get("id")
    return issues


def notes_live_api(spec: dict) -> list[str]:
    """ADVISORY, not a hard failure.

    Every generated company dashboard should carry a live public-API panel so
    the asset visibly CALLS something rather than only rendering warehouse data
    (see sigma-company-dashboard/reference/api-actions.md). This is a note
    rather than an error on purpose: the same validator runs for cohort apps,
    input-table apps and scratch probes, and hard-failing those would be wrong.
    A build that deliberately ships without one can simply ignore it.
    """
    has_plugin = any(el.get("kind") == "plugin"
                     for p in spec.get("pages", []) for el in p.get("elements", []))
    if not has_plugin:
        return ["no `plugin` element found — a demoable asset should include a "
                "live public-API panel (plugins/public-api-live/, wired via "
                "PUBLIC_API_PLUGIN_ID). See reference/api-actions.md."]

    # A plugin element with an empty pluginId renders as a blank panel, which is
    # worse than omitting it — catch the unset-env-var case explicitly.
    blank = [el.get("id") for p in spec.get("pages", []) for el in p.get("elements", [])
             if el.get("kind") == "plugin" and not (el.get("pluginId") or "").strip()]
    if blank:
        return [f"plugin element(s) {blank} have an EMPTY pluginId — they will "
                "render as a blank panel. Register the plugin and set its id "
                "(e.g. PUBLIC_API_PLUGIN_ID) before publishing."]
    return []


def main() -> None:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: validate-spec.py <spec.json>\n")
        sys.exit(2)
    with open(sys.argv[1]) as f:
        spec = json.load(f)

    root = _parse_layout(spec.get("layout", ""))

    all_issues: list[tuple[str, str]] = []
    for tag, fn in [
        ("no-per-page-layout",          lambda: issues_per_page_layout(spec)),
        ("elements-placed-in-layout",   lambda: issues_elements_placed(spec, root)),
        ("containers-have-children",    lambda: issues_containers_have_children(spec, root)),
        ("control-id-unique",           lambda: issues_control_id_unique(spec)),
    ]:
        for msg in fn():
            all_issues.append((tag, msg))

    notes = notes_live_api(spec)

    if not all_issues:
        print(f"validate-spec: {sys.argv[1]} — all {len(CHECKS)} checks passed")
        for n in notes:
            print(f"validate-spec: NOTE [live-api] {n}")
        sys.exit(0)

    for tag, msg in all_issues:
        sys.stderr.write(f"[{tag}] {msg}\n")
    for n in notes:
        sys.stderr.write(f"NOTE [live-api] {n}\n")
    sys.stderr.write(f"\nvalidate-spec: {len(all_issues)} issue(s) found in {sys.argv[1]}\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
