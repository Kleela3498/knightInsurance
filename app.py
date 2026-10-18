"""Flask app: upload a packet, review it, fix fields, reopen past reviews.

Single-user, local tool. Every run is saved to SQLite (knight/store.py), so the
history survives restarts and a reopened run keeps the reviewer's fixes.
"""
from __future__ import annotations

import os
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, make_response, request, send_file, send_from_directory

from knight.extract import (
    NORMALIZERS,
    extract_by_labels,
    extract_packet,
    read_pdf_pages,
)
from knight.llm import LLMUnavailable, RealLLM
from knight.lab import run_lab
from knight.models import FieldValue, PacketResult, RuleResult
from knight.narratives import (
    TEMPLATE_KINDS, generate_narratives, revise_draft, template_draft, template_summary,
)
from knight.rules import evaluate_rules
from knight.store import Store

BASE_DIR = Path(__file__).resolve().parent
DB = Store(os.environ.get("KNIGHT_DB", BASE_DIR / "data" / "knight.db"))

app = Flask(__name__, static_folder="static", static_url_path="/static")


def _pdf_text(path: Path) -> str:
    try:
        return "\n".join(t for _, t in read_pdf_pages(str(path)))
    except Exception:  # noqa: BLE001 — an unreadable attachment is reported as unreadable
        return ""


def _load_upload(dest: Path) -> dict | None:
    """Read the uploaded packet: email (file or pasted), application PDF, vehicle CSV, extra PDFs."""
    app_pdf = request.files.get("application_pdf")
    if not app_pdf or not app_pdf.filename:
        return None
    email_text, email_name = request.form.get("email_text", ""), "pasted broker email"
    email_file = request.files.get("email_file")
    if email_file and email_file.filename:
        email_text = email_file.read().decode("utf-8", errors="replace")
        email_name = email_file.filename
    app_path = dest / "application.pdf"
    app_pdf.save(app_path)
    csv_file = request.files.get("vehicles_csv")
    has_csv = bool(csv_file and csv_file.filename)
    attachments = []
    for i, f in enumerate(request.files.getlist("attachments")):
        if f.filename:
            p = dest / f"attachment_{i}.pdf"
            f.save(p)
            attachments.append({"filename": f.filename, "text": _pdf_text(p)})
    return {
        "packet_id": request.form.get("name", "").strip() or Path(app_pdf.filename).stem,
        "email_text": email_text,
        "email_filename": email_name,
        "application_pages": read_pdf_pages(str(app_path)),
        "application_filename": app_pdf.filename,
        "vehicles_csv_text": csv_file.read().decode("utf-8", errors="replace") if has_csv else None,
        "vehicles_filename": csv_file.filename if has_csv else "vehicles.csv (not supplied)",
        "attachments": attachments,
    }


def _run_pipeline(bundle: dict, llm: RealLLM) -> PacketResult:
    warnings: list[str] = []
    try:
        fields = extract_packet(
            email_text=bundle["email_text"],
            email_filename=bundle["email_filename"],
            application_pages=bundle["application_pages"],
            application_filename=bundle["application_filename"],
            vehicles_csv_text=bundle["vehicles_csv_text"],
            vehicles_filename=bundle["vehicles_filename"],
            attachments=bundle["attachments"],
            llm=llm,
        )
        reachable = True
    except LLMUnavailable as exc:
        # The model is down: say so, then read the fields by label matching so the reviewer still has values to check.
        warnings.append(
            f"AI extraction failed: {exc} The values below were read by simple label matching (no AI). "
            "Please check each one against the documents.")
        reachable = False
        fields = extract_by_labels(
            email_text=bundle["email_text"],
            email_filename=bundle["email_filename"],
            application_pages=bundle["application_pages"],
            application_filename=bundle["application_filename"],
            vehicles_csv_text=bundle["vehicles_csv_text"],
            vehicles_filename=bundle["vehicles_filename"],
            attachments=bundle["attachments"],
        )

    rules, overall = evaluate_rules(fields)
    summary, summary_origin, draft, draft_origin = generate_narratives(
        bundle["packet_id"], fields, rules, overall, llm)
    return PacketResult(
        packet_id=bundle["packet_id"], fields=fields, rules=rules, overall_status=overall,
        summary=summary, summary_origin=summary_origin,
        draft_email=draft, draft_origin=draft_origin,
        llm={"model": llm.model, "reachable": reachable}, warnings=warnings,
    )


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def dev_mode() -> bool:
    return os.environ.get("KNIGHT_DEV_MODE") != "0"


