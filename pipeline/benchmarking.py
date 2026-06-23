from __future__ import annotations

from collections import Counter
from itertools import combinations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LABELS = [
    "excellent",
    "good",
    "publishable",
    "bad",
    "boring",
    "confusing",
    "wrong_focus",
    "subtitle_bad",
    "late_hook",
    "unclear_context",
]

ROOT_CAUSE_TAGS = [
    "late_hook",
    "late_entry",
    "weak_hook",
    "speaker_unclear",
    "wrong_face_focus",
    "subtitle_overload",
    "bad_pacing",
    "missing_context",
    "weak_payoff",
    "crop_jitter",
    "too_slow",
    "too_fast",
    "confusing_dialogue",
    "bad_title",
]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def build_candidate_summary(meta: dict | None) -> dict:
    meta = dict(meta or {})
    story_summary = dict(meta.get("story_summary") or {})
    hook_score = max(
        _float(meta.get("hook_strength", 0.0)),
        _float(meta.get("hook_score", 0.0)),
        _float(meta.get("first_second_hook_score", 0.0)),
        _float(meta.get("sound_off_hook_score", 0.0)),
        _float(meta.get("premise_signal_score", 0.0)),
    )
    retention_soft_score = _float(meta.get("retention_soft_score", meta.get("viral_soft_score", 0.0)))
    subtitle_quality = _float(meta.get("subtitle_quality_score", 0.0))
    pacing_score = _float(meta.get("pacing_score", 0.0))
    face_focus_rate = max(
        _float(meta.get("speaker_centered_rate", 0.0)),
        _float(meta.get("subject_visibility_ratio", 0.0)),
    )
    speaker_confidence_score = _float(meta.get("speaker_confidence_score", 0.0))
    visual_conversation_score = _float(meta.get("visual_conversation_score", 0.0))
    publishable = str(meta.get("quality_governor_decision", "")).lower() == "accept" and not bool(meta.get("needs_review", False))
    if not publishable:
        publishable = (
            _float(meta.get("recommendation_readiness_score", 0.0)) >= 0.64
            and _float(meta.get("packaging_quality_score", 0.0)) >= 0.52
            and subtitle_quality >= 0.55
        )
    return {
        "hook_score": round(hook_score, 4),
        "retention_soft_score": round(retention_soft_score, 4),
        "subtitle_quality": round(subtitle_quality, 4),
        "pacing_score": round(pacing_score, 4),
        "face_focus_rate": round(face_focus_rate, 4),
        "speaker_confidence_score": round(speaker_confidence_score, 4),
        "visual_conversation_score": round(visual_conversation_score, 4),
        "publishable": bool(publishable),
        "watchability_score": round(_float(meta.get("watchability_score", 0.0)), 4),
        "recommendation_readiness_score": round(_float(meta.get("recommendation_readiness_score", 0.0)), 4),
        "packaging_quality_score": round(_float(meta.get("packaging_quality_score", 0.0)), 4),
        "first_second_hook_score": round(_float(meta.get("first_second_hook_score", 0.0)), 4),
        "visible_stakes_score": round(_float(meta.get("visible_stakes_score", 0.0)), 4),
        "first_frame_clarity_score": round(_float(meta.get("first_frame_clarity_score", 0.0)), 4),
        "speaker_centered_rate": round(_float(meta.get("speaker_centered_rate", 0.0)), 4),
        "subject_visibility_ratio": round(_float(meta.get("subject_visibility_ratio", 0.0)), 4),
        "subtitle_quality_score": round(subtitle_quality, 4),
        "trimmed_silence_seconds": round(_float(meta.get("trimmed_silence_seconds", 0.0)), 4),
        "silence_trim_events_count": int(len(list(meta.get("silence_trim_events") or []))),
        "speaker_confidence_score": round(speaker_confidence_score, 4),
        "visual_conversation_score": round(visual_conversation_score, 4),
        "story_thread_id": meta.get("story_thread_id") or story_summary.get("conversation_id"),
        "conversation_id": meta.get("conversation_id") or story_summary.get("conversation_id"),
        "story_arc_shape": meta.get("story_arc_shape") or story_summary.get("story_arc_shape"),
        "story_coherence_score": round(_float(meta.get("story_coherence_score", story_summary.get("story_coherence_score", 0.0))), 4),
        "story_completion_score": round(_float(meta.get("story_completion_score", story_summary.get("story_completion_score", 0.0))), 4),
        "context_completeness_score": round(_float(meta.get("context_completeness_score", story_summary.get("context_completeness_score", 0.0))), 4),
        "hook_type": meta.get("hook_type") or story_summary.get("hook_type"),
        "payoff_type": meta.get("payoff_type") or story_summary.get("payoff_type"),
        "topic_shift_events": int(_float(meta.get("topic_shift_events", 0), 0.0)),
        "quality_governor_decision": meta.get("quality_governor_decision"),
    }


