#!/usr/bin/env python3
"""Discovery artifacts (screenshots + call transcripts) -> a build-brief skeleton.

    scripts/intake-artifacts.py <path|dir> [<path|dir> ...] [--company NAME]
        [--domain acme.com] [--out brief.json]

The optional front door: instead of interviewing the user, read what they already
have. Point this at a folder of dashboard screenshots and call transcripts and it
inventories every file, says HOW each one must be read (and which ones cannot be
read at all), flags the ones that will read badly, points at the lines in a
transcript that carry a decision, and emits a `brief.json` skeleton.

What it does NOT do: decide anything. Classification is mechanical; extraction is
the agent's job with the Read tool, and every decision it derives has to carry a
provenance locator back to a file. Unanswerable questions come back in
`needsInput[]` — the same contract `ddl-to-spec.py` and `profile-table.py` use.
Exit code is always 0: an unreadable artifact is data, not an error.

Two rules this script exists to enforce mechanically:
  * A screenshot's long edge over 1568px is DOWNSCALED before the model sees it,
    so a full-page 3000px dashboard grab loses exactly the axis labels and card
    values you wanted. Flagged as `needs-crop` with the crop list to ask for.
  * Audio/video is not readable here, and neither is .docx/.pptx/.heic. Flagged
    `unreadable` with the specific remedy, instead of being silently skipped.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import struct
import sys

# Images whose long edge exceeds this are downscaled before the model sees them.
MAX_LONG_EDGE = 1568
# Below this width, dashboard card values and axis labels stop being legible.
MIN_LEGIBLE_WIDTH = 900
# Per-image ceiling on the API.
MAX_BYTES = 5 * 1024 * 1024

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# Readable-shaped but not by the Read tool — each needs a conversion step.
CONVERT_EXT = {
    ".heic": "sips -s format png <in> --out <out>.png   # macOS, no install",
    ".heif": "sips -s format png <in> --out <out>.png",
    ".tif": "sips -s format png <in> --out <out>.png",
    ".tiff": "sips -s format png <in> --out <out>.png",
    ".avif": "sips -s format png <in> --out <out>.png",
    ".docx": "textutil -convert txt <in>              # macOS; or unzip word/document.xml",
    ".doc": "textutil -convert txt <in>",
    ".pptx": "unzip -o <in> 'ppt/slides/*.xml' and read the text runs, or export to PDF",
    ".rtf": "textutil -convert txt <in>",
}
AV_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm",
          ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
TRANSCRIPT_EXT = {".txt", ".md", ".vtt", ".srt", ".text", ".log", ".csv"}

# Transcript signal cues. These are POINTERS, not conclusions — the agent reads
# the cited lines and decides. Every phrase here earned its place by being the
# thing that actually determined a build decision on a real call.
SIGNALS = {
    "writeback": [
        r"\bspreadsheet\b", r"\bexcel\b", r"\bgoogle sheet", r"\bmanually? (?:update|enter|type)",
        r"\btype (?:it|them|the numbers) in\b", r"\bsubmit\b", r"\bapprov", r"\bsign off\b",
        r"\badjust(?:ment|ed|s)?\b", r"\boverride\b", r"\bforecast\b", r"\bbudget\b",
        r"\bplan(?:ning)?\b", r"\bwhat.?if\b", r"\bscenario\b", r"\bsend it back\b",
    ],
    "segmentation": [
        r"\bcohort\b", r"\bsegment\b", r"\bfilter (?:it )?down\b", r"\bnarrow (?:it )?down\b",
        r"\btarget list\b", r"\bthe ones (?:that|who)\b", r"\bbuild a list\b",
        r"\bsaved (?:list|view|filter)\b", r"\bbook of business\b", r"\bmy accounts\b",
    ],
    "cadence": [
        r"\bevery (?:morning|day|monday|week|month|quarter)\b", r"\bdaily\b", r"\bweekly\b",
        r"\bmonthly\b", r"\bquarterly\b", r"\bmonth.end\b", r"\bclose\b", r"\bstandup\b",
        r"\bmonday morning\b", r"\bboard meeting\b", r"\breal.?time\b",
    ],
    "exception": [
        r"\bvariance\b", r"\boutlier\b", r"\bexception\b", r"\bflag(?:ged|s)?\b",
        r"\bthreshold\b", r"\bred\b", r"\bbreach", r"\boff (?:track|plan|target)\b",
        r"\bmiss(?:ing|ed|es)? (?:target|plan|quota)\b", r"\bdrill (?:in|down)\b",
    ],
    "comparison": [
        r"\bvs\.?\b", r"\bversus\b", r"\bcompared? to\b", r"\byear over year\b", r"\byoy\b",
        r"\blast (?:year|month|quarter|week)\b", r"\bprior (?:year|period|month)\b",
        r"\bbudget vs\b", r"\bplan vs\b", r"\bactuals?\b", r"\bbaseline\b",
    ],
    "pain": [
        r"\btakes (?:me )?(?:hours|days|all|forever)\b", r"\bmanual\b", r"\bcopy.?paste\b",
        r"\bthree (?:different )?systems\b", r"\bno visibility\b", r"\bstale\b",
        r"\bout of date\b", r"\bcan.?t (?:see|tell|find)\b", r"\bnobody (?:knows|trusts)\b",
        r"\bdon.?t trust\b", r"\bby hand\b",
    ],
    "persona": [
        r"\b(?:vp|svp|evp|cfo|coo|ceo|cro|cio)\b", r"\bdirector\b", r"\bcontroller\b",
        r"\banalyst\b", r"\bmanager\b", r"\bthe board\b", r"\bthe reps?\b", r"\bops team\b",
        r"\bexec(?:utive)?s?\b", r"\bstore manager\b", r"\bregional\b",
    ],
    "data-source": [
        r"\bsnowflake\b", r"\bdatabricks\b", r"\bredshift\b", r"\bbigquery\b", r"\bsql server\b",
        r"\bsalesforce\b", r"\bnetsuite\b", r"\bworkday\b", r"\bsap\b", r"\btableau\b",
        r"\bpower ?bi\b", r"\blooker\b", r"\bwarehouse\b", r"\bwe don.?t have (?:the )?data\b",
        r"\bno data yet\b", r"\bmock(?:ed)? up\b",
    ],
}

# Flagged, never auto-stripped: the human decides what leaves their org.
PII_PATTERNS = [
    ("email", r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    ("phone", r"(?:\+\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
    ("long-digit-run", r"\b\d{13,19}\b"),
    ("ssn-shaped", r"\b\d{3}-\d{2}-\d{4}\b"),
    ("ip", r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    ("secret-shaped", r"(?i)\b(?:api[_-]?key|secret|token|password|bearer)\b\s*[:=]?\s*\S{8,}"),
]
# Not PII — numbers that will get mistranscribed and then quietly become a
# metric definition. Every one has to be confirmed with a human out loud.
NUMBER_PATTERNS = [
    ("currency", r"[$£€]\s?\d[\d,.]*\s?(?:k|m|b|million|billion|thousand)?\b"),
    ("percent", r"\b\d[\d.]*\s?(?:%|percent|percentage points?|bps)\b"),
    ("spelled-number", r"(?i)\b(?:fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
                       r"hundred|thousand|million|billion)\b"),
    # Comparator + number. This is the shape a THRESHOLD takes when spoken aloud
    # ("under four hours", "off plan by more than fifteen percent"), and a
    # threshold mis-heard by one digit becomes a wrong metric definition.
    ("threshold", r"(?i)\b(?:under|over|above|below|at least|at most|more than|less than|"
                  r"greater than|fewer than|no more than)\s+"
                  r"(?:\d[\d,.]*|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
                  r"twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
                  r"hundred|thousand|million|billion)\b"),
]

SPEAKER_RE = re.compile(r"^\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?([A-Z][\w'’.\- ]{1,28}):\s")
TS_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{3})?\b")


# ---------------------------------------------------------------- image probes
def _png_size(b: bytes):
    if b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR":
        return struct.unpack(">II", b[16:24])
    return None


def _gif_size(b: bytes):
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", b[6:10])
    return None


def _bmp_size(b: bytes):
    if b[:2] == b"BM" and len(b) >= 26:
        w, h = struct.unpack("<ii", b[18:26])
        return abs(w), abs(h)
    return None


def _webp_size(b: bytes):
    if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    chunk = b[12:16]
    if chunk == b"VP8X" and len(b) >= 30:
        w = int.from_bytes(b[24:27], "little") + 1
        h = int.from_bytes(b[27:30], "little") + 1
        return w, h
    if chunk == b"VP8 ":
        i = b.find(b"\x9d\x01\x2a", 16, 64)
        if i > 0 and len(b) >= i + 7:
            w = int.from_bytes(b[i + 3:i + 5], "little") & 0x3FFF
            h = int.from_bytes(b[i + 5:i + 7], "little") & 0x3FFF
            return w, h
    if chunk == b"VP8L" and len(b) >= 25:
        bits = int.from_bytes(b[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _jpeg_size(path: str):
    """Walk the JPEG segment chain to the first SOF marker."""
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return None
        while True:
            byte = f.read(1)
            while byte and byte != b"\xff":
                byte = f.read(1)
            marker = f.read(1)
            while marker == b"\xff":
                marker = f.read(1)
            if not marker:
                return None
            m = marker[0]
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                continue
            length_bytes = f.read(2)
            if len(length_bytes) < 2:
                return None
            length = struct.unpack(">H", length_bytes)[0]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                data = f.read(5)
                if len(data) < 5:
                    return None
                h, w = struct.unpack(">HH", data[1:5])
                return w, h
            f.seek(length - 2, os.SEEK_CUR)


def image_size(path: str, ext: str):
    try:
        if ext in (".jpg", ".jpeg"):
            return _jpeg_size(path)
        with open(path, "rb") as f:
            head = f.read(64)
        for probe in (_png_size, _gif_size, _bmp_size, _webp_size):
            got = probe(head)
            if got:
                return got
    except OSError:
        return None
    return None


def pdf_pages(path: str):
    """Cheap page count — count /Type /Page objects. Good enough to decide
    whether the Read call needs an explicit `pages` range (>10 pages: required)."""
    try:
        with open(path, "rb") as f:
            blob = f.read(4 * 1024 * 1024)
    except OSError:
        return None
    n = len(re.findall(rb"/Type\s*/Page[^s]", blob))
    m = re.search(rb"/Count\s+(\d+)", blob)
    if m:
        n = max(n, int(m.group(1)))
    return n or None


# ----------------------------------------------------------- classify + inspect
def classify(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in AV_EXT:
        return "av"
    if ext in CONVERT_EXT:
        return "needs-conversion"
    if ext in TRANSCRIPT_EXT:
        return "transcript"
    if ext == ".json":
        return "transcript"
    return "unknown"


def inspect_image(path: str, ext: str, size_bytes: int) -> dict:
    out: dict = {"readWith": "Read", "readable": True, "flags": [], "notes": []}
    dims = image_size(path, ext)
    if dims:
        out["width"], out["height"] = dims
        long_edge = max(dims)
        if long_edge > MAX_LONG_EDGE:
            out["flags"].append("needs-crop")
            out["notes"].append(
                f"long edge {long_edge}px > {MAX_LONG_EDGE}px, so it is downscaled to "
                f"~{MAX_LONG_EDGE}px before the model sees it — card values and axis "
                f"labels will blur. Ask for section crops instead: header, KPI band, "
                f"each chart, one detail table."
            )
        if dims[0] < MIN_LEGIBLE_WIDTH:
            out["flags"].append("low-resolution")
            out["notes"].append(
                f"{dims[0]}px wide — under {MIN_LEGIBLE_WIDTH}px, tile labels are a "
                f"guess. Ask for a recapture at 100% browser zoom."
            )
        # A viewport capture is always landscape. Taller than wide means the
        # browser scrolled — the shape that reads worst.
        if dims[1] > 1.2 * dims[0]:
            out["flags"].append("full-page-scroll-capture")
            out["notes"].append(
                "aspect ratio says this is a scrolling full-page capture — the single "
                "worst input shape. Section crops read far better."
            )
    else:
        out["flags"].append("unparsed-dimensions")
        out["notes"].append("could not read dimensions from the header; Read it and judge legibility on screen.")
    if size_bytes > MAX_BYTES:
        out["flags"].append("oversize")
        out["notes"].append(f"{size_bytes // 1024}KB exceeds the {MAX_BYTES // 1024 // 1024}MB per-image ceiling — re-export or crop.")
    return out


def inspect_pdf(path: str) -> dict:
    pages = pdf_pages(path)
    out = {"readWith": "Read", "readable": True, "flags": [], "notes": [], "pages": pages}
    if pages and pages > 10:
        out["flags"].append("pages-required")
        out["notes"].append(f"{pages} pages — the Read call MUST pass `pages` (max 20 per request).")
    out["notes"].append(
        "a VECTOR pdf export of a dashboard often reads better than a png screenshot; "
        "if this one is just a wrapped bitmap it reads the same as the png."
    )
    return out


def inspect_transcript(path: str) -> dict:
    out: dict = {"readWith": "Read", "readable": True, "flags": [], "notes": [],
                 "signals": {}, "piiFlags": [], "numbersToConfirm": []}
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        out.update(readable=False, flags=["unreadable"], notes=[str(exc)])
        return out

    text = "\n".join(lines)
    words = len(text.split())
    out["lines"] = len(lines)
    out["words"] = words
    out["estimatedMinutes"] = round(words / 140.0, 1) if words else 0

    speakers: dict[str, int] = {}
    for ln in lines:
        m = SPEAKER_RE.match(ln)
        if m:
            speakers[m.group(1).strip()] = speakers.get(m.group(1).strip(), 0) + 1
    if speakers:
        out["speakers"] = sorted(speakers.items(), key=lambda kv: -kv[1])
        out["notes"].append(
            "speaker labels found — auto-transcription mis-attributes turns and mangles "
            "names. Attribute a requirement to a role only if the transcript says the "
            "role; otherwise the brief field is origin:\"inferred\"."
        )
    else:
        out["flags"].append("no-speaker-labels")
        out["notes"].append("no speaker labels — you cannot tell a requirement from an aside. Ask who said what.")

    if TS_RE.search(text):
        out["hasTimestamps"] = True

    for name, pats in SIGNALS.items():
        hits = []
        for i, ln in enumerate(lines, 1):
            for p in pats:
                if re.search(p, ln, re.I):
                    hits.append({"line": i, "text": ln.strip()[:160]})
                    break
        if hits:
            out["signals"][name] = hits[:12]

    for label, pat in PII_PATTERNS:
        hits = [i for i, ln in enumerate(lines, 1) if re.search(pat, ln)]
        if hits:
            out["piiFlags"].append({"kind": label, "count": len(hits), "lines": hits[:8]})
    if out["piiFlags"]:
        out["flags"].append("pii-present")
        out["notes"].append(
            "PII-shaped strings present. Do not carry them into a spec, a CallText "
            "prompt, a commit, or a workbook surface. Resolve every piiFlags entry."
        )

    for label, pat in NUMBER_PATTERNS:
        hits = [{"line": i, "match": m.group(0)}
                for i, ln in enumerate(lines, 1) for m in [re.search(pat, ln)] if m]
        if hits:
            out["numbersToConfirm"].append({"kind": label, "count": len(hits), "samples": hits[:8]})
    if out["numbersToConfirm"]:
        out["notes"].append(
            "numbers spoken on a call are the least reliable thing in the file — "
            "\"fifteen\" and \"fifty\" transcribe interchangeably. Confirm every "
            "threshold and target with a human before it becomes a metric definition."
        )
    return out


def inspect_av(path: str) -> dict:
    return {"readWith": None, "readable": False, "flags": ["unreadable"], "notes": [
        "audio/video cannot be read here. Ask for the text transcript (.txt/.vtt/.srt) "
        "that the meeting tool already produced — Zoom, Gong, Granola and Google Meet "
        "all export one. Do not describe the recording from its filename."
    ]}


def inspect_convert(path: str, ext: str) -> dict:
    return {"readWith": None, "readable": False, "flags": ["needs-conversion"], "notes": [
        f"not readable as-is. Convert first: {CONVERT_EXT[ext]}"
    ]}


# ------------------------------------------------------------------------ walk
def collect(paths: list[str]) -> list[str]:
    found: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fn in sorted(files):
                    if not fn.startswith("."):
                        found.append(os.path.join(root, fn))
        elif os.path.exists(p):
            found.append(p)
        else:
            sys.stderr.write(f"intake-artifacts: no such path: {p}\n")
    return found


def build_needs_input(arts: list[dict], company: str | None) -> list[dict]:
    """Everything artifacts structurally cannot answer, plus what this set is missing."""
    kinds = {a["kind"] for a in arts}
    needs: list[dict] = []

    if not company:
        needs.append({"code": "company", "question":
                      "Which company is this for, and what domain (for fetch_logo.py)?"})
    if "image" not in kinds and "pdf" not in kinds:
        needs.append({"code": "no-form-evidence", "question":
                      "No screenshots — nothing pins the LAYOUT. Ask for one, or choose a "
                      "layout from reference/layouts.md and say which and why."})
    if "transcript" not in kinds:
        needs.append({"code": "no-function-evidence", "question":
                      "No transcript or notes — screenshots show FORM but never metric "
                      "semantics, grain, or why anyone opens the dashboard. Every metric "
                      "definition here would be invented. Ask for the call notes."})
    # Structural blind spots. A screenshot never carries these and a transcript
    # almost never states them precisely.
    needs += [
        {"code": "grain", "question":
         "One row of the underlying data = what? (an order, a claim, a day per store …) "
         "Neither a screenshot nor a call transcript states this reliably."},
        {"code": "data-sourcing", "question":
         "Their own warehouse table (sigma-byod-data-model), the Big Buys sample reshaped "
         "(sigma-company-dashboard move 1), or nothing to point at (sigma-synthetic-star-model)? "
         "A screenshot of a working dashboard does NOT mean we get its data."},
        {"code": "comparison-basis", "question":
         "Every KPI card is comparative — compared against what? Prior year, prior period, "
         "plan/budget, or a target? A tile reading \"+4.2%\" does not say which."},
        {"code": "metric-definitions", "question":
         "For each KPI: the exact numerator/denominator and filter. A tile label "
         "(\"Attainment\") is a name, never a definition."},
        {"code": "refresh-and-window", "question":
         "How current does the data need to be, and what date window does the page default to?"},
    ]
    if any("pii-present" in a.get("flags", []) for a in arts):
        needs.append({"code": "pii", "question":
                      "PII-shaped strings found in the artifacts. Confirm what may leave "
                      "their org and what must be cropped or excluded before use."})
    if any("needs-crop" in a.get("flags", []) or "low-resolution" in a.get("flags", [])
           for a in arts):
        needs.append({"code": "recapture", "question":
                      "Some screenshots will read blurry (see flags). Ask for section crops "
                      "at 100% browser zoom: header, KPI band, each chart, one detail table."})
    return needs


def main() -> None:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("paths", nargs="+", help="artifact files and/or folders")
    ap.add_argument("--company", help="company name, if you already know it")
    ap.add_argument("--domain", help="company domain, for scripts/fetch_logo.py")
    ap.add_argument("--out", help="write brief skeleton here (default: stdout)")
    a = ap.parse_args()

    artifacts: list[dict] = []
    for i, path in enumerate(collect(a.paths), 1):
        ext = os.path.splitext(path)[1].lower()
        kind = classify(path)
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = 0
        rec: dict = {"id": f"a{i}", "path": path, "kind": kind, "bytes": size_bytes}
        if kind == "image":
            rec.update(inspect_image(path, ext, size_bytes))
        elif kind == "pdf":
            rec.update(inspect_pdf(path))
        elif kind == "transcript":
            rec.update(inspect_transcript(path))
        elif kind == "av":
            rec.update(inspect_av(path))
        elif kind == "needs-conversion":
            rec.update(inspect_convert(path, ext))
        else:
            rec.update({"readWith": "Read", "readable": True, "flags": ["unknown-kind"],
                        "notes": ["unrecognized extension — open it and see, or ask."]})
        # FORM vs FUNCTION: which half of the brief this artifact is allowed to fill.
        rec["fills"] = ("observed" if kind in ("image", "pdf")
                        else "stated" if kind == "transcript" else None)
        artifacts.append(rec)

    brief = {
        "specVersion": 1,
        "generatedAt": _dt.date.today().isoformat(),
        "company": {"name": a.company, "domain": a.domain,
                    "origin": "asked" if a.company else "needs-input"},
        "artifacts": artifacts,
        # FORM — filled from images only.
        "observed": {"layoutEvidence": None, "tiles": [], "charts": [], "controls": [],
                     "palette": [], "vocabulary": []},
        # FUNCTION — filled from transcripts only.
        "stated": {"process": [], "metrics": [], "personas": [], "cadence": None,
                   "pains": [], "quotes": []},
        # Every field below needs {value, origin, source?} — see validate-brief.py.
        "decisions": {"dataSourcing": None, "layout": None, "pages": [], "kpis": [],
                      "charts": [], "filters": [], "pluginConcept": None,
                      "page2Pattern": None, "brandKit": None},
        "piiFlags": [
            {"artifact": art["id"], "kind": f["kind"], "count": f["count"],
             "lines": f["lines"], "resolution": None, "resolved": False}
            for art in artifacts for f in art.get("piiFlags", [])
        ],
        "needsInput": build_needs_input(artifacts, a.company),
        "confirmedBy": None,
    }

    text = json.dumps(brief, indent=2)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
        readable = sum(1 for x in artifacts if x.get("readable"))
        print(f"intake-artifacts: wrote {a.out} — {len(artifacts)} artifact(s), "
              f"{readable} readable, {len(brief['needsInput'])} question(s) in needsInput")
        for x in artifacts:
            flags = ",".join(x.get("flags", [])) or "ok"
            dims = (f" {x.get('width')}x{x.get('height')}" if x.get("width")
                    else f" {x.get('lines')}L/{x.get('words')}w" if x.get("lines")
                    else f" {x.get('pages')}p" if x.get("pages") else "")
            print(f"  {x['id']:>3} {x['kind']:<16}{dims:<16} [{flags}] {x['path']}")
        for q in brief["needsInput"]:
            print(f"  ? [{q['code']}] {q['question'][:96]}")
        if brief["piiFlags"]:
            print(f"  ! {len(brief['piiFlags'])} PII flag(s) — resolve each before use")
    else:
        print(text)


if __name__ == "__main__":
    main()
