"""Shared data model for the Knight Insurance 24-hour exercise.

Field states and rule statuses follow field_specification.json / rules.json exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Evidence:
    source_file: str
    locator: str            # e.g. "email body", "PDF page 1", "CSV data rows 1-3 (header excluded)"
    excerpt_or_observation: str

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "locator": self.locator,
            "excerpt_or_observation": self.excerpt_or_observation,
        }


@dataclass
class FieldValue:
    name: str
    value: Any                          # normalized value or None
    state: str                          # KNOWN | MISSING | CONFLICT | INVALID
    evidence: list[Evidence] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)  # conflicting per-source values
    notes: list[str] = field(default_factory=list)
    override: Optional[dict] = None      # {"value": ..., "by": "human", ...} after a correction

    @property
    def effective_value(self) -> Any:
        if self.override is not None:
            return self.override["value"]
        return self.value

    @property
    def rule_state(self) -> str:
        """State the rule engine sees: a human override resolves the effective
        value, while the extracted state/conflict notes stay retained on the field."""
        if self.override is not None:
            return "KNOWN"
        return self.state

    @classmethod
    def from_dict(cls, d: dict) -> "FieldValue":
        """Rebuild a field from its stored to_dict() form (used when reloading a saved run)."""
        return cls(
            name=d["name"], value=d["value"], state=d["state"],
            evidence=[Evidence(**e) for e in d["evidence"]],
            alternatives=d["alternatives"], notes=d["notes"], override=d["override"],
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "effective_value": self.effective_value,
            "state": self.state,
            "evidence": [e.to_dict() for e in self.evidence],
            "alternatives": self.alternatives,
            "notes": self.notes,
            "override": self.override,
        }


@dataclass
class RuleResult:
    rule_id: str
    name: str
    status: str                 # PASS | FLAG | UNKNOWN
    reason: str
    inputs_used: dict           # field name -> effective value at evaluation time
    evidence_refs: list[str]    # human-readable pointers into the evidence

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "inputs_used": self.inputs_used,
            "evidence_refs": self.evidence_refs,
        }


@dataclass
class PacketResult:
    packet_id: str
    fields: dict[str, FieldValue]
    rules: list[RuleResult]
    overall_status: str         # READY_FOR_REVIEW | NEEDS_REVIEW
    summary: Optional[str] = None
    summary_origin: Optional[str] = None   # "llm" | "deterministic-fallback"
    draft_email: Optional[str] = None
    draft_origin: Optional[str] = None
    llm: dict = field(default_factory=dict)  # model name + whether the last extraction reached it
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "packet_id": self.packet_id,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "rules": [r.to_dict() for r in self.rules],
            "overall_status": self.overall_status,
            "disclaimer": (
                "READY_FOR_REVIEW / NEEDS_REVIEW indicate readiness for human review only. "
                "They do not approve, decline, quote, price, or bind coverage."
            ),
            "summary": self.summary,
            "summary_origin": self.summary_origin,
            "draft_email": self.draft_email,
            "draft_origin": self.draft_origin,
            "llm": self.llm,
            "warnings": self.warnings,
        }