def infer_failure_reasons(meta: dict | None) -> list[str]:
    meta = dict(meta or {})
    tags: list[str] = []
    hook_score = max(
        _float(meta.get("hook_strength", 0.0)),
        _float(meta.get("hook_score", 0.0)),
        _float(meta.get("first_second_hook_score", 0.0)),
        _float(meta.get("sound_off_hook_score", 0.0)),
        _float(meta.get("premise_signal_score", 0.0)),
    )
    subtitle_quality = _float(meta.get("subtitle_quality_score", 0.0))
    pacing_score = _float(meta.get("pacing_score", 0.0))
    speaker_confidence_score = _float(meta.get("speaker_confidence_score", 0.0))
    visual_conversation_score = _float(meta.get("visual_conversation_score", 0.0))
    face_focus_rate = max(_float(meta.get("speaker_centered_rate", 0.0)), _float(meta.get("subject_visibility_ratio", 0.0)))
    retention_soft_score = _float(meta.get("retention_soft_score", meta.get("viral_soft_score", 0.0)))
    if _float(meta.get("cold_open_dead_time_penalty", 0.0)) > 0.18 or hook_score < 0.50:
        tags.append("late_hook")
    if _float(meta.get("visible_stakes_score", 0.0)) < 0.45 and hook_score < 0.58:
        tags.append("weak_hook")
    if face_focus_rate < 0.55 or _float(meta.get("visual_subject_score", 0.0)) < 0.40:
        tags.append("wrong_face_focus")
    if _float(meta.get("subject_visibility_ratio", 0.0)) < 0.48 and _float(meta.get("speaker_centered_rate", 0.0)) < 0.50:
        tags.append("speaker_unclear")
    if subtitle_quality < 0.55 or int(meta.get("subtitle_overlap_outputs", 0) or 0) > 0 or int(meta.get("subtitle_blink_outputs", 0) or 0) > 0:
        tags.append("subtitle_overload")
    if _float(meta.get("dialogue_dependency_penalty", 0.0)) > 0.35:
        tags.append("missing_context")
    if _float(meta.get("story_completeness_score", 0.0)) < 0.48 and _float(meta.get("payoff_strength", 0.0)) < 0.50:
        tags.append("weak_payoff")
    if _float(meta.get("packaging_quality_score", 0.0)) < 0.50 or _float(meta.get("title_quality_score", 0.0)) < 0.55:
        tags.append("bad_title")
    if _float(meta.get("speaker_center_offset_avg", 0.0)) > 0.18 or _float(meta.get("speaker_center_offset_p95", 0.0)) > 0.22:
        tags.append("crop_jitter")
    if _float(meta.get("final_duration", 0.0)) > 42.0 and retention_soft_score < 0.58:
        tags.append("bad_pacing")
    if _float(meta.get("story_interest_score", 0.0)) < 0.52 and _float(meta.get("story_context_score", 0.0)) < 0.40:
        tags.append("confusing_dialogue")
    if _float(meta.get("story_coherence_score", 1.0)) < 0.58:
        tags.append("confusing_dialogue")
    if _float(meta.get("story_completion_score", 0.0)) < 0.56 or str(meta.get("payoff_type", "")).lower() == "unfinished":
        tags.append("weak_payoff")
    if int(_float(meta.get("topic_shift_events", 0), 0.0)) > 0:
        tags.append("confusing_dialogue")
    if visual_conversation_score < 0.48 or speaker_confidence_score < 0.42:
        tags.append("wrong_face_focus")
    if pacing_score < 0.48 or _float(meta.get("trimmed_silence_seconds", 0.0)) > 4.0 and retention_soft_score < 0.62:
        tags.append("bad_pacing")
    if _float(meta.get("final_duration", 0.0)) < 35.0:
        tags.append("too_fast")
    if _float(meta.get("final_duration", 0.0)) > 60.0 and retention_soft_score < 0.50:
        tags.append("too_slow")
    if bool(meta.get("reframe_scene_interest_fallback_used", False)) and _float(meta.get("subject_visibility_ratio", 0.0)) < 0.30:
        tags.append("late_entry")
    if bool(meta.get("subject_person_fallback_used", False)) and _float(meta.get("visual_subject_score", 0.0)) < 0.40:
        tags.append("speaker_unclear")
    if bool(meta.get("face_preserving_fallback_used", False)) and _float(meta.get("face_edge_clip_rate", 0.0)) > 0.12:
        tags.append("crop_jitter")
    if str(meta.get("quality_governor_decision", "")).lower() == "reject_visual":
        tags.append("wrong_face_focus")
    if str(meta.get("quality_governor_decision", "")).lower() == "reject_story":
        if hook_score < 0.50:
            tags.append("late_hook")
        if _float(meta.get("story_completeness_score", 0.0)) < 0.50:
            tags.append("weak_payoff")
        if _float(meta.get("story_context_score", 0.0)) < 0.40:
            tags.append("missing_context")
    if bool(meta.get("rejected_for_missing_payoff", False)):
        tags.append("weak_payoff")
    if bool(meta.get("rejected_for_topic_jump", False)):
        tags.append("confusing_dialogue")
    if bool(meta.get("rejected_for_confusing_story", False)):
        tags.append("confusing_dialogue")
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag not in ROOT_CAUSE_TAGS or tag in seen:
            continue
        seen.add(tag)
        deduped.append(tag)
    return deduped


