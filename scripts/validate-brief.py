#!/usr/bin/env python3
"""Gate a discovery build-brief before anything is generated from it.

    python3 scripts/validate-brief.py <brief.json> [--pre-gate] [--json]

`intake-artifacts.py` inventories the artifacts; the agent reads them and fills
`decisions`. This is the gate between "I read some screenshots" and "I published
a data model." It exists because the failure mode of artifact-driven generation
is a brief that reads as authoritative and is quietly half invented — a metric
definition nobody stated, a threshold OCR'd off a blurry tile, a layout chosen
because the agent liked it.

So every decision carries `origin`, and `observed`/`stated` origins carry a
`source` locator back to a specific artifact:

    {"value":"command-center","origin":"observed",
     "source":{"artifact":"a2","region":"KPI band + left tab strip"}}

    origin: observed  — seen in an image        (needs source.artifact + region)
            stated    — said in a transcript    (needs source.artifact + line|timestamp)
            asked     — the human told us directly
            inferred  — the agent's judgement   (needs confirmed:true)
            default   — a repo default          (needs confirmed:true)

`--pre-gate` skips the human-confirmation check, so you can validate a draft
before putting it in front of anyone. Everything else still applies.

Exits 0 when clean (warnings still print), non-zero on any issue.
"""
from __future__ import annotations

import json
import sys

CHECKS = [
    "required-fields",
    "artifact-refs-resolve",
    "origin-present",
    "source-locator-present",
    "inferred-confirmed",
    "no-number-from-image",
    "no-definition-from-image",
    "layout-in-catalog",
    "layout-prerequisites",
    "page2-consistency",
    "data-sourcing-valid",
    "kpis-comparative",
    "pii-resolved",
    "needs-input-resolved",
    "human-confirmed",
]

LAYOUTS = {"exec-brief", "command-center", "analyst-detail",
           "comparison-variance", "app-shell", "product-surface"}
SOURCING = {"sample", "byod", "synthetic"}
PAGE2 = {"input-table", "cohort-builder", "both", "none"}
ORIGINS = {"observed", "stated", "asked", "inferred", "default"}
IMAGE_KINDS = {"image", "pdf"}

# Decision fields that are single {value, origin, ...} objects.
SCALAR_FIELDS = ["dataSourcing", "layout", "pluginConcept", "page2Pattern", "brandKit"]
# Decision fields that are lists of {…, origin, …} objects.
LIST_FIELDS = ["pages", "kpis", "charts", "filters"]


def _decisions(brief: dict):
    """Yield (path, obj) for every provenanced object in `decisions`."""
    d = brief.get("decisions") or {}
    for f in SCALAR_FIELDS:
        obj = d.get(f)
        if isinstance(obj, dict):
            yield f"decisions.{f}", obj
    for f in LIST_FIELDS:
        for i, obj in enumerate(d.get(f) or []):
            if isinstance(obj, dict):
                yield f"decisions.{f}[{i}]", obj


def issues_required_fields(brief: dict):
    for key in ("specVersion", "artifacts", "decisions", "needsInput"):
        if key not in brief:
            yield f"missing top-level `{key}`"
    if not (brief.get("company") or {}).get("name"):
        yield "company.name is unset — the whole build is branded on it"
    if not brief.get("artifacts"):
        yield "artifacts[] is empty; a brief with no artifacts is just an interview — " \
              "use sigma-company-dashboard directly instead"
    d = brief.get("decisions") or {}
    for f in ("dataSourcing", "layout"):
        if not d.get(f):
            yield f"decisions.{f} is unset — nothing can be generated without it"
    if not d.get("kpis"):
        yield "decisions.kpis[] is empty — a dashboard with no KPI band is not this repo's shape"


