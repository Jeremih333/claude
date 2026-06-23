from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_metadata(metadata: Any) -> dict:
    if isinstance(metadata, dict):
        return dict(metadata)
    if not metadata:
        return {}
    try:
        path = Path(str(metadata))
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _score_candidate(meta: dict) -> float:
    return round(
        float(meta.get("recommendation_readiness_score", 0.0) or 0.0) * 0.34
        + float(meta.get("watchability_score", 0.0) or 0.0) * 0.20
        + float(meta.get("packaging_quality_score", 0.0) or 0.0) * 0.12
        + float(meta.get("first_second_hook_score", 0.0) or 0.0) * 0.12
        + float(meta.get("story_interest_score", 0.0) or 0.0) * 0.10
        + float(meta.get("visible_stakes_score", 0.0) or 0.0) * 0.06
        + float(meta.get("first_frame_clarity_score", 0.0) or 0.0) * 0.04
        - float(meta.get("cold_open_dead_time_penalty", 0.0) or 0.0) * 0.08,
        4,
    )


def rank_assisted_candidates(generated_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for index, output in enumerate(generated_outputs, start=1):
        meta = _load_metadata(output.get("metadata"))
        ranked.append(
            {
                "rank": index,
                "video": output.get("video"),
                "metadata": output.get("metadata"),
                "generated_title": meta.get("generated_title"),
                "story_unit_type": meta.get("story_unit_type"),
                "score": _score_candidate(meta),
                "recommendation_readiness_score": float(meta.get("recommendation_readiness_score", 0.0) or 0.0),
                "watchability_score": float(meta.get("watchability_score", 0.0) or 0.0),
                "packaging_quality_score": float(meta.get("packaging_quality_score", 0.0) or 0.0),
                "first_second_hook_score": float(meta.get("first_second_hook_score", 0.0) or 0.0),
                "story_interest_score": float(meta.get("story_interest_score", 0.0) or 0.0),
                "visible_stakes_score": float(meta.get("visible_stakes_score", 0.0) or 0.0),
                "first_frame_clarity_score": float(meta.get("first_frame_clarity_score", 0.0) or 0.0),
                "cold_open_dead_time_penalty": float(meta.get("cold_open_dead_time_penalty", 0.0) or 0.0),
                "subtitle_quality_score": float(meta.get("subtitle_quality_score", 0.0) or 0.0),
                "premise_summary": meta.get("premise_summary"),
                "selected_opening_reason": meta.get("selected_opening_reason"),
                "source_file": meta.get("source_file"),
            }
        )
    ranked.sort(
        key=lambda item: (
            item["score"],
            item["recommendation_readiness_score"],
            item["watchability_score"],
            item["packaging_quality_score"],
            item["first_second_hook_score"],
            item["story_interest_score"],
            item["visible_stakes_score"],
            item["first_frame_clarity_score"],
        ),
        reverse=True,
    )
    for position, item in enumerate(ranked, start=1):
        item["rank"] = position
    return ranked


def feedback_log_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir or Path.cwd()).resolve()
    return root / "logs" / "assisted_ranking_feedback.jsonl"


def append_feedback_event(event: dict[str, Any], base_dir: str | Path | None = None) -> Path:
    path = feedback_log_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("timestamp_utc", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return path