def build_candidate_manifest(
    candidate_id: str,
    meta: dict | None,
    *,
    report: dict | None = None,
    series_name: str | None = None,
    episode_name: str | None = None,
    paths: dict | None = None,
    human_labels: list[str] | None = None,
    failure_reason: list[str] | None = None,
    created_at: str | None = None,
    source_dir: str | Path | None = None,
) -> dict:
    meta = dict(meta or {})
    report = dict(report or {})
    summary = build_candidate_summary(meta)
    inferred_failure = infer_failure_reasons(meta)
    human_labels = list(human_labels or meta.get("human_labels") or [])
    failure_reason = list(failure_reason or meta.get("failure_reason") or inferred_failure)
    candidate_id = str(candidate_id)
    source_file = str(meta.get("source_file") or report.get("source_file") or "")
    source_path = Path(source_file) if source_file else None
    if not series_name:
        series_name = meta.get("series_name") or report.get("series_name")
    if not series_name and source_path is not None:
        series_name = source_path.parent.name or source_path.stem
    if not episode_name:
        episode_name = meta.get("episode_name") or report.get("episode_name")
    if not episode_name and source_path is not None:
        episode_name = source_path.stem
    if not created_at:
        created_at = meta.get("created_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "candidate_id": candidate_id,
        "created_at": created_at,
        "pipeline_version": meta.get("pipeline_version") or report.get("pipeline_version"),
        "config_hash": meta.get("config_hash") or report.get("config_hash"),
        "git_commit": meta.get("git_commit") or report.get("git_commit"),
        "series_name": series_name or "",
        "episode_name": episode_name or "",
        "duration": round(_float(meta.get("final_duration", meta.get("duration", 0.0))), 3),
        "human_labels": human_labels,
        "failure_reason": failure_reason,
        "paths": paths or {},
        "summary": summary,
        "story_summary": dict(meta.get("story_summary") or {}),
        "story_chain": dict(meta.get("story_chain") or {}),
        "story_fragments": list(meta.get("story_fragments") or []),
        "story_summary_path": meta.get("story_summary_path"),
        "story_chain_path": meta.get("story_chain_path"),
        "story_fragments_path": meta.get("story_fragments_path"),
        "story_window_assembly_used": bool(meta.get("story_window_assembly_used", False)),
        "story_window_plan": meta.get("story_window_plan"),
        "story_window_segments": meta.get("story_window_segments"),
        "story_thread_id": meta.get("story_thread_id"),
        "conversation_id": meta.get("conversation_id"),
        "story_arc_shape": meta.get("story_arc_shape"),
        "story_coherence_score": _float(meta.get("story_coherence_score", 0.0)),
        "story_completion_score": _float(meta.get("story_completion_score", 0.0)),
        "context_completeness_score": _float(meta.get("context_completeness_score", 0.0)),
        "hook_type": meta.get("hook_type"),
        "payoff_type": meta.get("payoff_type"),
        "topic_shift_events": int(_float(meta.get("topic_shift_events", 0), 0.0)),
        "rejected_for_missing_payoff": bool(meta.get("rejected_for_missing_payoff", False)),
        "rejected_for_topic_jump": bool(meta.get("rejected_for_topic_jump", False)),
        "rejected_for_confusing_story": bool(meta.get("rejected_for_confusing_story", False)),
        "coherence_merge_reason": meta.get("coherence_merge_reason"),
        "coherence_rejection_reason": meta.get("coherence_rejection_reason"),
        "clarity_score": _float(meta.get("clarity_score", meta.get("story_clarity_score", summary.get("hook_score", 0.0)))),
        "duration_penalty": _float(meta.get("duration_penalty", 0.0)),
        "window_expansion_meta": meta.get("window_expansion_meta"),
        "merge_reason": meta.get("merge_reason") or meta.get("stitch_reason"),
        "pacing_score": _float(meta.get("pacing_score", summary.get("pacing_score", 0.0))),
        "trimmed_silence_seconds": _float(meta.get("trimmed_silence_seconds", summary.get("trimmed_silence_seconds", 0.0))),
        "silence_trim_events": list(meta.get("silence_trim_events") or []),
        "speaker_switches": int(meta.get("speaker_switches", 0) or 0),
        "reframe_fallback_count": int(meta.get("reframe_fallback_count", 0) or 0),
        "speaker_confidence_score": _float(meta.get("speaker_confidence_score", summary.get("speaker_confidence_score", 0.0))),
        "visual_conversation_score": _float(meta.get("visual_conversation_score", summary.get("visual_conversation_score", 0.0))),
        "source_file": source_file or None,
        "source_dir": str(source_dir) if source_dir is not None else None,
    }
    return manifest


