# ROI

Every euro amount in OpsPilot is a simulation. The API returns this sentence with the numbers:

> Simulation based on configurable assumptions.

The assumptions are in `config/roi_assumptions.json`. Change the file and call `GET /roi` or `python scripts/roi.py` again. Nothing in that file was measured on a real operations team.

## Assumptions shipped with the demo

| Name | Value | Meaning |
| --- | --- | --- |
| `manual_minutes_per_request` | 8 | Time a person spends doing the whole request by hand. |
| `automated_review_minutes_per_request` | 1.5 | Time the same person spends reading the proposal. |
| `requests_per_month` | 100 | Volume used for the monthly simulation. Not the audit count. |
| `hourly_labor_cost_eur` | 35 | Assumed fully loaded internal hour. |
| `llm_cost_per_request_eur` | 0.02 | Assumed model cost. Not an invoice. |
| `platform_cost_per_month_eur` | 0 | No hosting cost is included. |

`automated_review_minutes_per_request` cannot be larger than `manual_minutes_per_request`. Negative numbers are rejected.

## Formulas

```text
manual_monthly_minutes = requests_per_month * manual_minutes_per_request
automated_monthly_human_minutes = requests_per_month * automated_review_minutes_per_request
estimated_minutes_saved = manual_monthly_minutes - automated_monthly_human_minutes
estimated_hours_saved = estimated_minutes_saved / 60
automation_rate = estimated_minutes_saved / manual_monthly_minutes

llm_cost_monthly = requests_per_month * llm_cost_per_request_eur
estimated_monthly_cost = llm_cost_monthly + platform_cost_per_month_eur
estimated_gross_savings = estimated_hours_saved * hourly_labor_cost_eur
estimated_net_benefit = estimated_gross_savings - estimated_monthly_cost
estimated_roi_ratio = estimated_net_benefit / estimated_monthly_cost
```

With the shipped numbers, manual time is 800 minutes, review time is 150 minutes, and the time automation rate is 0.8125. The net benefit is large relative to a 2 euro model bill, so the ROI ratio is high. That ratio only compares the assumed wage with the assumed API price. It is not evidence that the workflow saved money.

If `estimated_monthly_cost` is zero, the ratio is null instead of a division by zero.

## What is measured

`GET /kpi` reads `operation_requests`.

- `requests_processed`, `successful_executions`, and `failed_executions` are counts.
- `approval_rate` is approved divided by approved plus rejected. It is null when nobody has decided.
- `rejection_rate` uses the same denominator.
- `automation_rate` on this endpoint is completed divided by all stored requests. This is not the time-saving rate above.
- Average processing time and average human review time are null until a request has both timestamps.
- `estimated_hours_saved.value` applies the minute assumptions only to requests whose status is `Completed`. The object is marked `simulation: true`. It is null when nothing has completed.
- `llm_cost_per_request_eur` repeats the assumption and is marked `simulation: true`.

`completed_requests_observed` on `/roi` is the completed count from the database. It is displayed beside the simulation and is not substituted for `requests_per_month`.
