"""Document ingestion, normalization, and deterministic field merging.

The LLM extracts *per-source raw values* for the
seven text fields. Everything after that — normalization, conflict detection,
CSV counting, attachment inventory — is ordinary deterministic code so the
behaviour is testable without a model.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import re
from typing import Any, Optional

from pypdf import PdfReader

from .llm import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_TEMPLATE,
    LLMUnavailable,
    RealLLM,
)
from .models import Evidence, FieldValue

LLM_FIELDS = [
    "business_name",
    "requested_effective_date",
    "garaging_state",
    "operations_description",
    "declared_vehicle_count",
    "operating_radius_miles",
    "hazardous_materials",
]

ABSENT_PHRASES = {"not provided", "n/a", "na", "none", "-", ""}


# ---------------------------------------------------------------- ingestion

def read_pdf_pages(path: str) -> list[tuple[int, str]]:
    """Return [(page_no, text)] for a PDF; empty text marks it unreadable."""
    pages: list[tuple[int, str]] = []
    with open(path, "rb") as fh:
        reader = PdfReader(fh)
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages.append((i, text))
    return pages


def pdf_full_text(pages: list[tuple[int, str]]) -> str:
    return "\n".join(t for _, t in pages)


def count_csv_vehicles(csv_text: str) -> tuple[Optional[int], str, Optional[str]]:
    """Count nonblank data rows after the header. Returns (count, locator, error)."""
    try:
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
    except Exception as exc:  # noqa: BLE001 — surfaced as evidence
        return None, "vehicles.csv (unreadable)", f"CSV parse failed: {exc}"
    if not rows:
        return None, "vehicles.csv (empty file)", "CSV has no rows, not even a header"
    data_rows = [r for r in rows[1:] if any((c or "").strip() for c in r)]
    n = len(data_rows)
    locator = f"CSV data rows 1-{n} (header excluded)" if n else "CSV (header only, no data rows)"
    return n, locator, None


LOSS_RUNS_RE = re.compile(r"loss[\s\-_]*(runs|history)", re.IGNORECASE)
PROMISE_RE = re.compile(r"\b(to follow|will (?:be )?(?:send|sent|provid\w+|forward\w*)|forthcoming|pending|not (?:yet )?attached|"
                        r"not available|upon request|separately|later|requested|request)\b", re.IGNORECASE)
HEADING_RE = re.compile(r"^(?:[\w&',\-]+\s+){0,3}loss[\s\-_]*(?:runs|history)\b", re.IGNORECASE)
SENTENCE_START_RE = re.compile(r"^(see|please|attached|attach|enclosed|find|here|below|refer|our|your)\b", re.IGNORECASE)


def _is_loss_runs_heading(text: str) -> bool:
    """The document names itself a loss runs / loss history report in a short title line near the top.
    A body sentence such as "see attached loss runs below." or "loss runs to follow" is not a title."""
    heads = [ln.strip() for ln in text.splitlines() if ln.strip()][:6]
    return any(len(h) <= 60 and HEADING_RE.match(h) and not h.endswith(".") and not SENTENCE_START_RE.match(h)
               and not PROMISE_RE.search(h) for h in heads)


def detect_loss_runs(attachments: list[dict]) -> tuple[Optional[bool], Evidence, list[str]]:
    """attachments: [{"filename": str, "text": str|None}]. Returns (present, evidence, notes).
    PASS needs a readable attachment whose own heading says it is a loss runs/history report.
    Unreadable files, or readable ones that only mention loss runs, are UNKNOWN for a human to judge."""
    notes: list[str] = []
    inventory = ", ".join(a["filename"] for a in attachments) or "(no attachments uploaded)"
    readable = [(a["filename"], a["text"]) for a in attachments if a["text"]]
    unreadable = [a["filename"] for a in attachments if not a["text"]]
    for filename, text in readable:
        if _is_loss_runs_heading(text):
            ev = Evidence(filename, "attachment heading", f"Document identifies itself as a loss runs/history report ({filename}).")
            return True, ev, notes
    unsure = list(unreadable)
    for fn, text in readable:
        if not (LOSS_RUNS_RE.search(text) or LOSS_RUNS_RE.search(fn)):
            continue  # unrelated document
        if PROMISE_RE.search(text):
            notes.append(f"{fn} talks about loss runs but is a promise or request, not the report itself.")
        else:
            unsure.append(fn)
            notes.append(f"{fn} mentions loss runs but does not present itself as a loss runs report.")
    if unsure:
        ev = Evidence(
            ", ".join(unsure), "uploaded attachment inventory",
            f"Attachment(s) present but unreadable or not clearly a loss runs report: {', '.join(unsure)}. "
            f"Full inventory: {inventory}.",
        )
        return None, ev, notes
    ev = Evidence(
        "uploaded attachment inventory",
        "uploaded attachment inventory",
        f"No loss runs or loss history document attached. Inventory: {inventory}. "
        "An email promise to send loss runs later does not count as an attachment.",
    )
    return False, ev, notes


# ------------------------------------------------------------- normalization

def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in ABSENT_PHRASES)


def norm_name(v: Any) -> tuple[Optional[str], bool]:
    if _blank(v):
        return None, True
    return str(v).strip(), True


def norm_date(v: Any) -> tuple[Optional[str], bool]:
    """Returns (iso_date_or_None, valid)."""
    if _blank(v):
        return None, True
    try:
        d = datetime.date.fromisoformat(str(v).strip())
        return d.isoformat(), True
    except ValueError:
        return None, False


STATE_MAP = {"CALIFORNIA": "CA"}


def norm_state(v: Any) -> tuple[Optional[str], bool]:
    if _blank(v):
        return None, True
    s = str(v).strip().upper()
    s = STATE_MAP.get(s, s)
    return (s, True) if re.fullmatch(r"[A-Z]{2}", s) else (None, False)


RADIUS_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
RANGE_RE = re.compile(r"\d\s*(?:to|-|–|—|and|or|/)\s*[-+]?\d", re.IGNORECASE)


def norm_radius(v: Any) -> tuple[Optional[float], bool]:
    if _blank(v):
        return None, True
    if isinstance(v, (int, float)):
        num = float(v)
    else:
        text = str(v).replace(",", "")
        if RANGE_RE.search(text):  # "150 to 200 miles" is ambiguous, never silently take the first number
            return None, False
        m = RADIUS_RE.search(text)
        if not m:
            return None, False
        num = float(m.group())
    if num < 0:
        return None, False
    # Normalize harmless float noise: 150 and 150.0 compare equal downstream.
    return (round(num, 6), True)


def norm_hazmat(v: Any) -> tuple[Optional[bool], bool]:
    if _blank(v):
        return None, True
    if isinstance(v, bool):
        return v, True
    s = str(v).strip().lower()
    if s in {"yes", "y", "true", "1"}:
        return True, True
    if s in {"no", "n", "false", "0"}:
        return False, True
    return None, False


def norm_count(v: Any) -> tuple[Optional[int], bool]:
    if _blank(v):
        return None, True
    try:
        n = int(str(v).strip())
    except (ValueError, TypeError):
        return None, False
    return (n, True) if n >= 0 else (None, False)


NORMALIZERS = {
    "business_name": norm_name,
    "requested_effective_date": norm_date,
    "garaging_state": norm_state,
    "operations_description": norm_name,
    "declared_vehicle_count": norm_count,
    "operating_radius_miles": norm_radius,
    "hazardous_materials": norm_hazmat,
}


def _canon_ops(s: Any) -> str:
    t = re.sub(r"\s+", " ", str(s).strip().lower())
    return t.rstrip(".!?").strip()


def values_equal(name: str, a: Any, b: Any) -> bool:
    if name == "business_name":
        return str(a).strip().lower() == str(b).strip().lower()
    if name == "operations_description":
        # Harmless differences (trailing punctuation, case, whitespace) are not conflicts.
        return _canon_ops(a) == _canon_ops(b)
    return a == b


# ------------------------------------------------------------------- merging

def merge_field(
    name: str,
    email_raw: Any,
    pdf_raw: Any,
    email_ev: Evidence,
    pdf_ev: Evidence,
    inspected: str,
) -> FieldValue:
    """Deterministic two-source merge with CONFLICT semantics from field_specification.json."""
    norm = NORMALIZERS[name]
    ev = norm(email_raw)
    pv = norm(pdf_raw)
    fv = FieldValue(name=name, value=None, state="MISSING")

    email_missing, pdf_missing = ev[0] is None and ev[1], pv[0] is None and pv[1]
    email_invalid, pdf_invalid = not ev[1], not pv[1]

    if email_missing and pdf_missing:
        fv.state = "MISSING"
        fv.evidence = [email_ev, pdf_ev]
        fv.notes.append(f"No mention in inspected sources ({inspected}).")
        return fv
    if email_invalid or pdf_invalid:
        fv.state = "INVALID"
        fv.evidence = [e for e, m in ((email_ev, not email_missing), (pdf_ev, not pdf_missing)) if m]
        fv.notes.append("A supplied value could not be normalized to the field type.")
        return fv
    if not email_missing and not pdf_missing:
        if values_equal(name, ev[0], pv[0]):
            fv.value, fv.state = ev[0], "KNOWN"
            fv.evidence = [email_ev, pdf_ev]
            return fv
        fv.state = "CONFLICT"
        fv.evidence = [email_ev, pdf_ev]
        fv.alternatives = [
            {"source": "email", "value": ev[0], "excerpt": email_ev.excerpt_or_observation},
            {"source": "application", "value": pv[0], "excerpt": pdf_ev.excerpt_or_observation},
        ]
        fv.notes.append(
            "Email and application disagree on the explicit normalized value; kept unresolved "
            "(no source preferred)."
        )
        return fv
    # Exactly one side present: missing mentions are not conflicts.
    if not email_missing:
        fv.value, fv.state, fv.evidence = ev[0], "KNOWN", [email_ev]
    else:
        fv.value, fv.state, fv.evidence = pv[0], "KNOWN", [pdf_ev]
    return fv


# ------------------------------------------------------------------ pipeline

def _strip_code_fence(raw: str) -> str:
    """Models often wrap JSON in a ```json fence; unwrap it, leave anything else untouched."""
    text = raw.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else text


def _squash(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def ground_excerpt(excerpt: Any, value: Any, source_text: str) -> Optional[str]:
    """Keep the model's excerpt only if it appears in the source text and contains the
    extracted value; otherwise fall back to the first source line containing the value.
    A bare value on its own line (PDF tables) gets the label line above it prepended."""
    squash = _squash

    core = squash(value).rstrip(".!?") if value is not None else ""
    found = None
    if excerpt and squash(excerpt) in squash(source_text) and (not core or core in squash(excerpt)):
        found = str(excerpt).strip()
    elif core:
        token = re.compile(r"(?<![\w.])" + re.escape(core) + r"(?![\w])")
        found = next((ln.strip() for ln in source_text.splitlines() if token.search(squash(ln))), None)
    if found and core and squash(found).rstrip(".!?") == core:
        lines = [ln.strip() for ln in source_text.splitlines()]
        for i, ln in enumerate(lines):
            if i and lines[i - 1] and squash(ln) == squash(found):
                return f"{lines[i - 1]} {ln}"
    return found


def _llm_extract(email_text: str, application_text: str, llm: RealLLM) -> dict:
    """Returns {"email": {field: {"value", "excerpt"}}, "application": {...}}; raises
    LLMUnavailable on malformed JSON or a missing field (failures are never hidden)."""
    user = EXTRACTION_USER_TEMPLATE.format(email_text=email_text, application_text=application_text)
    raw = llm.chat(EXTRACTION_SYSTEM_PROMPT, user, max_tokens=700)
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Model did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("Model response was not a JSON object.")
    out: dict = {"email": {}, "application": {}}
    for src in ("email", "application"):
        block = data.get(src)
        if not isinstance(block, dict):
            raise LLMUnavailable(f"Model response missing '{src}' block.")
        for f in LLM_FIELDS:
            cell = block.get(f)
            if cell is None or isinstance(cell, (str, int, float, bool)):
                cell = {"value": cell}  # compact shape: the value itself, or null
            if not isinstance(cell, dict) or (f not in block):
                raise LLMUnavailable(f"Model response missing field '{f}' in '{src}'.")
            out[src][f] = {"value": cell.get("value"), "excerpt": cell.get("excerpt")}
    return out


def deterministic_fields(
    email_text: str,
    vehicles_csv_text: Optional[str],
    vehicles_filename: str,
    attachments: list[dict],
) -> dict[str, FieldValue]:
    """Fields that need no model: schedule vehicle count and loss-runs presence."""
    if vehicles_csv_text is None:
        count, locator, err = None, f"{vehicles_filename} (not supplied)", "No vehicle schedule supplied."
    else:
        count, locator, err = count_csv_vehicles(vehicles_csv_text)
    sched = FieldValue(name="schedule_vehicle_count", value=count,
                       state="KNOWN" if count is not None else "INVALID")
    sched.evidence = [Evidence(vehicles_filename, locator, err or f"{count} nonblank data row(s) counted; header excluded.")]
    if err:
        sched.notes.append(err)

    present, loss_ev, loss_notes = detect_loss_runs(attachments)
    loss = FieldValue(name="loss_runs_present", value=present,
                      state="KNOWN" if present is not None else "INVALID")
    loss.evidence = [loss_ev]
    loss.notes.extend(loss_notes)
    # Surface an email promise as context, never as an attachment.
    if re.search(r"loss\s+runs", email_text, re.IGNORECASE) and present is False:
        loss.notes.append("Broker email mentions loss runs but a promise is not an attachment.")
    return {"schedule_vehicle_count": sched, "loss_runs_present": loss}


LABEL_ALIASES = {
    "business_name": ["Business name", "Named insured", "Insured name"],
    "requested_effective_date": ["Requested effective date", "Effective date"],
    "garaging_state": ["Garaging state", "Garage state"],
    "operations_description": ["Operations description", "Operations", "Description of operations"],
    "declared_vehicle_count": ["Declared vehicle count", "Number of vehicles", "Vehicle count"],
    "operating_radius_miles": ["Operating radius miles", "Operating radius", "Radius"],
    "hazardous_materials": ["Hazardous materials", "Hazmat"],
}
_KNOWN_LABELS = {a.lower() for aliases in LABEL_ALIASES.values() for a in aliases}


def label_extract(email_text: str, application_text: str) -> dict:
    """No-AI fallback: read "Label: value" lines in the email and "Label" / "value" rows in the
    PDF. Same shape as _llm_extract. Anything it cannot find is None, so it shows as Missing."""
    out: dict = {"email": {}, "application": {}}
    email_lines = [ln.strip() for ln in email_text.splitlines()]
    app_lines = [ln.strip() for ln in application_text.splitlines()]
    for f, aliases in LABEL_ALIASES.items():
        names = {a.lower() for a in aliases}
        inline = re.compile(r"^(?:%s)\s*:\s*(.+)$" % "|".join(re.escape(a) for a in aliases), re.IGNORECASE)
        cell_e = {"value": None, "excerpt": None}
        for ln in email_lines:
            m = inline.match(ln)
            if m:
                cell_e = {"value": m.group(1).strip(), "excerpt": ln}
                break
        cell_p = {"value": None, "excerpt": None}
        for i, ln in enumerate(app_lines):
            m = inline.match(ln)
            if m:
                cell_p = {"value": m.group(1).strip(), "excerpt": ln}
                break
            nxt = app_lines[i + 1] if i + 1 < len(app_lines) else ""
            if ln.lower().rstrip(":") in names and nxt and nxt.lower() not in _KNOWN_LABELS:
                cell_p = {"value": nxt, "excerpt": f"{ln} {nxt}"}
                break
        out["email"][f], out["application"][f] = cell_e, cell_p
    return out


def _no_line(raw: Any, where: str) -> str:
    if raw is None:
        return f"No mention in {where}."
    return f"Reported {raw!r} but no matching source line was found in the {where}."


_NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                 "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]


def _spellings(name: str, raw: Any) -> list[str]:
    """Other ways a document may write a value the model normalized (2026-11-01, CA, 3, Yes)."""
    s = str(raw).strip()
    if name == "requested_effective_date":
        try:
            d = datetime.date.fromisoformat(s)
        except ValueError:
            return []
        return [f"{d:%B} {d.day}, {d.year}", f"{d:%b} {d.day}, {d.year}", f"{d:%b}. {d.day}, {d.year}",
                f"{d.day} {d:%B} {d.year}", f"{d.month}/{d.day}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}"]
    if name == "garaging_state":
        return [full.title() for full, code in STATE_MAP.items() if code == s.upper()]
    if name == "hazardous_materials":
        word = {"true": "yes", "false": "no"}.get(s.lower())
        return [word] if word else []
    if name in ("declared_vehicle_count", "operating_radius_miles"):
        try:
            num = float(s.replace(",", ""))
        except ValueError:
            return []
        out = [str(int(num))] if num == int(num) else []
        if num == int(num) and 0 <= num < len(_NUMBER_WORDS):
            out.append(_NUMBER_WORDS[int(num)])
        return out
    return []


def _ground(name: str, cell: dict, text: str) -> Optional[str]:
    """The source line that backs the model's value, or None if the value is not in the text."""
    raw = cell["value"]
    found = ground_excerpt(cell["excerpt"], raw, text)
    if found is None and raw is not None and not _blank(raw):
        found = next((g for g in (ground_excerpt(None, alt, text) for alt in _spellings(name, raw)) if g), None)
    return found


def _page_of(excerpt: Optional[str], pages: list[tuple[int, str]]) -> str:
    """Locator for where the excerpt really is, not just the first page with text."""
    if excerpt:
        want = _squash(excerpt)
        for pg, t in pages:
            if want in _squash(t):
                return f"PDF page {pg}"
    return "PDF page 1" if len(pages) <= 1 else f"PDF pages 1-{len(pages)}"


def _assemble(
    extracted: dict,
    email_text: str,
    email_filename: str,
    application_pages: list[tuple[int, str]],
    application_filename: str,
    vehicles_csv_text: Optional[str],
    vehicles_filename: str,
    attachments: list[dict],
) -> dict[str, FieldValue]:
    """Per-source raw values -> checked evidence -> merged fields (+ the no-model fields).
    A value the model reports that cannot be found in its source is ignored (treated as missing),
    never trusted: the rules must not act on something the documents do not say."""
    application_text = pdf_full_text(application_pages)
    inspected = f"email body ({email_filename}), PDF pages 1-{len(application_pages)} ({application_filename})"

    fields: dict[str, FieldValue] = {}
    for f in LLM_FIELDS:
        e_cell, p_cell = extracted["email"][f], extracted["application"][f]
        e_raw, p_raw = e_cell["value"], p_cell["value"]
        e_ex = _ground(f, e_cell, email_text)
        p_ex = _ground(f, p_cell, application_text)
        email_ev = Evidence(email_filename, "email body", e_ex or _no_line(e_raw, "email body"))
        pdf_ev = Evidence(application_filename, _page_of(p_ex, application_pages), p_ex or _no_line(p_raw, "application PDF"))
        ignored = []
        if e_ex is None and not _blank(e_raw):
            e_raw = None
            ignored.append(("email", e_cell["value"]))
        if p_ex is None and not _blank(p_raw):
            p_raw = None
            ignored.append(("application PDF", p_cell["value"]))
        fields[f] = merge_field(f, e_raw, p_raw, email_ev, pdf_ev, inspected)
        for where, val in ignored:
            fields[f].notes.append(f"The AI reported {val!r} for the {where}, but that value is not in the text, "
                                   "so it was ignored. Check the document yourself.")

    fields.update(deterministic_fields(email_text, vehicles_csv_text, vehicles_filename, attachments))
    return fields


def extract_packet(
    email_text: str,
    email_filename: str,
    application_pages: list[tuple[int, str]],
    application_filename: str,
    vehicles_csv_text: Optional[str],
    vehicles_filename: str,
    attachments: list[dict],
    llm: RealLLM,
) -> dict[str, FieldValue]:
    """Full extraction. Raises LLMUnavailable if the model fails; the caller then
    falls back to extract_by_labels and shows the failure state."""
    extracted = _llm_extract(email_text, pdf_full_text(application_pages), llm)
    return _assemble(extracted, email_text, email_filename, application_pages, application_filename,
                     vehicles_csv_text, vehicles_filename, attachments)


def extract_by_labels(
    email_text: str,
    email_filename: str,
    application_pages: list[tuple[int, str]],
    application_filename: str,
    vehicles_csv_text: Optional[str],
    vehicles_filename: str,
    attachments: list[dict],
) -> dict[str, FieldValue]:
    """Same result as extract_packet but read by label matching, with no model."""
    extracted = label_extract(email_text, pdf_full_text(application_pages))
    return _assemble(extracted, email_text, email_filename, application_pages, application_filename,
                     vehicles_csv_text, vehicles_filename, attachments)

