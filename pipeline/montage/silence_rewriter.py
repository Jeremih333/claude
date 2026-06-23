from __future__ import annotations

from collections import Counter

try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except ImportError:
    np = None
    _NUMPY_AVAILABLE = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _pause_energy(pcm, sample_rate: int, gap_start: float, gap_end: float) -> float:
    """Return RMS energy (0-1) for the audio slice [gap_start, gap_end].

    Returns 0.0 when numpy is unavailable or pcm is None/empty.
    """
    if not _NUMPY_AVAILABLE or pcm is None:
        return 0.0
    if pcm.size == 0:
        return 0.0
    left = max(0, int(gap_start * sample_rate))
    right = min(len(pcm), int(gap_end * sample_rate))
    if right - left <= sample_rate * 0.1:
        return 0.0
    gap_pcm = pcm[left:right].astype(np.float32)
    return (
        float(np.sqrt(np.mean(np.square(gap_pcm)))) / 32768.0 if gap_pcm.size else 0.0
    )


# ---------------------------------------------------------------------------
# Public classification API
# ---------------------------------------------------------------------------


def classify_silence_pause(
    gap_dur: float,
    energy: float,
    prev_dur: float,
    next_dur: float,
    continuation_bonus: float,
    cfg: dict,
) -> dict:
    """Classify a silence gap into a semantic pause type.

    Returns a dict with keys:
        silence_type, silence_confidence, trim_allowed,
        max_allowed_silence, reason
    """
    event_threshold = float(cfg.get("min_non_silent_event_energy", 0.16))
    soft_threshold = float(cfg.get("pause_soft_keep_min_energy", 0.11))
    story_keep_energy = max(
        event_threshold,
        float(cfg.get("pause_story_keep_min_energy", event_threshold + 0.02)),
    )

    max_normal = 1.5
    max_emotional = 2.5
    max_comedic = 3.0

    low_energy = energy <= max(0.03, soft_threshold * 0.45)
    medium_energy = energy >= max(0.06, soft_threshold * 0.85)
    short_turn = min(prev_dur or 0.0, next_dur or 0.0) <= 0.65
    strong_turn = min(prev_dur or 0.0, next_dur or 0.0) >= 0.85
    asymmetric_turn = abs((prev_dur or 0.0) - (next_dur or 0.0)) >= 0.55

    # dead_air: long, silent, no continuation signal
    if gap_dur >= 2.0 and low_energy and continuation_bonus < 0.45:
        return {
            "silence_type": "dead_air",
            "silence_confidence": 0.92,
            "trim_allowed": True,
            "max_allowed_silence": max_normal,
            "reason": "low_energy_long_gap",
        }

    # reaction_pause: very short gap with conversational context
    if gap_dur <= 0.75:
        if continuation_bonus >= 0.72 and (short_turn or asymmetric_turn):
            return {
                "silence_type": "reaction_pause",
                "silence_confidence": 0.68,
                "trim_allowed": False,
                "max_allowed_silence": max_normal,
                "reason": "reaction_hold",
            }
        return {
            "silence_type": "unknown",
            "silence_confidence": 0.44,
            "trim_allowed": False,
            "max_allowed_silence": max_normal,
            "reason": "short_gap",
        }

    # dramatic pause bucket (gap_dur in (0.75, 3.0])
    if gap_dur <= max_comedic:
        # emotional_pause: strong continuation, strong turn, audible energy
        if continuation_bonus >= 0.84 and strong_turn and medium_energy:
            return {
                "silence_type": "emotional_pause",
                "silence_confidence": 0.74,
                "trim_allowed": False,
                "max_allowed_silence": max_emotional,
                "reason": "emotional_hold",
            }
        # comedic_pause: strong continuation, short turn
        if continuation_bonus >= 0.82 and short_turn:
            return {
                "silence_type": "comedic_pause",
                "silence_confidence": 0.72,
                "trim_allowed": False,
                "max_allowed_silence": max_comedic,
                "reason": "comic_timing",
            }
        # tension_pause: strong continuation, strong turn, up to 2.5 s
        if continuation_bonus >= 0.76 and strong_turn and gap_dur <= max_emotional:
            return {
                "silence_type": "tension_pause",
                "silence_confidence": 0.70,
                "trim_allowed": False,
                "max_allowed_silence": max_emotional,
                "reason": "tension_bridge",
            }
        # softer reaction
        if continuation_bonus >= 0.62 and medium_energy:
            return {
                "silence_type": "reaction_pause",
                "silence_confidence": 0.61,
                "trim_allowed": False,
                "max_allowed_silence": max_normal,
                "reason": "reaction_flow",
            }

    # long, uncertain low-energy gap
    if gap_dur > 2.0 and low_energy and continuation_bonus < 0.58:
        return {
            "silence_type": "unknown",
            "silence_confidence": 0.48,
            "trim_allowed": True,
            "max_allowed_silence": max_normal,
            "reason": "uncertain_low_energy",
        }

    # energetic hold — audible gap worth preserving
    if energy >= story_keep_energy and continuation_bonus >= 0.55:
        return {
            "silence_type": "emotional_pause",
            "silence_confidence": 0.58,
            "trim_allowed": False,
            "max_allowed_silence": max_emotional,
            "reason": "energetic_hold",
        }

    # fallback
    return {
        "silence_type": "unknown",
        "silence_confidence": 0.52 if low_energy else 0.57,
        "trim_allowed": (
            False
            if gap_dur <= max_comedic
            else (low_energy and continuation_bonus < 0.40)
        ),
        "max_allowed_silence": max_normal if gap_dur <= max_emotional else max_comedic,
        "reason": "uncertain",
    }