@app.get("/api/dev")
def dev_status():
    return jsonify({"enabled": dev_mode()})


@app.post("/api/dev/lab")
def dev_lab():
    """Edge-Case Lab: run fault-injection scenarios through the real pipeline. Nothing is saved."""
    if not dev_mode():
        abort(404)
    return jsonify({"results": run_lab(_run_pipeline)})


@app.get("/api/llm-status")
def llm_status():
    llm = RealLLM.from_env()
    reachable = llm.ping()
    return jsonify({
        "reachable": reachable, "model": llm.model, "base_url": llm.base_url,
        "note": None if reachable else
        "Model unreachable. Start Ollama, or set KNIGHT_LLM_BASE_URL / KNIGHT_LLM_MODEL / KNIGHT_LLM_API_KEY. "
        "Extraction will show its failure state; deterministic checks still run.",
    })


@app.get("/api/runs")
def list_runs():
    return jsonify({"runs": DB.list(deleted=bool(request.args.get("deleted")))})


@app.post("/api/runs")
def create_run():
    run_id = uuid.uuid4().hex[:12]
    bundle = _load_upload(DB.upload_dir(run_id))
    if bundle is None:
        return jsonify({"error": "Please choose the application PDF."}), 400
    pending = DB.save_pending(run_id, bundle["packet_id"])
    _JOBS.put((run_id, bundle))
    _ensure_worker()
    return jsonify(pending), 202


# One worker: Ollama serves one model, so reviews wait in line and run one at a time.
_JOBS: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, daemon=True)
            _worker.start()


def _worker_loop() -> None:
    while True:
        run_id, bundle = _JOBS.get()
        try:
            _process(run_id, bundle)
        except Exception as exc:  # noqa: BLE001 — one bad packet must not stop the queue
            DB.set_state(run_id, "FAILED", f"The review could not be completed: {exc}")
        finally:
            _JOBS.task_done()


def wait_idle() -> None:
    """Block until every queued review has finished (used by tests)."""
    _JOBS.join()


def _process(run_id: str, bundle: dict) -> None:
    existing = DB.get(run_id)
    if existing is None:  # deleted forever while waiting
        return
    DB.set_state(run_id, "PROCESSING")
    result = _run_pipeline(bundle, RealLLM.from_env())
    payload = {
        **result.to_dict(),
        "run_id": run_id,
        "name": bundle["packet_id"],
        "created_at": existing["created_at"],
        "undo": [], "redo": [], "draft_edited": None, "draft_stale": False,
        "draft_history": [], "draft_feedback": [],
        "sources": {
            "email": {"filename": bundle["email_filename"], "text": bundle["email_text"]},
            "application": {
                "filename": bundle["application_filename"],
                "pages": [{"page": p, "text": t} for p, t in bundle["application_pages"]],
            },
        },
    }
    if DB.get(run_id) is not None:
        DB.update(payload)


@app.get("/api/runs/<run_id>")
def get_run(run_id):
    payload = DB.get(run_id)
    if payload is None:
        abort(404)
    return jsonify(_with_defaults(payload))


@app.get("/api/runs/<run_id>/pdf")
def run_pdf(run_id):
    path = DB.upload_dir(run_id) / "application.pdf"
    if DB.get(run_id) is None or not path.exists():
        abort(404)
    return send_file(path, mimetype="application/pdf")


def _parse_override(name: str, raw):
    """Returns (value, error)."""
    if name in NORMALIZERS:
        value, valid = NORMALIZERS[name](raw)
        return (value, None) if valid else (None, f"'{raw}' is not a valid value for this field.")
    if name == "schedule_vehicle_count":
        try:
            value = int(raw)
        except (ValueError, TypeError):
            value = -1
        return (value, None) if value >= 0 else (None, "Enter a whole number, 0 or more.")
    if name == "loss_runs_present":
        value = {"true": True, "yes": True, "false": False, "no": False}.get(str(raw).strip().lower())
        return (value, None) if value is not None else (None, "Enter Yes or No.")
    return None, "This field cannot be changed."


