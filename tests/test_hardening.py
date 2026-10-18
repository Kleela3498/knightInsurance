"""Review findings: auth header, loss-runs detection, ungrounded values, PDF page locator, radius ranges."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knight import llm as llm_module  # noqa: E402
from knight.extract import _assemble, detect_loss_runs, norm_radius  # noqa: E402
from knight.llm import RealLLM  # noqa: E402


# ---- API key header (hosted providers need "Bearer <key>")

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_api_key_is_sent_as_a_bearer_token(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req.get_header("Authorization"))
        return _Resp(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    client = RealLLM("http://x/v1", "m", api_key="test-key-123")
    assert client.chat("s", "u") == "hi"
    assert client.ping() is True
    assert seen == ["Bearer test-key-123", "Bearer test-key-123"]


def test_no_authorization_header_without_a_key(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_module.urllib.request, "urlopen",
                        lambda req, timeout=0: seen.append(req.get_header("Authorization")) or _Resp(b"{}"))
    assert RealLLM("http://x/v1", "m").ping() is True
    assert seen == [None]


# ---- loss runs: the document itself must be the report

REPORT = "Fictional assessment data\n1\nLoss runs report\nExperience period 2023-2025\n"


def test_a_real_loss_runs_heading_passes():
    assert detect_loss_runs([{"filename": "x.pdf", "text": REPORT}])[0] is True


def test_cover_letter_that_promises_loss_runs_is_not_a_report():
    letter = "Cover letter\nThanks for your time.\nLoss runs to follow under separate cover.\n"
    present, _, notes = detect_loss_runs([{"filename": "cover.pdf", "text": letter}])
    assert present is False
    assert any("promise" in n for n in notes)


def test_loss_runs_in_the_filename_alone_is_unknown_for_a_human():
    present, _, _ = detect_loss_runs([{"filename": "loss_runs.pdf", "text": "Hello.\nPlease see the policy.\n"}])
    assert present is None


def test_a_body_mention_without_a_heading_is_unknown_not_pass():
    text = "Account summary\nNothing special here.\nSee attached loss runs 2023-2025 below.\n"
    assert detect_loss_runs([{"filename": "summary.pdf", "text": text}])[0] is None


def test_a_long_sentence_is_not_a_heading():
    text = "This letter confirms that our loss history report is being prepared by the carrier for you\n"
    assert detect_loss_runs([{"filename": "n.pdf", "text": text}])[0] is not True


# ---- radius: ranges are ambiguous

@pytest.mark.parametrize("text", ["150 to 200 miles", "150-200", "between 150 and 200 miles", "150/200"])
def test_radius_ranges_are_invalid(text):
    assert norm_radius(text) == (None, False)


def test_radius_with_other_numbers_in_the_sentence_still_reads():
    assert norm_radius("within 150 miles of Los Angeles, CA 90001") == (150.0, True)


def test_an_unparseable_value_in_both_sources_is_invalid_not_missing():
    cells = {"operating_radius_miles": {"value": "150 to 200", "excerpt": "Operating radius miles: 150 to 200"}}
    text = "Operating radius miles: 150 to 200\n"
    f = _assemble_for(_cells(cells, cells), text, [(1, text)])["operating_radius_miles"]
    assert f.state == "INVALID" and len(f.evidence) == 2


# ---- invented values and PDF page locator

def _cells(email=None, app=None):
    blank = {"value": None, "excerpt": None}
    fields = ["business_name", "requested_effective_date", "garaging_state", "operations_description",
              "declared_vehicle_count", "operating_radius_miles", "hazardous_materials"]
    return {"email": {f: (email or {}).get(f, blank) for f in fields},
            "application": {f: (app or {}).get(f, blank) for f in fields}}


def _assemble_for(extracted, email_text, pages):
    return _assemble(extracted, email_text, "e.txt", pages, "a.pdf", None, "v.csv", [])


def test_a_value_not_in_the_documents_is_ignored_and_explained():
    cells = {"operating_radius_miles": {"value": "90", "excerpt": "Operating radius miles: 90"}}
    f = _assemble_for(_cells(cells, cells), "Hello.\nBusiness name: Acme\n", [(1, "Application\nBusiness name Acme\n")])
    r = f["operating_radius_miles"]
    assert r.state == "MISSING" and r.value is None
    assert any("ignored" in n for n in r.notes)


def test_a_value_the_model_normalized_is_still_found():
    email = "Effective date: November 1, 2026\nGaraging state: California\nVehicles: three\n"
    cells = {"requested_effective_date": {"value": "2026-11-01", "excerpt": None},
             "garaging_state": {"value": "CA", "excerpt": None},
             "declared_vehicle_count": {"value": 3, "excerpt": None}}
    f = _assemble_for(_cells(cells), email, [(1, "x")])
    assert f["requested_effective_date"].state == "KNOWN"
    assert f["garaging_state"].state == "KNOWN"
    assert f["declared_vehicle_count"].state == "KNOWN" and f["declared_vehicle_count"].value == 3


def test_pdf_locator_names_the_page_the_value_is_on():
    pages = [(1, "Cover page\nNothing here\n"), (2, "Operating radius miles 80\n")]
    cells = {"operating_radius_miles": {"value": "80", "excerpt": "Operating radius miles 80"}}
    f = _assemble_for(_cells(None, cells), "Hello.\n", pages)
    assert f["operating_radius_miles"].evidence[-1].locator == "PDF page 2"
