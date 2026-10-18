"""End-to-end review loop through the Flask app, with a fake model (no network)."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KNIGHT_DB", str(Path(tempfile.mkdtemp()) / "import.db"))

import app as app_module  # noqa: E402
from knight.store import Store  # noqa: E402

SUB_C = ROOT / "sample_packets" / "SUB-C"


def cell(value, excerpt=None):
    return {"value": value, "excerpt": excerpt or str(value)}


EXTRACTION = json.dumps({
    "email": {
        "business_name": cell("Metro Route Example LLC", "Business name: Metro Route Example LLC"),
        "requested_effective_date": cell("2026-11-01"),
        "garaging_state": cell("CA"),
        "operations_description": cell("Local delivery of packaged retail goods"),
        "declared_vehicle_count": cell("4"),
        "operating_radius_miles": cell("180", "Operating radius miles: 180"),
        "hazardous_materials": cell("Yes"),
    },
    "application": {
        "business_name": cell("Metro Route Example LLC"),
        "requested_effective_date": cell("2026-11-01"),
        "garaging_state": cell("California"),
        "operations_description": cell("Local delivery of packaged retail goods"),
        "declared_vehicle_count": cell("4"),
        "operating_radius_miles": cell("80"),
        "hazardous_materials": cell("No"),
    },
})


class FakeLLM:
    model = "fake"
    base_url = "fake"

    def chat(self, system, user, max_tokens=1200):
        return EXTRACTION if "BROKER EMAIL" in user else "Fake narrative."

    def ping(self):
        return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB", Store(tmp_path / "t.db"))
    monkeypatch.setattr(app_module.RealLLM, "from_env", classmethod(lambda cls: FakeLLM()))
    return app_module.app.test_client()


def upload(client):
    started = client.post("/api/runs", content_type="multipart/form-data", data={
        "name": "SUB-C",
        "application_pdf": (open(SUB_C / "application.pdf", "rb"), "application.pdf"),
        "email_file": (open(SUB_C / "broker_email.txt", "rb"), "broker_email.txt"),
        "vehicles_csv": (open(SUB_C / "vehicles.csv", "rb"), "vehicles.csv"),
    })
    app_module.wait_idle()
    return client.get(f"/api/runs/{started.get_json()['run_id']}")


def statuses(payload):
    return {r["rule_id"]: r["status"] for r in payload["rules"]}


def test_nothing_is_preloaded(client):
    assert client.get("/api/runs").get_json() == {"runs": []}
    assert client.get("/api/packets").status_code == 404


def test_upload_requires_the_application_pdf(client):
    assert client.post("/api/runs", data={"name": "x"}).status_code == 400


def test_conflict_fix_recheck_reload_and_undo(client):
    run = upload(client).get_json()
    rid = run["run_id"]
    assert run["fields"]["operating_radius_miles"]["state"] == "CONFLICT"
    assert statuses(run)["R3"] == "UNKNOWN" and statuses(run)["R2"] == "FLAG"
    assert "Operating radius" in run["sources"]["email"]["text"]
    assert run["sources"]["application"]["pages"][0]["page"] == 1

    # Underwriter checks the PDF, decides the email is right, fixes the field.
    fixed = client.post(f"/api/runs/{rid}/override", json={"field": "operating_radius_miles", "value": "180"}).get_json()
    assert statuses(fixed)["R3"] == "FLAG"
    assert {"rule_id": "R3", "before": "UNKNOWN", "after": "FLAG"} in fixed["changes"]
    f = fixed["fields"]["operating_radius_miles"]
    assert f["override"]["by"] == "human" and f["effective_value"] == 180
    assert f["state"] == "CONFLICT" and len(f["alternatives"]) == 2 and f["evidence"]

    # The fix is saved: reopening from history shows it, and the history row counts it.
    reopened = client.get(f"/api/runs/{rid}").get_json()
    assert statuses(reopened)["R3"] == "FLAG"
    assert client.get("/api/runs").get_json()["runs"][0]["fixes"] == 1

    undone = client.post(f"/api/runs/{rid}/override", json={"field": "operating_radius_miles", "clear": True}).get_json()
    assert statuses(undone)["R3"] == "UNKNOWN"
    assert client.get("/api/runs").get_json()["runs"][0]["fixes"] == 0


def test_bad_override_is_rejected_and_pdf_is_served(client):
    rid = upload(client).get_json()["run_id"]
    bad = client.post(f"/api/runs/{rid}/override", json={"field": "garaging_state", "value": "Narnia!"})
    assert bad.status_code == 400
    assert client.post(f"/api/runs/{rid}/override", json={"field": "nope", "value": "1"}).status_code == 400
    assert client.get(f"/api/runs/{rid}/pdf").mimetype == "application/pdf"
    assert client.get("/api/runs/missing").status_code == 404


def test_undo_redo_and_restore_original_values(client):
    rid = upload(client).get_json()["run_id"]
    post = lambda path, **kw: client.post(f"/api/runs/{rid}/{path}", **kw)
    assert post("undo").status_code == 400  # nothing to undo yet

    post("override", json={"field": "operating_radius_miles", "value": "180"})
    post("override", json={"field": "hazardous_materials", "value": "No"})
    state = client.get(f"/api/runs/{rid}").get_json()
    assert statuses(state)["R3"] == "FLAG" and statuses(state)["R4"] == "PASS"
    assert len(state["undo"]) == 2 and state["redo"] == []

    undone = post("undo").get_json()  # reverts the hazmat fix only
    assert statuses(undone)["R4"] == "UNKNOWN" and statuses(undone)["R3"] == "FLAG"
    assert len(undone["redo"]) == 1
    redone = post("redo").get_json()
    assert statuses(redone)["R4"] == "PASS" and redone["redo"] == []

    post("undo")
    post("override", json={"field": "garaging_state", "value": "NV"})  # a new fix clears Redo
    assert client.get(f"/api/runs/{rid}").get_json()["redo"] == []

    restored = post("reset").get_json()  # back to what the AI extracted
    assert statuses(restored)["R3"] == "UNKNOWN" and statuses(restored)["R4"] == "UNKNOWN"
    assert not any(f["override"] for f in restored["fields"].values())
    assert post("reset").status_code == 400
    back = post("undo").get_json()  # the restore itself can be undone
    assert statuses(back)["R3"] == "FLAG"


def test_save_and_restore_draft_and_rename(client):
    rid = upload(client).get_json()["run_id"]
    saved = client.post(f"/api/runs/{rid}/draft", json={"text": "My edited email"}).get_json()
    assert saved["draft_edited"] == "My edited email" and not saved["draft_stale"]
    stale = client.post(f"/api/runs/{rid}/override", json={"field": "operating_radius_miles", "value": "180"}).get_json()
    assert stale["draft_edited"] == "My edited email" and stale["draft_stale"]
    restored = client.post(f"/api/runs/{rid}/draft", json={"restore": True}).get_json()
    assert restored["draft_edited"] is None and not restored["draft_stale"]

    assert client.post(f"/api/runs/{rid}/rename", json={"name": "  "}).status_code == 400
    client.post(f"/api/runs/{rid}/rename", json={"name": "Metro renewal"})
    assert client.get("/api/runs").get_json()["runs"][0]["name"] == "Metro renewal"


def test_email_feedback_loop_revise_and_previous_version(client, monkeypatch):
    rid = upload(client).get_json()["run_id"]
    assert client.post(f"/api/runs/{rid}/draft/revise", json={"instruction": " "}).status_code == 400

    revised = client.post(f"/api/runs/{rid}/draft/revise", json={"instruction": "make it shorter"}).get_json()
    assert revised["draft_edited"].endswith("(Draft only — do not auto-send.)")
    assert revised["draft_feedback"][0]["instruction"] == "make it shorter"
    assert revised["draft_history"] == ["Fake narrative."]  # the AI draft it replaced

    back = client.post(f"/api/runs/{rid}/draft/previous").get_json()
    assert back["draft_edited"] == "Fake narrative." and back["draft_history"] == []
    assert client.post(f"/api/runs/{rid}/draft/previous").status_code == 400

    def boom(*args, **kwargs):
        raise app_module.LLMUnavailable("down")
    monkeypatch.setattr(app_module, "revise_draft", boom)
    failed = client.post(f"/api/runs/{rid}/draft/revise", json={"instruction": "shorter"})
    assert failed.status_code == 503 and "unchanged" in failed.get_json()["error"]


def test_delete_restore_and_delete_forever(client):
    rid = upload(client).get_json()["run_id"]
    upload_dir = app_module.DB.upload_dir(rid)
    assert client.delete(f"/api/runs/{rid}").status_code == 200
    assert client.get("/api/runs").get_json()["runs"] == []
    assert [r["run_id"] for r in client.get("/api/runs?deleted=1").get_json()["runs"]] == [rid]

    assert client.post(f"/api/runs/{rid}/restore").status_code == 200
    assert [r["run_id"] for r in client.get("/api/runs").get_json()["runs"]] == [rid]

    client.delete(f"/api/runs/{rid}")
    assert client.delete(f"/api/runs/{rid}?forever=1").status_code == 200
    assert client.get("/api/runs?deleted=1").get_json()["runs"] == []
    assert client.get(f"/api/runs/{rid}").status_code == 404 and not upload_dir.exists()
    assert client.delete(f"/api/runs/{rid}").status_code == 404



def test_reviews_queue_and_run_one_at_a_time(client, monkeypatch):
    import threading
    gate, real = threading.Event(), app_module._run_pipeline

    def slow(bundle, llm):
        gate.wait(5)
        return real(bundle, llm)
    monkeypatch.setattr(app_module, "_run_pipeline", slow)

    def start(name):
        return client.post("/api/runs", content_type="multipart/form-data", data={
            "name": name, "application_pdf": (open(SUB_C / "application.pdf", "rb"), "application.pdf"),
        })
    first, second = start("one").get_json()["run_id"], start("two").get_json()["run_id"]
    assert client.post(f"/api/runs/{second}/override", json={"field": "garaging_state", "value": "CA"}).status_code == 409
    listed = {r["name"]: r["overall_status"] for r in client.get("/api/runs").get_json()["runs"]}
    assert listed["two"] == "QUEUED" and listed["one"] in ("QUEUED", "PROCESSING")

    gate.set()
    app_module.wait_idle()
    done = {r["name"]: r["overall_status"] for r in client.get("/api/runs").get_json()["runs"]}
    assert set(done.values()) <= {"READY_FOR_REVIEW", "NEEDS_REVIEW"}
    assert "state" not in client.get(f"/api/runs/{first}").get_json()


def test_interrupted_reviews_are_marked_failed(client):
    app_module.DB.save_pending("abc", "stuck")
    app_module.DB.fail_interrupted()
    run = client.get("/api/runs/abc").get_json()
    assert run["state"] == "FAILED" and "restarted" in run["error"]