def pacing_score_from_pause_timeline(
    pause_timeline: list[dict],
    *,
    original_duration: float = 0.0,
    output_duration: float = 0.0,
    subtitle_signals: dict | None = None,
) -> float:
    """Return a pacing quality score in [0, 1] based on pause decisions and
    optional dialogue signals from subtitle analysis."""
    subtitle_signals = dict(subtitle_signals or {})
    original_duration = max(0.0, float(original_duration))
    output_duration = max(0.0, float(output_duration))

    meaningful_pause_kept_seconds = 0.0
    dead_air_cut_seconds = 0.0
    unknown_cut_seconds = 0.0
    trim_events = 0

    for item in pause_timeline or []:
        duration = max(0.0, float(item.get("duration", 0.0) or 0.0))
        decision = str(item.get("decision", ""))
        silence_type = str(item.get("silence_type", "unknown") or "unknown")
        if decision == "cut":
            trim_events += 1
            if silence_type == "dead_air":
                dead_air_cut_seconds += duration
            elif silence_type == "unknown":
                unknown_cut_seconds += duration
        elif decision in {"soft_keep", "keep_for_story"} and silence_type in {
            "comedic_pause",
            "emotional_pause",
            "reaction_pause",
            "tension_pause",
        }:
            meaningful_pause_kept_seconds += duration

    dialogue_flow = _clamp01(
        float(subtitle_signals.get("dialogue_exchange_score", 0.0) or 0.0)
    )
    interestingness = _clamp01(
        float(subtitle_signals.get("interestingness_score", 0.0) or 0.0)
    )
    hook_score = _clamp01(float(subtitle_signals.get("hook_score", 0.0) or 0.0))
    context_score = _clamp01(
        max(
            float(subtitle_signals.get("story_context_score", 0.0) or 0.0),
            float(subtitle_signals.get("closure_score", 0.0) or 0.0),
        )
    )
    subtitle_quality = _clamp01(
        float(subtitle_signals.get("subtitle_quality_score", 0.0) or 0.0)
    )

    trimmed_ratio = 0.0
    if original_duration > 0.0:
        trimmed_ratio = max(0.0, 1.0 - min(1.0, output_duration / original_duration))

    dead_air_ratio = dead_air_cut_seconds / max(1.0, original_duration)
    unknown_ratio = unknown_cut_seconds / max(1.0, original_duration)
    meaningful_keep_ratio = meaningful_pause_kept_seconds / max(1.0, original_duration)

    flow_score = (
        dialogue_flow * 0.26
        + interestingness * 0.18
        + hook_score * 0.12
        + context_score * 0.12
        + subtitle_quality * 0.08
        + min(1.0, meaningful_keep_ratio * 2.0) * 0.08
        + min(1.0, trimmed_ratio * 1.2) * 0.08
        + min(1.0, 1.0 - max(0.0, dead_air_ratio * 1.5 + unknown_ratio * 0.75)) * 0.08
    )
    penalty = min(
        0.40,
        dead_air_ratio * 1.7 + unknown_ratio * 0.75 + max(0, trim_events - 1) * 0.025,
    )
    pacing = _clamp01(flow_score - penalty + 0.16)
    return round(pacing, 4)


