"""Deterministic implementation of R1..R5 from rules.json.

Conflict-before-threshold for R2-R4, absence-precedence for R1, and the
150-mile boundary are implemented exactly as specified. No LLM is consulted
here; tests run against this module with zero model dependency.
"""
from __future__ import annotations

from .models import FieldValue, RuleResult

RADIUS_THRESHOLD = 150


def _r1(fields: dict[str, FieldValue]) -> RuleResult:
    name, date = fields["business_name"], fields["requested_effective_date"]
    inputs = {"business_name": name.effective_value, "requested_effective_date": date.effective_value}
    refs = ["business_name", "requested_effective_date"]
    if name.rule_state == "MISSING" or date.rule_state == "MISSING":
        missing = [f for f, v in (("business_name", name), ("requested_effective_date", date)) if v.rule_state == "MISSING"]
        return RuleResult("R1", "Required identity and effective date", "FLAG",
                          f"Required field(s) absent: {', '.join(missing)}. "
                          "Absence takes precedence over any other unresolved value.", inputs, refs)
    if name.rule_state in ("INVALID", "CONFLICT") or date.rule_state in ("INVALID", "CONFLICT"):
        bad = [f"{f} ({v.rule_state})" for f, v in (("business_name", name), ("requested_effective_date", date))
               if v.rule_state in ("INVALID", "CONFLICT")]
        return RuleResult("R1", "Required identity and effective date", "UNKNOWN",
                          f"No absence, but required field(s) unresolved: {', '.join(bad)}.", inputs, refs)
    return RuleResult("R1", "Required identity and effective date", "PASS",
                      f"Name '{name.effective_value}' and effective date {date.effective_value} present, valid, non-conflicting.",
                      inputs, refs)


def _r2(fields: dict[str, FieldValue]) -> RuleResult:
    declared, sched = fields["declared_vehicle_count"], fields["schedule_vehicle_count"]
    inputs = {"declared_vehicle_count": declared.effective_value, "schedule_vehicle_count": sched.effective_value}
    refs = ["declared_vehicle_count", "schedule_vehicle_count"]
    dv, sv = declared.effective_value, sched.effective_value
    dv_ok = isinstance(dv, int) and dv >= 0 and declared.rule_state == "KNOWN"
    sv_ok = isinstance(sv, int) and sv >= 0 and sched.rule_state == "KNOWN"
    if dv_ok and sv_ok:
        if dv == sv:
            return RuleResult("R2", "Vehicle count reconciliation", "PASS",
                              f"Declared count ({dv}) equals schedule count ({sv}).", inputs, refs)
        return RuleResult("R2", "Vehicle count reconciliation", "FLAG",
                          f"Both counts valid but differ: declared {dv} vs schedule {sv}.", inputs, refs)
    why = []
    for label, f in (("declared", declared), ("schedule", sched)):
        if f.rule_state != "KNOWN":
            why.append(f"{label} count is {f.rule_state}")
    return RuleResult("R2", "Vehicle count reconciliation", "UNKNOWN",
                      "Cannot reconcile: " + "; ".join(why) + ".", inputs, refs)


def _r3(fields: dict[str, FieldValue]) -> RuleResult:
    r = fields["operating_radius_miles"]
    inputs = {"operating_radius_miles": r.effective_value}
    refs = ["operating_radius_miles"]
    if r.rule_state == "CONFLICT":
        alts = ", ".join(f"{a['source']}={a['value']}" for a in r.alternatives)
        return RuleResult("R3", "Operating radius", "UNKNOWN",
                          f"Conflicting radius values ({alts}); conflicts are evaluated before thresholds.", inputs, refs)
    v = r.effective_value
    if r.rule_state != "KNOWN" or not isinstance(v, (int, float)):
        return RuleResult("R3", "Operating radius", "UNKNOWN",
                          f"Radius is {r.rule_state.lower()} (absent, invalid, negative or non-numeric).", inputs, refs)
    if v <= RADIUS_THRESHOLD:
        return RuleResult("R3", "Operating radius", "PASS",
                          f"Valid radius {v} miles is within the {RADIUS_THRESHOLD}-mile threshold "
                          f"({RADIUS_THRESHOLD} allowed).", inputs, refs)
    return RuleResult("R3", "Operating radius", "FLAG",
                      f"Valid radius {v} miles exceeds the {RADIUS_THRESHOLD}-mile threshold.", inputs, refs)


def _r4(fields: dict[str, FieldValue]) -> RuleResult:
    h = fields["hazardous_materials"]
    inputs = {"hazardous_materials": h.effective_value}
    refs = ["hazardous_materials"]
    if h.rule_state == "CONFLICT":
        alts = ", ".join(f"{a['source']}={a['value']}" for a in h.alternatives)
        return RuleResult("R4", "Hazardous materials", "UNKNOWN",
                          f"Conflicting answers ({alts}); conflicts are evaluated before Yes/No values.", inputs, refs)
    v = h.effective_value
    if h.rule_state != "KNOWN" or not isinstance(v, bool):
        return RuleResult("R4", "Hazardous materials", "UNKNOWN",
                          f"Hazmat answer is {h.rule_state.lower()} (absent or ambiguous).", inputs, refs)
    if v is False:
        return RuleResult("R4", "Hazardous materials", "PASS",
                          "Explicit non-conflicting answer: No hazardous materials.", inputs, refs)
    return RuleResult("R4", "Hazardous materials", "FLAG",
                      "Explicit non-conflicting answer: Yes, hazardous materials.", inputs, refs)


def _r5(fields: dict[str, FieldValue]) -> RuleResult:
    l = fields["loss_runs_present"]
    inputs = {"loss_runs_present": l.effective_value}
    refs = ["loss_runs_present"]
    v = l.effective_value
    if v is True:
        return RuleResult("R5", "Loss runs attachment", "PASS",
                          "A readable loss runs / loss history report is attached.", inputs, refs)
    if v is False:
        return RuleResult("R5", "Loss runs attachment", "FLAG",
                          "No loss runs or loss history document is attached. "
                          "A broker promise to send them later does not count.", inputs, refs)
    return RuleResult("R5", "Loss runs attachment", "UNKNOWN",
                      "A potential loss runs attachment is present but unreadable or unclassifiable.", inputs, refs)


def evaluate_rules(fields: dict[str, FieldValue]) -> tuple[list[RuleResult], str]:
    results = [_r1(fields), _r2(fields), _r3(fields), _r4(fields), _r5(fields)]
    overall = "NEEDS_REVIEW" if any(r.status in ("FLAG", "UNKNOWN") for r in results) else "READY_FOR_REVIEW"
    return results, overall