def aggregate_session_metrics(records: list[dict]) -> dict:
    total = len(records)
    if not total:
        return {
            "count": 0,
            "publishable_rate": 0.0,
            "average_hook_score": 0.0,
            "average_retention_soft_score": 0.0,
            "average_story_completion_score": 0.0,
            "average_context_completeness_score": 0.0,
            "subtitle_quality": 0.0,
            "pacing_score": 0.0,
            "face_focus_rate": 0.0,
            "speaker_confidence_score": 0.0,
            "visual_conversation_score": 0.0,
            "runtime": None,
            "stability": 0.0,
            "fallback_frequency": 0.0,
            "rejection_reasons": {},
            "root_cause_tags": {},
        }
    publishable = sum(1 for record in records if bool((record.get("summary") or {}).get("publishable", False)))
    avg = lambda key: round(sum(_float((record.get("summary") or {}).get(key, 0.0)) for record in records) / total, 4)
    rejection = Counter()
    root_causes = Counter()
    runtime_values = []
    fallback_flags = 0
    stability_hits = 0
    for record in records:
        reasons = list(record.get("failure_reason") or [])
        rejection.update(reasons)
        root_causes.update(reasons)
        summary = record.get("summary") or {}
        if bool(summary.get("publishable", False)):
            stability_hits += 1
        if any(
            bool(record.get(key))
            for key in (
                "ranking_fallback_used",
                "ranking_fast_fallback_used",
                "watchdog_fallback_used",
                "semantic_preview_fallback_used",
            )
        ):
            fallback_flags += 1
        runtime = record.get("runtime_seconds")
        if runtime is not None:
            runtime_values.append(_float(runtime))
    return {
        "count": total,
        "publishable_rate": round(publishable / total, 4),
        "average_hook_score": avg("hook_score"),
        "average_retention_soft_score": avg("retention_soft_score"),
        "average_story_completion_score": avg("story_completion_score"),
        "average_context_completeness_score": avg("context_completeness_score"),
        "subtitle_quality": avg("subtitle_quality"),
        "pacing_score": avg("pacing_score"),
        "face_focus_rate": avg("face_focus_rate"),
        "speaker_confidence_score": avg("speaker_confidence_score"),
        "visual_conversation_score": avg("visual_conversation_score"),
        "runtime": round(sum(runtime_values) / len(runtime_values), 3) if runtime_values else None,
        "stability": round(stability_hits / total, 4),
        "fallback_frequency": round(fallback_flags / total, 4),
        "rejection_reasons": dict(rejection),
        "root_cause_tags": dict(root_causes),
    }


def count_label_rates(records: list[dict]) -> dict:
    total = len(records)
    if not total:
        return {label: 0.0 for label in LABELS}
    counts = Counter()
    for record in records:
        counts.update(record.get("human_labels") or [])
    return {label: round(counts.get(label, 0) / total, 4) for label in LABELS}


def summarize_failure_clusters(records: list[dict], top_n: int = 10) -> dict:
    root_counts = Counter()
    label_counts = Counter()
    co_occurrence = Counter()
    for record in records:
        reasons = sorted({str(item) for item in (record.get("failure_reason") or []) if str(item)})
        labels = [str(item) for item in (record.get("human_labels") or []) if str(item)]
        root_counts.update(reasons)
        label_counts.update(labels)
        for left, right in combinations(reasons, 2):
            co_occurrence[f"{left}::{right}"] += 1
    return {
        "root_cause_counts": dict(root_counts),
        "top_root_causes": root_counts.most_common(top_n),
        "label_counts": dict(label_counts),
        "top_labels": label_counts.most_common(top_n),
        "failure_cooccurrence": co_occurrence.most_common(top_n),
    }