MAX_UNDO = 50


def _with_defaults(payload: dict) -> dict:
    if payload.get("state"):  # queued, processing or failed: no result yet
        return payload
    payload.setdefault("undo", [])
    payload.setdefault("redo", [])
    payload.setdefault("draft_history", [])
    payload.setdefault("draft_feedback", [])
    return payload


def _load(run_id: str) -> dict:
    payload = DB.get(run_id)
    if payload is None:
        abort(404)
    if payload.get("state"):
        abort(make_response(jsonify({"error": "This review is not finished yet."}), 409))
    return _with_defaults(payload)


def _current_overrides(payload: dict) -> dict:
    return {k: f["override"] for k, f in payload["fields"].items()}


def _recheck(payload: dict, overrides: dict) -> list[dict]:
    """Apply a set of overrides, re-run the rules and template narratives (no model call), save. Returns rule changes."""
    fields = {k: FieldValue.from_dict(v) for k, v in payload["fields"].items()}
    for name, ov in overrides.items():
        fields[name].override = ov
    before = {r["rule_id"]: r["status"] for r in payload["rules"]}
    rules, overall = evaluate_rules(fields)
    summary = template_summary(payload["packet_id"], fields, rules, overall)
    draft = template_draft("standard", payload["packet_id"], fields, rules)
    s_origin = d_origin = "template"
    payload.update(
        fields={k: v.to_dict() for k, v in fields.items()},
        rules=[r.to_dict() for r in rules], overall_status=overall,
        summary=summary, summary_origin=s_origin, draft_email=draft, draft_origin=d_origin,
    )
    if payload.get("draft_edited") is not None:
        payload["draft_stale"] = True  # the reviewer's saved draft predates this re-check
    DB.update(payload)
    return [{"rule_id": r.rule_id, "before": before[r.rule_id], "after": r.status}
            for r in rules if before[r.rule_id] != r.status]


def _change(payload: dict, new: dict):
    """Record the current overrides for Undo, clear Redo, then apply `new`."""
    payload["undo"] = (payload["undo"] + [_current_overrides(payload)])[-MAX_UNDO:]
    payload["redo"] = []
    changes = _recheck(payload, new)
    return jsonify({**payload, "changes": changes})


@app.post("/api/runs/<run_id>/override")
def override(run_id):
    payload = _load(run_id)
    data = request.get_json(force=True)
    name = data.get("field")
    if name not in payload["fields"]:
        return jsonify({"error": f"Unknown field {name}."}), 400
    new = _current_overrides(payload)
    if data.get("clear"):
        new[name] = None
    else:
        value, error = _parse_override(name, data.get("value"))
        if error:
            return jsonify({"error": error}), 400
        new[name] = {
            "value": value,
            "by": "human",
            "at": datetime.now(timezone.utc).isoformat(),
            "note": "Human override: the original extracted value, evidence and conflict are kept.",
        }
    return _change(payload, new)


@app.post("/api/runs/<run_id>/reset")
def reset(run_id):
    """Restore every field to what the AI extracted (undoable)."""
    payload = _load(run_id)
    if not any(_current_overrides(payload).values()):
        return jsonify({"error": "There are no fixes to restore."}), 400
    return _change(payload, {k: None for k in payload["fields"]})


def _step(run_id: str, source: str, target: str):
    """Undo or redo: swap the current overrides with the top of one stack."""
    payload = _load(run_id)
    if not payload[source]:
        return jsonify({"error": f"Nothing to {'undo' if source == 'undo' else 'redo'}."}), 400
    wanted = payload[source].pop()
    payload[target] = (payload[target] + [_current_overrides(payload)])[-MAX_UNDO:]
    changes = _recheck(payload, wanted)
    return jsonify({**payload, "changes": changes})


@app.post("/api/runs/<run_id>/undo")
def undo(run_id):
    return _step(run_id, "undo", "redo")


