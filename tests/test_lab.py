"""The Edge-Case Lab: every scenario must pass, and it can be switched off with KNIGHT_DEV_MODE=0."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KNIGHT_DB", str(Path(tempfile.mkdtemp()) / "import.db"))

import app as app_module  # noqa: E402
from knight.lab import SCENARIOS, run_lab  # noqa: E402


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_every_lab_scenario_passes(scenario):
    from knight.lab import run_scenario
    result = run_scenario(scenario, app_module._run_pipeline)
    assert result["ok"], f"{result['title']}: {result['problem']} expected {result['expected']} got {result['actual']}"


def test_every_scenario_explains_itself_in_plain_words():
    for r in run_lab(app_module._run_pipeline):
        for key in ("group", "what", "why", "should"):
            assert r[key].strip(), f"{r['id']} has no '{key}'"
        assert r["did"] and r["rules"], f"{r['id']} does not say what the app did"
        assert r["summary"].strip() and r["draft_email"].strip(), f"{r['id']} shows no summary or email"
        assert {x["id"] for x in r["rules"]} == {"R1", "R2", "R3", "R4", "R5"}


def test_lab_reports_a_failing_expectation(monkeypatch):
    monkeypatch.setitem(SCENARIOS[0].expect, "R1", "FLAG")
    assert run_lab(app_module._run_pipeline)[0]["ok"] is False


def test_lab_is_on_by_default_and_can_be_turned_off(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.delenv("KNIGHT_DEV_MODE", raising=False)
    assert client.get("/api/dev").get_json() == {"enabled": True}
    results = client.post("/api/dev/lab").get_json()["results"]
    assert len(results) == len(SCENARIOS) and all(r["ok"] for r in results)
    monkeypatch.setenv("KNIGHT_DEV_MODE", "0")
    assert client.get("/api/dev").get_json() == {"enabled": False}
    assert client.post("/api/dev/lab").status_code == 404