def issues_artifact_refs(brief: dict):
    ids = {a.get("id") for a in brief.get("artifacts") or []}
    unreadable = {a.get("id") for a in brief.get("artifacts") or []
                  if not a.get("readable")}
    for path, obj in _decisions(brief):
        src = obj.get("source")
        for s in (src if isinstance(src, list) else [src] if src else []):
            ref = (s or {}).get("artifact")
            if ref and ref not in ids:
                yield f"{path}.source.artifact={ref!r} matches no artifacts[].id"
            elif ref in unreadable:
                yield (f"{path} cites {ref!r}, which intake marked UNREADABLE — "
                       f"a citation to a file nobody could read is worse than none")
    for f in brief.get("piiFlags") or []:
        if f.get("artifact") and f["artifact"] not in ids:
            yield f"piiFlags entry cites unknown artifact {f['artifact']!r}"


def issues_origin_present(brief: dict):
    for path, obj in _decisions(brief):
        origin = obj.get("origin")
        if not origin:
            yield f"{path} has no `origin` — every decision states where it came from"
        elif origin not in ORIGINS:
            yield f"{path}.origin={origin!r} is not one of {sorted(ORIGINS)}"


def issues_source_locator(brief: dict):
    arts = {a.get("id"): a for a in brief.get("artifacts") or []}
    for path, obj in _decisions(brief):
        if obj.get("origin") not in ("observed", "stated"):
            continue
        srcs = obj.get("source")
        srcs = srcs if isinstance(srcs, list) else [srcs] if srcs else []
        if not srcs:
            yield f"{path}.origin={obj['origin']!r} but no `source` — cite the artifact"
            continue
        for s in srcs:
            art = arts.get(s.get("artifact"), {})
            kind = art.get("kind")
            if obj["origin"] == "observed":
                if kind and kind not in IMAGE_KINDS:
                    yield (f"{path}.origin=\"observed\" cites {s.get('artifact')!r}, a "
                           f"{kind} — `observed` means seen in an image; a transcript is `stated`")
                if not s.get("region"):
                    yield f"{path}.source needs a `region` (which part of the screenshot)"
            else:
                if kind in IMAGE_KINDS:
                    yield (f"{path}.origin=\"stated\" cites {s.get('artifact')!r}, an image — "
                           f"a screenshot states nothing; that is `observed`")
                if not (s.get("line") or s.get("timestamp") or s.get("quote")):
                    yield f"{path}.source needs a `line`, `timestamp` or `quote`"


def issues_inferred_confirmed(brief: dict):
    for path, obj in _decisions(brief):
        if obj.get("origin") in ("inferred", "default") and not obj.get("confirmed"):
            yield (f"{path}.origin={obj['origin']!r} and confirmed is not true — the agent's "
                   f"own judgement goes in front of the human before it drives a build")


def _image_sourced(obj: dict, arts: dict) -> bool:
    srcs = obj.get("source")
    srcs = srcs if isinstance(srcs, list) else [srcs] if srcs else []
    return bool(srcs) and all(
        arts.get((s or {}).get("artifact"), {}).get("kind") in IMAGE_KINDS for s in srcs)


def issues_number_from_image(brief: dict):
    """A number read off a screenshot is the client's real figure AND an OCR guess.
    It is never a baseline, a target, or a threshold in what we generate."""
    arts = {a.get("id"): a for a in brief.get("artifacts") or []}
    numeric_keys = ("baseline", "target", "threshold", "currentValue", "priorValue", "goal")
    for path, obj in _decisions(brief):
        if not _image_sourced(obj, arts):
            continue
        for k in numeric_keys:
            if obj.get(k) not in (None, "", []):
                yield (f"{path}.{k}={obj[k]!r} is sourced only from an image. Numbers on a "
                       f"screenshot are the client's real figures read through a downscaler — "
                       f"reproduce the SHAPE, generate the VALUES. Drop it, or get it stated.")


