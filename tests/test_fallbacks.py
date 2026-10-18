"""What still works when the model is down: label extraction, summary and email templates."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KNIGHT_DB", str(Path(tempfile.mkdtemp()) / "import.db"))

import app as app_module  # noqa: E402
from knight.extract import extract_by_labels, label_extract  # noqa: E402
from knight.llm import LLMUnavailable  # noqa: E402
from knight.narratives import template_draft, template_summary  # noqa: E402
from knight.rules import evaluate_rules  # noqa: E402
from knight.store import Store  # noqa: E402

PACKETS = ROOT / "sample_packets"


def packet_fields(packet: str):
    """Read a sample packet with no model at all."""
    folder = PACKETS / packet
    pages = app_module.read_pdf_pages(str(folder / "application.pdf"))
    csv = folder / "vehicles.csv"
    attachments = [{"filename": p.name, "text": app_module._pdf_text(p)} for p in folder.glob("loss_runs.pdf")]
    return extract_by_labels(
        (folder / "broker_email.txt").read_text(encoding="utf-8"), "broker_email.txt",
        pages, "application.pdf",
        csv.read_text(encoding="utf-8") if csv.exists() else None, "vehicles.csv", attachments)


# ---- label extractor edge cases ----

def test_email_label_is_case_insensitive_and_whitespace_tolerant():
    out = label_extract("  BUSINESS NAME :   Acme LLC  \n", "")
    assert out["email"]["business_name"] == {"value": "Acme LLC", "excerpt": "BUSINESS NAME :   Acme LLC"}


def test_pdf_label_value_rows_and_inline_labels():
    out = label_extract("", "Business name\nAcme LLC\nGaraging state: CA\n")
    assert out["application"]["business_name"]["value"] == "Acme LLC"
    assert out["application"]["garaging_state"]["value"] == "CA"


def test_label_followed_directly_by_another_label_has_no_value():
    out = label_extract("", "Business name\nRequested effective date\n2026-11-01\n")
    assert out["application"]["business_name"]["value"] is None
    assert out["application"]["requested_effective_date"]["value"] == "2026-11-01"


def test_empty_or_scanned_pdf_means_everything_is_missing():
    out = label_extract("", "")
    assert all(c["value"] is None for c in out["application"].values())


def test_not_provided_becomes_a_missing_field():
    fields = packet_fields("SUB-B")
    assert fields["requested_effective_date"].state == "MISSING"


# ---- the three sample packets, read with no model, still reach the right outcome ----

@pytest.mark.parametrize("packet, expected", [
    ("SUB-A", {"R1": "PASS", "R2": "PASS", "R3": "PASS", "R4": "PASS", "R5": "PASS"}),
    ("SUB-B", {"R1": "FLAG", "R2": "PASS", "R3": "UNKNOWN", "R4": "UNKNOWN", "R5": "FLAG"}),
    ("SUB-C", {"R1": "PASS", "R2": "FLAG", "R3": "UNKNOWN", "R4": "UNKNOWN", "R5": "FLAG"}),
])
def test_sample_packets_without_any_model(packet, expected):
    rules, _ = evaluate_rules(packet_fields(packet))
    assert {r.rule_id: r.status for r in rules} == expected


# ---- templates ----

def sub_c():
    fields = packet_fields("SUB-C")
    rules, overall = evaluate_rules(fields)
    return fields, rules, overall


def test_standard_template_asks_for_every_open_item_with_real_values():
    fields, rules, _ = sub_c()
    text = template_draft("standard", "SUB-C", fields, rules)
    assert "loss runs" in text and "180" in text and "80" in text and "hazardous" in text.lower()
    assert text.endswith("(Draft only — do not auto-send.)")


def test_missing_and_clarify_templates_split_the_items():
    fields, rules, _ = sub_c()
    missing = template_draft("missing", "SUB-C", fields, rules)
    clarify = template_draft("clarify", "SUB-C", fields, rules)
    assert "loss runs" in missing and "operating radius" not in missing
    assert "operating radius" in clarify and "loss runs" not in clarify


def test_acknowledge_template_asks_for_nothing():
    fields, rules, _ = sub_c()
    text = template_draft("acknowledge", "SUB-C", fields, rules)
    assert "received" in text and "1." not in text


def test_all_pass_packet_gets_an_acknowledgement_and_summary():
    fields = packet_fields("SUB-A")
    rules, overall = evaluate_rules(fields)
    assert "received" in template_draft("standard", "SUB-A", fields, rules)
    assert "all 5 checks passed" in template_summary("SUB-A", fields, rules, overall)


def test_template_summary_lists_each_open_rule():
    fields, rules, overall = sub_c()
    text = template_summary("SUB-C", fields, rules, overall)
    for rid in ("R2", "R3", "R4", "R5"):
        assert rid in text and f"{rid} " in text
    assert "R1 " not in text and "not a coverage decision" in text


# ---- through the app, with the model down ----

class DownLLM:
    model = "down"
    base_url = "down"

    def chat(self, system, user, max_tokens=1200):
        raise LLMUnavailable("The model is not reachable.")

    def ping(self):
        return False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB", Store(tmp_path / "t.db"))
    monkeypatch.setattr(app_module.RealLLM, "from_env", classmethod(lambda cls: DownLLM()))
    return app_module.app.test_client()


def upload(client, packet="SUB-C"):
    folder = PACKETS / packet
    started = client.post("/api/runs", content_type="multipart/form-data", data={
        "name": packet,
        "application_pdf": (open(folder / "application.pdf", "rb"), "application.pdf"),
        "email_file": (open(folder / "broker_email.txt", "rb"), "broker_email.txt"),
        "vehicles_csv": (open(folder / "vehicles.csv", "rb"), "vehicles.csv"),
    })
    app_module.wait_idle()
    return client.get(f"/api/runs/{started.get_json()['run_id']}")


def test_run_with_model_down_still_has_fields_rules_summary_and_email(client):
    run = upload(client).get_json()
    assert any("label matching" in w for w in run["warnings"])
    assert {r["rule_id"]: r["status"] for r in run["rules"]} == {
        "R1": "PASS", "R2": "FLAG", "R3": "UNKNOWN", "R4": "UNKNOWN", "R5": "FLAG"}
    assert run["summary_origin"] == run["draft_origin"] == "deterministic-fallback"
    assert "loss runs" in run["draft_email"]


def test_template_endpoint_swaps_the_draft_and_previous_version_undoes_it(client):
    run = upload(client).get_json()
    rid, before = run["run_id"], run["draft_email"]
    swapped = client.post(f"/api/runs/{rid}/draft/template", json={"kind": "clarify"}).get_json()
    assert "operating radius" in swapped["draft_edited"] and "loss runs" not in swapped["draft_edited"]
    back = client.post(f"/api/runs/{rid}/draft/previous").get_json()
    assert back["draft_edited"] == before


def test_template_endpoint_rejects_an_unknown_kind(client):
    rid = upload(client).get_json()["run_id"]
    assert client.post(f"/api/runs/{rid}/draft/template", json={"kind": "nope"}).status_code == 400