@app.post("/api/runs/<run_id>/redo")
def redo(run_id):
    return _step(run_id, "redo", "undo")


def _set_draft(payload: dict, text):
    """Change the saved draft; the text it replaces goes on a stack for 'Previous version'."""
    current = payload.get("draft_edited")
    if current is None:
        current = payload.get("draft_email") or ""
    if text != current:
        payload["draft_history"] = (payload.get("draft_history", []) + [current])[-MAX_UNDO:]
    payload["draft_edited"] = text
    payload["draft_stale"] = False


@app.post("/api/runs/<run_id>/draft")
def save_draft(run_id):
    """Save the reviewer's edited draft email, or (restore=true) go back to the AI draft."""
    payload = _load(run_id)
    data = request.get_json(force=True)
    _set_draft(payload, None if data.get("restore") else str(data.get("text", "")))
    DB.update(payload)
    return jsonify(payload)


@app.post("/api/runs/<run_id>/draft/revise")
def revise(run_id):
    """Email feedback loop: the reviewer says what to change, the model rewrites the draft."""
    payload = _load(run_id)
    data = request.get_json(force=True)
    instruction = str(data.get("instruction", "")).strip()[:300]
    if not instruction:
        return jsonify({"error": "Please say what to change, for example “make it shorter”."}), 400
    current = data.get("text")
    if current is None:
        current = payload.get("draft_edited") or payload.get("draft_email") or ""
    try:
        revised = revise_draft(str(current), instruction, RealLLM.from_env())
    except LLMUnavailable as exc:
        return jsonify({"error": f"The AI could not rewrite the email: {exc} Your draft is unchanged. "
                                 "You can still pick a template."}), 503
    payload["draft_edited"] = str(current)  # so an unsaved edit is kept on the Previous-version stack
    _set_draft(payload, revised)
    payload["draft_feedback"] = (payload.get("draft_feedback", [])
                                 + [{"instruction": instruction, "at": datetime.now(timezone.utc).isoformat()}])[-MAX_UNDO:]
    DB.update(payload)
    return jsonify(payload)


@app.post("/api/runs/<run_id>/draft/template")
def use_template(run_id):
    """Replace the draft with a ready-made template (works with no model)."""
    payload = _load(run_id)
    kind = (request.get_json(force=True) or {}).get("kind")
    if kind not in TEMPLATE_KINDS:
        return jsonify({"error": "Please choose one of the templates."}), 400
    fields = {k: FieldValue.from_dict(v) for k, v in payload["fields"].items()}
    rules = [RuleResult(**r) for r in payload["rules"]]
    _set_draft(payload, template_draft(kind, payload["packet_id"], fields, rules))
    DB.update(payload)
    return jsonify(payload)


@app.post("/api/runs/<run_id>/draft/previous")
def previous_draft(run_id):
    payload = _load(run_id)
    if not payload.get("draft_history"):
        return jsonify({"error": "There is no earlier version."}), 400
    payload["draft_edited"] = payload["draft_history"].pop()
    payload["draft_stale"] = False
    DB.update(payload)
    return jsonify(payload)


@app.post("/api/runs/<run_id>/rename")
def rename(run_id):
    payload = _load(run_id)
    name = str((request.get_json(force=True) or {}).get("name", "")).strip()
    if not name:
        return jsonify({"error": "Please type a name."}), 400
    payload["name"] = name[:80]
    DB.update(payload)
    return jsonify(payload)


@app.delete("/api/runs/<run_id>")
def delete_run(run_id):
    """Move to 'Recently deleted'; ?forever=1 removes it and its files for good."""
    ok = DB.purge(run_id) if request.args.get("forever") else DB.set_deleted(run_id, True)
    return (jsonify({"ok": True}), 200) if ok else abort(404)


@app.post("/api/runs/<run_id>/restore")
def restore_run(run_id):
    return (jsonify({"ok": True}), 200) if DB.set_deleted(run_id, False) else abort(404)



if __name__ == "__main__":
    DB.fail_interrupted()
    threading.Thread(target=RealLLM.from_env().warm_up, daemon=True).start()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5057")), debug=False)