def issues_definition_from_image(brief: dict):
    """A tile label is a name. A definition has to be said out loud by someone."""
    arts = {a.get("id"): a for a in brief.get("artifacts") or []}
    for i, k in enumerate(((brief.get("decisions") or {}).get("kpis") or [])):
        if not isinstance(k, dict):
            continue
        if k.get("definition") and _image_sourced(k, arts):
            yield (f"decisions.kpis[{i}] ({k.get('name')!r}) has a `definition` sourced only "
                   f"from an image. A tile label names a metric; it never defines one. "
                   f"Get the numerator/denominator/filter from a transcript or the human.")


def issues_layout_catalog(brief: dict):
    lay = (brief.get("decisions") or {}).get("layout") or {}
    val = lay.get("value")
    if val and val not in LAYOUTS:
        yield (f"decisions.layout.value={val!r} is not in the catalog "
               f"({sorted(LAYOUTS)}) — see sigma-company-dashboard/reference/layouts.md")


def issues_layout_prereq(brief: dict):
    d = brief.get("decisions") or {}
    lay = (d.get("layout") or {})
    val = lay.get("value")
    if val == "app-shell" and not lay.get("writebackConnectionConfirmed"):
        yield ("layout app-shell means input tables, which need a WRITEBACK-enabled "
               "connection. Set decisions.layout.writebackConnectionConfirmed after "
               "scripts/api/list-connections.sh --writable")
    if val == "comparison-variance" and not lay.get("tagColumn"):
        yield ("layout comparison-variance requires the column that carries the "
               "period/scenario/entity tag — set decisions.layout.tagColumn")
    if val in ("exec-brief", "comparison-variance") and not lay.get("unverifiedAcknowledged"):
        yield (f"layout {val} is PROPOSED, not verified, in layouts.md. Say so to the user "
               f"and set decisions.layout.unverifiedAcknowledged")


def issues_page2(brief: dict):
    d = brief.get("decisions") or {}
    p2 = (d.get("page2Pattern") or {})
    val = p2.get("value")
    if val and val not in PAGE2:
        yield f"decisions.page2Pattern.value={val!r} is not one of {sorted(PAGE2)}"
    if val in ("input-table", "both") and not p2.get("writebackConnectionConfirmed"):
        yield (f"page2Pattern {val!r} builds input tables — confirm a writeback-enabled "
               f"connection and set page2Pattern.writebackConnectionConfirmed, or the page "
               f"renders and silently saves nothing")


def issues_data_sourcing(brief: dict):
    ds = (brief.get("decisions") or {}).get("dataSourcing") or {}
    val = ds.get("value")
    if val and val not in SOURCING:
        yield f"decisions.dataSourcing.value={val!r} is not one of {sorted(SOURCING)}"
    if val == "byod" and not ds.get("table"):
        yield "dataSourcing byod needs the table: set dataSourcing.table to DB.SCHEMA.TABLE"
    if val == "synthetic" and not ds.get("syntheticBannerPlanned"):
        yield ("dataSourcing synthetic requires the visible \"Synthetic demo data\" banner "
               "in the header — set dataSourcing.syntheticBannerPlanned")
    if val == "byod" and any(
            f for f in (brief.get("piiFlags") or []) if not f.get("resolved")):
        yield "dataSourcing byod with unresolved piiFlags — resolve them before touching real data"


def issues_kpis_comparative(brief: dict):
    for i, k in enumerate(((brief.get("decisions") or {}).get("kpis") or [])):
        if not isinstance(k, dict):
            yield f"decisions.kpis[{i}] is not an object"
            continue
        if not k.get("name"):
            yield f"decisions.kpis[{i}] has no name"
        if not k.get("comparison"):
            yield (f"decisions.kpis[{i}] ({k.get('name')!r}) has no `comparison` — every card "
                   f"in this repo is comparative. Ask what it is compared against.")


