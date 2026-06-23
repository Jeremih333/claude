from __future__ import annotations


def apply_timeline_plan(window: dict, plan: dict | None = None) -> dict:
    edited = dict(window or {})
    plan = dict(plan or {})
    edited["timeline_edit_applied"] = True
    edited["timeline_edit_plan"] = plan
    edited["window_duration"] = round(float(plan.get("duration", edited.get("duration", 0.0)) or edited.get("duration", 0.0)), 3)
    return edited