def build_pause_timeline(
    voiced: list[tuple[float, float]],
    pcm,  # numpy array or None
    sample_rate: int,
    cfg: dict,
    *,
    detected_silences: list[tuple[float, float]] | None = None,
    total_duration: float | None = None,
) -> list[dict]:
    """Build a classified pause timeline from voiced intervals and/or detected
    silence regions.

    Each entry in the returned list represents one gap between voiced segments
    and contains timing, energy, classification, and keep/cut decision fields.
    """
    keep_short_gaps = max(
        1.0,
        float(
            cfg.get(
                "story_pause_cut_threshold_seconds",
                cfg.get("keep_dialogue_gap_seconds", 1.0),
            )
        ),
    )
    max_story_gap = max(
        keep_short_gaps,
        float(
            cfg.get(
                "story_pause_keep_max_seconds",
                cfg.get("story_extension_max_pause_seconds", 1.15),
            )
        ),
    )
    story_gap_keep_limit = min(max_story_gap, keep_short_gaps + 0.15)

    event_threshold = float(cfg.get("min_non_silent_event_energy", 0.16))
    soft_threshold = float(cfg.get("pause_soft_keep_min_energy", 0.11))
    story_keep_energy = max(
        event_threshold,
        float(cfg.get("pause_story_keep_min_energy", event_threshold + 0.02)),
    )

    timeline: list[dict] = []

    # --- Build candidate gaps from voiced intervals ---
    candidate_gaps: list[tuple[float, float]] = []
    for index in range(1, len(voiced)):
        prev_start, prev_end = voiced[index - 1]
        next_start, _next_end = voiced[index]
        gap_start = float(prev_end)
        gap_end = float(next_start)
        if gap_end - gap_start > 0.0:
            candidate_gaps.append((gap_start, gap_end))

    # --- Merge in externally detected silence regions ---
    for start, end in list(detected_silences or []):
        start = max(0.0, float(start))
        end = max(start, float(end))
        if total_duration is not None:
            end = min(float(total_duration), end)
        if end - start >= max(0.45, keep_short_gaps * 0.8):
            candidate_gaps.append((start, end))

    if not candidate_gaps:
        return timeline

    # --- Merge overlapping / touching gaps ---
    merged_gaps: list[list[float]] = []
    for gap_start, gap_end in sorted(
        candidate_gaps, key=lambda item: (item[0], item[1])
    ):
        if not merged_gaps:
            merged_gaps.append([gap_start, gap_end])
            continue
        prev = merged_gaps[-1]
        if gap_start <= prev[1] + 0.08:
            prev[1] = max(prev[1], gap_end)
        else:
            merged_gaps.append([gap_start, gap_end])

    # --- Classify and decide each gap ---
    for gap_start, gap_end in merged_gaps:
        gap_dur = max(0.0, gap_end - gap_start)
        if gap_dur <= 0.0:
            continue

        energy = _pause_energy(pcm, sample_rate, gap_start, gap_end)

        # Find adjacent voiced turns
        prev_turn = None
        next_turn = None
        for start, end in voiced:
            if float(end) <= gap_start + 0.02:
                prev_turn = (start, end)
            elif float(start) >= gap_end - 0.02:
                next_turn = (start, end)
                break

        prev_dur = max(0.0, float(prev_turn[1] - prev_turn[0])) if prev_turn else 0.0
        next_dur = max(0.0, float(next_turn[1] - next_turn[0])) if next_turn else 0.0
        continuation_bonus = min(1.0, (min(prev_dur, next_dur) / 1.15))

        event_sensitive = energy >= event_threshold
        soft_context = energy >= soft_threshold or continuation_bonus >= 0.62
        strong_story_context = (
            energy >= story_keep_energy
            or (energy >= soft_threshold and continuation_bonus >= 0.78)
            or continuation_bonus >= 0.92
        )

        classification = classify_silence_pause(
            gap_dur, energy, prev_dur, next_dur, continuation_bonus, cfg
        )
        silence_type = str(classification.get("silence_type", "unknown"))
        silence_confidence = round(
            float(classification.get("silence_confidence", 0.0) or 0.0), 4
        )
        trim_allowed = bool(classification.get("trim_allowed", False))

        if gap_dur <= keep_short_gaps:
            decision = "soft_keep"
            reason = "short_gap"
        elif silence_type == "dead_air" or (
            silence_type == "unknown" and silence_confidence < 0.50 and trim_allowed
        ):
            decision = "cut"
            reason = classification.get(
                "reason",
                "dead_air" if silence_type == "dead_air" else "unknown_low_confidence",
            )
        elif gap_dur <= story_gap_keep_limit and (
            strong_story_context
            or soft_context
            or event_sensitive
            or continuation_bonus >= 0.50
            or not trim_allowed
        ):
            decision = "keep_for_story"
            if silence_type == "comedic_pause":
                reason = "comedic_timing"
            elif silence_type == "emotional_pause":
                reason = "emotional_hold"
            elif silence_type == "reaction_pause":
                reason = "reaction_hold"
            elif silence_type == "tension_pause":
                reason = "tension_hold"
            elif energy >= story_keep_energy:
                reason = "event_energy"
            elif continuation_bonus >= 0.92:
                reason = "continuation_bonus"
            elif soft_context:
                reason = "soft_context"
            else:
                reason = "story_gap"
        else:
            if (
                silence_type == "unknown"
                and silence_confidence >= 0.50
                and not trim_allowed
            ):
                decision = "keep_for_story"
                reason = "uncertain_preserve"
            else:
                decision = "cut"
                reason = (
                    "long_silence" if gap_dur > max_story_gap else "over_2s_silence"
                )

        timeline.append(
            {
                "start": round(gap_start, 3),
                "end": round(gap_end, 3),
                "duration": round(gap_dur, 3),
                "energy": round(energy, 4),
                "continuation_bonus": round(continuation_bonus, 4),
                "silence_type": silence_type,
                "silence_confidence": silence_confidence,
                "max_allowed_silence": round(
                    float(classification.get("max_allowed_silence", max_story_gap)), 3
                ),
                "trim_allowed": trim_allowed,
                "decision": decision,
                "reason": reason,
            }
        )

    return timeline