def issues_pii_resolved(brief: dict):
    for i, f in enumerate(brief.get("piiFlags") or []):
        if not f.get("resolved"):
            yield (f"piiFlags[{i}] ({f.get('kind')}, {f.get('count')} hit(s) in "
                   f"{f.get('artifact')}) is unresolved — set a `resolution` "
                   f"(cropped / excluded / confirmed-non-sensitive) and resolved:true")


def issues_needs_input(brief: dict):
    open_q = [q for q in brief.get("needsInput") or []
              if not (q.get("answer") or q.get("resolved"))]
    for q in open_q:
        yield f"needsInput [{q.get('code')}] unanswered: {str(q.get('question'))[:110]}"


def issues_human_confirmed(brief: dict):
    if not brief.get("confirmedBy"):
        yield ("confirmedBy is unset. The brief goes in front of the human as a readout, "
               "they correct it, and you record who confirmed and when. Artifacts do not "
               "replace that gate. (Use --pre-gate to validate a draft.)")


def warnings(brief: dict):
    arts = brief.get("artifacts") or []
    kinds = {a.get("kind") for a in arts}
    if kinds and not (kinds & {"transcript"}):
        yield ("form-without-function", "screenshots but no transcript — you can copy the "
               "shape but every metric meaning in this brief is a guess")
    if kinds and not (kinds & IMAGE_KINDS):
        yield ("function-without-form", "transcript but no screenshot — layout is a "
               "recommendation from layouts.md, not evidence. Say so.")
    for a in arts:
        for fl in a.get("flags") or []:
            if fl in ("needs-crop", "low-resolution", "full-page-scroll-capture"):
                yield (fl, f"{a.get('id')} {a.get('path')} — anything read off this is "
                           f"low-confidence; prefer section crops")
    for q in brief.get("stated", {}).get("quotes") or []:
        if q.get("useInWorkbook"):
            yield ("verbatim-on-surface", "a client verbatim is marked useInWorkbook — a "
                   "quote from their call does not belong on a shareable surface")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 1:
        sys.stderr.write("usage: validate-brief.py <brief.json> [--pre-gate] [--json]\n")
        sys.exit(2)
    with open(args[0]) as f:
        brief = json.load(f)

    runners = [
        ("required-fields", issues_required_fields),
        ("artifact-refs-resolve", issues_artifact_refs),
        ("origin-present", issues_origin_present),
        ("source-locator-present", issues_source_locator),
        ("inferred-confirmed", issues_inferred_confirmed),
        ("no-number-from-image", issues_number_from_image),
        ("no-definition-from-image", issues_definition_from_image),
        ("layout-in-catalog", issues_layout_catalog),
        ("layout-prerequisites", issues_layout_prereq),
        ("page2-consistency", issues_page2),
        ("data-sourcing-valid", issues_data_sourcing),
        ("kpis-comparative", issues_kpis_comparative),
        ("pii-resolved", issues_pii_resolved),
        ("needs-input-resolved", issues_needs_input),
    ]
    if "--pre-gate" not in flags:
        runners.append(("human-confirmed", issues_human_confirmed))

    found: list[tuple[str, str]] = []
    for tag, fn in runners:
        for msg in fn(brief):
            found.append((tag, msg))
    warns = list(warnings(brief))

    if "--json" in flags:
        print(json.dumps({"file": args[0], "checks": len(runners),
                          "issues": [{"check": t, "message": m} for t, m in found],
                          "warnings": [{"check": t, "message": m} for t, m in warns]},
                         indent=2))
        sys.exit(1 if found else 0)

    for tag, msg in warns:
        sys.stderr.write(f"[warn {tag}] {msg}\n")
    if not found:
        print(f"validate-brief: {args[0]} — all {len(runners)} checks passed"
              + (f", {len(warns)} warning(s)" if warns else ""))
        sys.exit(0)
    for tag, msg in found:
        sys.stderr.write(f"[{tag}] {msg}\n")
    sys.stderr.write(f"\nvalidate-brief: {len(found)} issue(s) found in {args[0]}\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
