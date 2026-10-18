"""Extraction-pipeline unit tests: CSV counting, loss-runs inventory, merging."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knight.extract import (
    count_csv_vehicles,
    detect_loss_runs,
    merge_field,
    norm_count,
    norm_date,
    norm_hazmat,
    norm_radius,
    norm_state,
)
from knight.models import Evidence


def ev(src, excerpt):
    return Evidence("f", src, excerpt)


def test_csv_counts_nonblank_rows_header_excluded():
    csv_text = "vehicle_id,vin,year,make,model\nA-01,X,2022,M,V\n\nA-02,Y,2023,M,V\n"
    count, locator, err = count_csv_vehicles(csv_text)
    assert count == 2 and err is None
    assert "header excluded" in locator


def test_csv_zero_rows_valid():
    count, _, err = count_csv_vehicles("vehicle_id,vin,year,make,model\n")
    assert count == 0 and err is None


def test_csv_unreadable_reports_error():
    count, locator, err = count_csv_vehicles("")
    assert count is None and err is not None


def test_loss_runs_readable_report_passes():
    atts = [{"filename": "loss_runs.pdf",
             "text": "Loss runs report\nReported claims 0"}]
    present, evidence, _ = detect_loss_runs(atts)
    assert present is True


def test_loss_runs_promise_is_not_attachment():
    present, evidence, notes = detect_loss_runs([])
    assert present is False
    assert "promise" in evidence.excerpt_or_observation


def test_loss_runs_unreadable_attachment_is_unknown():
    present, evidence, _ = detect_loss_runs([{"filename": "scan.pdf", "text": ""}])
    assert present is None


def test_conflict_keeps_alternatives_and_null_value():
    f = merge_field("operating_radius_miles", "180", "80",
                    ev("email body", "180"), ev("PDF page 1", "80"), "inspected")
    assert f.state == "CONFLICT" and f.value is None
    assert len(f.alternatives) == 2


def test_missing_in_one_source_is_not_conflict():
    f = merge_field("hazardous_materials", "No", "Not provided",
                    ev("email body", "No"), ev("PDF page 1", "Not provided"), "inspected")
    assert f.state == "KNOWN" and f.value is False


def test_both_missing_records_inspected_sources():
    f = merge_field("operating_radius_miles", "Not provided", "Not provided",
                    ev("email body", "Not provided"), ev("PDF page 1", "Not provided"), "inspected")
    assert f.state == "MISSING"
    assert not any("invent" in n.lower() for n in f.notes)


def test_norm_date_rejects_invalid():
    assert norm_date("2026-13-99") == (None, False)
    assert norm_date("2026-11-01") == ("2026-11-01", True)
    assert norm_date("Not provided") == (None, True)


def test_norm_state_maps_california():
    assert norm_state("California") == ("CA", True)
    assert norm_state("ca") == ("CA", True)


def test_norm_radius_rejects_negative_and_nonnumeric():
    assert norm_radius("-5") == (None, False)
    assert norm_radius("about a hundred") == (None, False)
    assert norm_radius("150 miles") == (150.0, True)


def test_norm_hazmat_absence_is_not_no():
    assert norm_hazmat("Not provided") == (None, True)
    assert norm_hazmat("No") == (False, True)
    assert norm_hazmat("Yes") == (True, True)


def test_norm_count_non_integer_is_invalid():
    assert norm_count("three") == (None, False)
    assert norm_count("3") == (3, True)


def test_code_fence_is_stripped_but_garbage_is_not():
    import json
    from knight.extract import _strip_code_fence
    assert json.loads(_strip_code_fence('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(_strip_code_fence('```\n{"a": 1}\n```')) == {"a": 1}
    assert _strip_code_fence('{"a": 1}') == '{"a": 1}'
    assert _strip_code_fence("Sure! here you go") == "Sure! here you go"