# ---------------------------------------------------------------------------
# Plan / stats helpers (kept from original module)
# ---------------------------------------------------------------------------


def build_silence_rewrite_plan(pause_timeline: list[dict] | None) -> dict:
    timeline = list(pause_timeline or [])
    cut = [item for item in timeline if str(item.get("decision", "")) == "cut"]
    keep = [item for item in timeline if str(item.get("decision", "")) != "cut"]
    return {
        "pause_cut_count": len(cut),
        "pause_keep_count": len(keep),
        "trimmed_silence_seconds": round(
            sum(float(item.get("duration", 0.0) or 0.0) for item in cut), 3
        ),
        "silence_trim_events": [
            {
                "start": round(float(item.get("start", 0.0) or 0.0), 3),
                "end": round(float(item.get("end", 0.0) or 0.0), 3),
                "duration": round(float(item.get("duration", 0.0) or 0.0), 3),
                "silence_type": str(item.get("silence_type", "unknown") or "unknown"),
                "reason": str(item.get("reason", "")),
            }
            for item in cut
        ],
    }


def pause_timeline_stats(timeline: list[dict]) -> dict:
    cut = [item for item in timeline if str(item.get("decision", "")) == "cut"]
    soft_keep = [
        item for item in timeline if str(item.get("decision", "")) == "soft_keep"
    ]
    story_keep = [
        item for item in timeline if str(item.get("decision", "")) == "keep_for_story"
    ]
    silence_type_counts = Counter(
        str(item.get("silence_type", "unknown") or "unknown") for item in timeline
    )
    return {
        "pause_cut_count": len(cut),
        "pause_soft_keep_count": len(soft_keep),
        "pause_story_keep_count": len(story_keep),
        "trimmed_silence_seconds": round(
            sum(float(item.get("duration", 0.0) or 0.0) for item in cut), 3
        ),
        "story_sensitive_pause_kept_seconds_total": round(
            sum(float(item.get("duration", 0.0) or 0.0) for item in story_keep), 3
        ),
        "silence_type_counts": dict(silence_type_counts),
        "silence_trim_events": [
            {
                "start": round(float(item.get("start", 0.0) or 0.0), 3),
                "end": round(float(item.get("end", 0.0) or 0.0), 3),
                "duration": round(float(item.get("duration", 0.0) or 0.0), 3),
                "silence_type": str(item.get("silence_type", "unknown") or "unknown"),
                "reason": str(item.get("reason", "")),
            }
            for item in cut
        ],
    }
