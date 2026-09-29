"""ROI math. Every figure produced here is a simulation from explicit assumptions."""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED = (
    "manual_minutes_per_request",
    "automated_review_minutes_per_request",
    "requests_per_month",
    "hourly_labor_cost_eur",
    "llm_cost_per_request_eur",
    "platform_cost_per_month_eur",
)

DISCLAIMER = "Simulation based on configurable assumptions."


def load_assumptions(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED if key not in raw]
    if missing:
        raise ValueError(f"Missing ROI assumptions: {', '.join(missing)}")
    assumptions = {key: float(raw[key]) for key in REQUIRED}
    for key, value in assumptions.items():
        if value < 0:
            raise ValueError(f"ROI assumption {key} cannot be negative")
    manual = assumptions["manual_minutes_per_request"]
    review = assumptions["automated_review_minutes_per_request"]
    if review > manual:
        raise ValueError("automated review time cannot exceed manual time")
    return assumptions


def calculate_roi(assumptions: dict, completed_requests: int = 0) -> dict:
    requests = assumptions["requests_per_month"]
    manual_minutes = requests * assumptions["manual_minutes_per_request"]
    review_minutes = requests * assumptions["automated_review_minutes_per_request"]
    saved_minutes = manual_minutes - review_minutes
    saved_hours = saved_minutes / 60
    gross_savings = saved_hours * assumptions["hourly_labor_cost_eur"]
    llm_cost = requests * assumptions["llm_cost_per_request_eur"]
    platform_cost = assumptions["platform_cost_per_month_eur"]
    monthly_cost = llm_cost + platform_cost
    net_benefit = gross_savings - monthly_cost
    automation_rate = None if manual_minutes == 0 else saved_minutes / manual_minutes
    roi_ratio = None if monthly_cost == 0 else net_benefit / monthly_cost
    return {
        "label": DISCLAIMER,
        "assumptions": assumptions,
        "manual_monthly_minutes": manual_minutes,
        "automated_monthly_human_minutes": review_minutes,
        "estimated_minutes_saved": saved_minutes,
        "estimated_hours_saved": round(saved_hours, 2),
        "automation_rate": automation_rate,
        "llm_cost_monthly_eur": round(llm_cost, 4),
        "estimated_monthly_cost_eur": round(monthly_cost, 4),
        "estimated_gross_savings_eur": round(gross_savings, 2),
        "estimated_net_benefit_eur": round(net_benefit, 2),
        "estimated_roi_ratio": None if roi_ratio is None else round(roi_ratio, 4),
        "completed_requests_observed": completed_requests,
        "note": (
            "The monthly figures use requests_per_month from the assumptions file. "
            "completed_requests_observed is the only number taken from the audit log. "
            "A high ROI ratio only means the assumed labor rate is larger than the assumed "
            "API cost. It is not a measured saving."
        ),
    }
