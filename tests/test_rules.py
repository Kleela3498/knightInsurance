"""Rule-engine tests: truth tables for SUB-A/B/C, boundaries, precedence.

No LLM is involved; FieldValues are constructed the way extract.merge_field
produces them (verified against the sample files by hand).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knight.extract import merge_field
from knight.models import Evidence, FieldValue
from knight.rules import evaluate_rules


def ev(src="email body", excerpt="x"):
    return Evidence("f", src, excerpt)


def known(name, value):
    return FieldValue(name=name, value=value, state="KNOWN", evidence=[ev()])


def fields_for(packet: dict) -> dict:
    """Build FieldValues through the real merge path from per-source raw values."""
    out = {}
    for name, (e_raw, p_raw) in packet["text_fields"].items():
        out[name] = merge_field(name, e_raw, p_raw, ev("email body", str(e_raw)),
                               ev("PDF page 1", str(p_raw)), "inspected")
    out["schedule_vehicle_count"] = known("schedule_vehicle_count", packet["schedule_count"])
    loss = FieldValue(name="loss_runs_present", value=packet["loss_runs"],
                      state="KNOWN", evidence=[ev("inventory", "inv")])
    out["loss_runs_present"] = loss
    return out


SUB_A = {
    "text_fields": {
        "business_name": ("Pacific Harbor Delivery Example LLC", "Pacific Harbor Delivery Example LLC"),
        "requested_effective_date": ("2026-11-01", "2026-11-01"),
        "garaging_state": ("CA", "California"),
        "operations_description": ("Local delivery of packaged retail goods.", "Local delivery of packaged retail goods"),
        "declared_vehicle_count": ("3", "3"),
        "operating_radius_miles": ("150", "150"),
        "hazardous_materials": ("No", "No"),
    },
    "schedule_count": 3,
    "loss_runs": True,
}

SUB_B = {
    "text_fields": {
        "business_name": ("Canyon Parcel Example LLC", "Canyon Parcel Example LLC"),
        "requested_effective_date": ("Not provided", "Not provided"),
        "garaging_state": ("CA", "California"),
        "operations_description": ("Local delivery of packaged retail goods.", "Local delivery of packaged retail goods"),
        "declared_vehicle_count": ("2", "2"),
        "operating_radius_miles": ("Not provided", "Not provided"),
        "hazardous_materials": ("Not provided", "Not provided"),
    },
    "schedule_count": 2,
    "loss_runs": False,
}

SUB_C = {
    "text_fields": {
        "business_name": ("Metro Route Example LLC", "Metro Route Example LLC"),
        "requested_effective_date": ("2026-11-01", "2026-11-01"),
        "garaging_state": ("CA", "California"),
        "operations_description": ("Local delivery of packaged retail goods.", "Local delivery of packaged retail goods"),
        "declared_vehicle_count": ("4", "4"),
        "operating_radius_miles": ("180", "80"),
        "hazardous_materials": ("Yes", "No"),
    },
    "schedule_count": 3,
    "loss_runs": False,
}


def statuses(fields):
    rules, overall = evaluate_rules(fields)
    return {r.rule_id: r.status for r in rules}, overall


def test_sub_a_all_clean():
    st, overall = statuses(fields_for(SUB_A))
    assert st == {"R1": "PASS", "R2": "PASS", "R3": "PASS", "R4": "PASS", "R5": "PASS"}
    assert overall == "READY_FOR_REVIEW"


def test_sub_b_missing_information():
    st, overall = statuses(fields_for(SUB_B))
    assert st == {"R1": "FLAG", "R2": "PASS", "R3": "UNKNOWN", "R4": "UNKNOWN", "R5": "FLAG"}
    assert overall == "NEEDS_REVIEW"


def test_sub_c_conflict_case():
    st, overall = statuses(fields_for(SUB_C))
    assert st == {"R1": "PASS", "R2": "FLAG", "R3": "UNKNOWN", "R4": "UNKNOWN", "R5": "FLAG"}
    assert overall == "NEEDS_REVIEW"


def test_sub_c_conflicts_detected_before_thresholds():
    f = fields_for(SUB_C)
    assert f["operating_radius_miles"].state == "CONFLICT"
    assert f["operating_radius_miles"].value is None
    assert f["hazardous_materials"].state == "CONFLICT"
    # Alternatives preserved, no source preferred
    assert {a["source"] for a in f["operating_radius_miles"].alternatives} == {"email", "application"}


def test_r3_boundary_150_pass_150_point_1_flag():
    base = fields_for(SUB_A)
    base["operating_radius_miles"] = known("operating_radius_miles", 150.0)
    st, _ = statuses(base)
    assert st["R3"] == "PASS"
    base["operating_radius_miles"] = known("operating_radius_miles", 150.1)
    st, _ = statuses(base)
    assert st["R3"] == "FLAG"


def test_r3_harmless_float_difference_is_not_a_conflict():
    f = merge_field("operating_radius_miles", "150", "150.0",
                    ev("email body", "150"), ev("PDF page 1", "150.0"), "inspected")
    assert f.state == "KNOWN" and f.value == 150.0


def test_r1_absence_precedence_over_conflict():
    f = fields_for(SUB_A)
    f["requested_effective_date"] = FieldValue(name="requested_effective_date", value=None, state="MISSING")
    f["business_name"] = merge_field("business_name", "Acme LLC", "ACME llc Inc",
                                     ev("email body", "Acme LLC"), ev("PDF page 1", "ACME llc Inc"), "inspected")
    assert f["business_name"].state == "CONFLICT"
    st, _ = statuses(f)
    assert st["R1"] == "FLAG"  # absent date wins over conflicting name


def test_r2_unequal_counts_flag():
    f = fields_for(SUB_A)
    f["declared_vehicle_count"] = known("declared_vehicle_count", 5)
    f["schedule_vehicle_count"] = known("schedule_vehicle_count", 3)
    st, _ = statuses(f)
    assert st["R2"] == "FLAG"


def test_zero_vehicles_is_valid():
    f = fields_for(SUB_A)
    f["declared_vehicle_count"] = known("declared_vehicle_count", 0)
    f["schedule_vehicle_count"] = known("schedule_vehicle_count", 0)
    st, _ = statuses(f)
    assert st["R2"] == "PASS"


def test_r2_unreadable_csv_is_unknown():
    f = fields_for(SUB_A)
    bad = FieldValue(name="schedule_vehicle_count", value=None, state="INVALID",
                     evidence=[ev("vehicles.csv", "unreadable")])
    f["schedule_vehicle_count"] = bad
    st, _ = statuses(f)
    assert st["R2"] == "UNKNOWN"


def test_garaging_state_normalization_is_not_a_conflict():
    f = fields_for(SUB_A)
    assert f["garaging_state"].state == "KNOWN"
    assert f["garaging_state"].value == "CA"


def test_ops_trailing_punctuation_is_not_a_conflict():
    f = fields_for(SUB_A)
    assert f["operations_description"].state == "KNOWN"