def assess_data_sufficiency(reviewed_count: int) -> dict:
    reviewed_count = int(reviewed_count or 0)
    if reviewed_count < 100:
        status = "weak"
        warning = "insufficient_data_for_confident_iteration"
    elif reviewed_count < 250:
        status = "minimum"
        warning = "usable_but_still_small"
    elif reviewed_count < 500:
        status = "recommended"
        warning = "good_enough_for_targeted_changes"
    else:
        status = "strong"
        warning = None
    return {
        "reviewed_count": reviewed_count,
        "status": status,
        "minimum_threshold": 100,
        "recommended_threshold": 250,
        "strong_threshold": 500,
        "warning": warning,
        "minimum_met": reviewed_count >= 100,
        "recommended_met": reviewed_count >= 250,
        "strong_met": reviewed_count >= 500,
    }


def score_distribution(records: list[dict], key: str, buckets: int = 5) -> dict:
    buckets = max(2, int(buckets))
    counts = [0 for _ in range(buckets)]
    values: list[float] = []
    for record in records:
        value = record.get("summary", {}).get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except Exception:
            continue
        values.append(number)
        index = min(buckets - 1, max(0, int(number * buckets)))
        counts[index] += 1
    return {
        "key": key,
        "buckets": buckets,
        "counts": counts,
        "min": round(min(values), 4) if values else None,
        "max": round(max(values), 4) if values else None,
        "avg": round(sum(values) / len(values), 4) if values else None,
        "sample_size": len(values),
    }


def summarize_session_trends(sessions: list[dict]) -> dict:
    ordered = []
    for session in sessions:
        created_at = session.get("created_at")
        aggregate = session.get("aggregate") or {}
        if not created_at:
            continue
        ordered.append((created_at, aggregate))
    ordered.sort(key=lambda item: item[0])
    if len(ordered) < 3:
        return {
            "available": False,
            "warning": "not_enough_sessions_for_trend_analysis",
            "recent_minus_early": {},
        }
    third = max(1, len(ordered) // 3)
    early = [item[1] for item in ordered[:third]]
    recent = [item[1] for item in ordered[-third:]]

    def avg(items: list[dict], key: str) -> float | None:
        values = []
        for item in items:
            try:
                values.append(float(item.get(key)))
            except Exception:
                continue
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    keys = (
        "publishable_candidates",
        "runtime_seconds",
        "ranking_fallback_used",
        "ranking_fast_fallback_used",
        "semantic_preview_fallback_used",
        "watchdog_fallback_used",
    )
    deltas = {}
    for key in keys:
        before = avg(early, key)
        after = avg(recent, key)
        if before is None or after is None:
            continue
        deltas[key] = round(after - before, 4)
    return {
        "available": True,
        "session_count": len(ordered),
        "recent_minus_early": deltas,
        "sample_window": third,
    }


def build_baseline_report(records: list[dict], sessions: list[dict] | None = None) -> dict:
    sessions = list(sessions or [])
    metrics = aggregate_session_metrics(records)
    labels = count_label_rates(records)
    clusters = summarize_failure_clusters(records)
    sufficiency = assess_data_sufficiency(len(records))
    distributions = {
        key: score_distribution(records, key)
        for key in ("retention_soft_score", "subtitle_quality", "pacing_score", "speaker_confidence_score", "visual_conversation_score", "face_focus_rate", "hook_score", "story_completion_score", "context_completeness_score")
    }
    accepted_outputs_by_arc_shape = Counter()
    for record in records:
        summary = record.get("summary") or {}
        if bool(summary.get("publishable", False)):
            arc_shape = str(summary.get("story_arc_shape") or record.get("story_arc_shape") or "unknown")
            accepted_outputs_by_arc_shape[arc_shape] += 1
    report = {
        "count": len(records),
        "metrics": metrics,
        "label_rates": labels,
        "failure_clusters": clusters,
        "score_distributions": distributions,
        "accepted_outputs_by_arc_shape": dict(accepted_outputs_by_arc_shape),
        "data_sufficiency": sufficiency,
        "confidence_warnings": [],
    }
    if len(records) < 20:
        report["confidence_warnings"].append("small_sample_size")
    if len(records) < 100:
        report["confidence_warnings"].append("below_minimum_data_gate")
    if sessions:
        report["trend_analysis"] = summarize_session_trends(sessions)
    else:
        report["trend_analysis"] = {"available": False, "warning": "no_sessions_provided", "recent_minus_early": {}}
    return report
