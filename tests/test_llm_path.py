"""Model-free tests for the LLM path: a fake client stands in for Ollama."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knight.extract import extract_by_labels, extract_packet, ground_excerpt
from knight.llm import LLMUnavailable
from knight.narratives import generate_narratives
from knight.rules import evaluate_rules

EMAIL = "Business name: Acme LLC\nOperating radius miles: 180\n"
PDF_TEXT = "Business name\nAcme LLC\nOperating radius miles\n80\n"


class FakeLLM:
    model = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def chat(self, system, user, max_tokens=1200):
        if self.error:
            raise self.error
        return self.reply


def cell(value, excerpt=None):
    return {"value": value, "excerpt": excerpt}


def reply(email: dict, app: dict) -> str:
    blank = {f: cell(None) for f in (
        "business_name", "requested_effective_date", "garaging_state", "operations_description",
        "declared_vehicle_count", "operating_radius_miles", "hazardous_materials")}
    return json.dumps({"email": {**blank, **email}, "application": {**blank, **app}})


def run(llm):
    return extract_packet(EMAIL, "broker_email.txt", [(1, PDF_TEXT)], "application.pdf",
                          "id\nA\nB\n", "vehicles.csv", [], llm)


def test_source_conflict_flows_through_extraction_with_both_sources_kept():
    fields = run(FakeLLM(reply(
        {"operating_radius_miles": cell("180", "Operating radius miles: 180")},
        {"operating_radius_miles": cell("80", "80")})))
    radius = fields["operating_radius_miles"]
    assert radius.state == "CONFLICT"
    assert {a["source"] for a in radius.alternatives} == {"email", "application"}


def test_missing_field_names_the_sources_checked():
    fields = run(FakeLLM(reply({}, {})))
    note = fields["hazardous_materials"].notes[0]
    assert "broker_email.txt" in note and "application.pdf" in note


def test_malformed_json_is_a_visible_failure():
    with pytest.raises(LLMUnavailable):
        run(FakeLLM("Sorry, I cannot do that."))


def test_missing_field_in_model_reply_is_a_visible_failure():
    with pytest.raises(LLMUnavailable):
        run(FakeLLM(json.dumps({"email": {}, "application": {}})))


def test_unreachable_model_propagates_and_labels_still_extract():
    with pytest.raises(LLMUnavailable):
        run(FakeLLM(error=LLMUnavailable("down")))
    fields = extract_by_labels(EMAIL, "broker_email.txt", [(1, PDF_TEXT)], "application.pdf",
                               "id\nA\nB\n", "vehicles.csv", [])
    assert fields["business_name"].effective_value == "Acme LLC"
    radius = fields["operating_radius_miles"]
    assert radius.state == "CONFLICT" and {a["source"] for a in radius.alternatives} == {"email", "application"}
    assert fields["hazardous_materials"].state == "MISSING"


def test_invented_excerpt_is_replaced_by_the_real_source_line():
    assert ground_excerpt("Hello underwriting team,", "Acme LLC", EMAIL) == "Business name: Acme LLC"
    assert ground_excerpt("Operating radius miles: 180", "180", EMAIL) == "Operating radius miles: 180"
    assert ground_excerpt("made up", None, EMAIL) is None
    # PDF table: a bare value line gets its label line prepended; trailing period is ignored.
    assert ground_excerpt("80", "80", PDF_TEXT) == "Operating radius miles 80"
    assert ground_excerpt(None, "Acme LLC.", PDF_TEXT) == "Business name Acme LLC"
    # Verbatim but unrelated to the value: still replaced.
    assert ground_excerpt("Operating radius miles: 180", "Acme LLC", EMAIL) == "Business name: Acme LLC"


def test_narration_falls_back_and_is_labelled_when_model_is_down():
    fields = run(FakeLLM(reply({}, {})))
    rules, overall = evaluate_rules(fields)
    summary, s_origin, draft, d_origin = generate_narratives(
        "T", fields, rules, overall, FakeLLM(error=LLMUnavailable("down")))
    assert s_origin == d_origin == "deterministic-fallback"
    assert "checks need attention" in summary and "do not auto-send" in draft


def test_draft_falls_back_on_its_own_when_only_the_second_call_fails():
    fields = run(FakeLLM(reply({}, {})))
    rules, overall = evaluate_rules(fields)

    class SecondCallFails(FakeLLM):
        calls = 0

        def chat(self, system, user, max_tokens=1200):
            self.calls += 1
            if self.calls == 2:
                raise LLMUnavailable("timeout")
            return "AI summary."

    summary, s_origin, draft, d_origin = generate_narratives("T", fields, rules, overall, SecondCallFails())
    assert (summary, s_origin, d_origin) == ("AI summary.", "llm", "deterministic-fallback")
    assert "do not auto-send" in draft
