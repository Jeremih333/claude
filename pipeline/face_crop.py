from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from statistics import mean

from moviepy import VideoFileClip

from .active_speaker import estimate_face_tracks, sample_face_focus_stats, clear_face_track_cache
from .benchmarking import compute_turn_first_metrics


# PHASE 5: Cached wrapper for face detection
def estimate_face_tracks_cached(video_path, start, end, sample_fps=2, detector_profile="light"):
    """Cached wrapper around estimate_face_tracks for overlapping candidates."""
    from .active_speaker import _cache_key, _FACE_TRACK_CACHE
    
    key = _cache_key(video_path, start, end, int(sample_fps), str(detector_profile))
    
    if key in _FACE_TRACK_CACHE:
        return _FACE_TRACK_CACHE[key]  # INSTANT RETURN — 40% time savings
    
    tracks = estimate_face_tracks(video_path, start, end, sample_fps, detector_profile)
    _FACE_TRACK_CACHE[key] = tracks
    return tracks


def _clamp(value, low, high):
    return max(low, min(high, value))


def _clamp01(value):
    return _clamp(float(value), 0.0, 1.0)


def _pick_center(local_tracks, reframe_mode):
    if not local_tracks:
        return (0.5, 0.5), 0.0
    if reframe_mode == "speaker_focus":
        detected = [item for item in local_tracks if item["detected"]]
        if detected:
            best = max(detected, key=lambda item: item["box_w"] * item["box_h"])
            return (best["center_x"], best["center_y"]), best["box_w"] * best["box_h"]
    detected = [item for item in local_tracks if item["detected"]]
    source = detected or local_tracks
    cx = sum(item["center_x"] for item in source) / len(source)
    cy = sum(item["center_y"] for item in source) / len(source)
    strength = max((item["box_w"] * item["box_h"] for item in detected), default=0.0)
    return (cx, cy), strength


def _visible_faces(local_tracks, track_limit=3):
    faces = []
    for item in local_tracks:
        for face in item.get("faces", []) or []:
            if face.get("detected"):
                faces.append(face)
    faces.sort(
        key=lambda item: (
            float(item.get("speaking_score", 0.0)),
            float(item.get("listener_score", 0.0)),
            item["box_w"] * item["box_h"],
        ),
        reverse=True,
    )
    return faces[: max(1, int(track_limit))]


def _visible_persons(local_tracks, track_limit=2):
    persons = []
    for item in local_tracks:
        for person in item.get("persons", []) or []:
            persons.append(person)
    persons.sort(
        key=lambda item: (
            float(item.get("confidence", 0.0)),
            item["box_w"] * item["box_h"],
        ),
        reverse=True,
    )
    return persons[: max(1, int(track_limit))]


def _center_crop_geometry(source_w, source_h, target_w, target_h):
    src_aspect = source_w / float(source_h or 1)
    dst_aspect = target_w / float(target_h or 1)
    if src_aspect >= dst_aspect:
        crop_h = source_h
        crop_w = int(round(source_h * dst_aspect))
        crop_w = max(2, min(source_w, crop_w - (crop_w % 2)))
    else:
        crop_w = source_w
        crop_h = int(round(source_w / dst_aspect))
        crop_h = max(2, min(source_h, crop_h - (crop_h % 2)))
    return crop_w, crop_h


def _safe_face_preserving_offset(face_center_px, crop_size_px, frame_size_px, safe_margin_px):
    frame_size_px = max(1, int(frame_size_px))
    crop_size_px = max(2, int(crop_size_px))
    safe_margin_px = max(0, int(safe_margin_px))
    low = max(0, int(round(face_center_px - crop_size_px + safe_margin_px)))
    high = min(frame_size_px - crop_size_px, int(round(face_center_px - safe_margin_px)))
    if low > high:
        return _clamp(int(round(face_center_px - crop_size_px / 2.0)), 0, max(0, frame_size_px - crop_size_px))
    return _clamp(int(round(face_center_px - crop_size_px / 2.0)), low, high)


def _speaker_priority(face):
    return (
        float(face.get("speaking_score", 0.0)) * 1.15
        + float(face.get("listener_score", 0.0)) * 0.35
        + float(face["box_w"] * face["box_h"]) * 0.55
    )


def _listener_priority(face):
    return (
        float(face.get("listener_score", 0.0)) * 1.15
        + float(face.get("speaking_score", 0.0)) * 0.20
        + float(face["box_w"] * face["box_h"]) * 0.40
    )


# PHASE 3C: Turn-first speaker switching helpers
def _build_turn_timeline(subtitle_segments, start_t, end_t):
    """Build speaker turn timeline from subtitle segments."""
    if not subtitle_segments:
        return []
    
    timeline = []
    for index, seg in enumerate(subtitle_segments):
        seg_start = max(start_t, float(seg.get("start", 0.0)))
        seg_end = min(end_t, float(seg.get("end", seg_start + 0.5)))
        if seg_end <= seg_start or seg_start >= end_t:
            continue
        
        # PHASE 3C: Fix speaker_id fallback - do NOT use text as identity
        speaker_id = seg.get("speaker_id")
        if not speaker_id:
            speaker_id = f"unknown_turn_{index}"
        timeline.append({
            "start": seg_start,
            "end": seg_end,
            "speaker": str(speaker_id),
            "text": seg.get("text", ""),
        })
    
    # Merge consecutive same-speaker segments
    if not timeline:
        return []
    
    merged = [timeline[0]]
    for turn in timeline[1:]:
        prev = merged[-1]
        if turn["speaker"] == prev["speaker"] and turn["start"] - prev["end"] < 0.3:
            prev["end"] = turn["end"]
            prev["text"] += " " + turn["text"]
        else:
            merged.append(turn)
    
    return merged


def _find_best_face_for_speaker(faces, turn_start, turn_end):
    """Find best face for current turn using speaking_score priority."""
    if not faces:
        return (0.5, 0.5)
    
    # Filter faces active during turn
    active = []
    for face in faces:
        face_time = face.get("t", face.get("timestamp", 0.0))
        if turn_start <= face_time < turn_end and face.get("detected"):
            active.append(face)
    
    if not active:
        # Fallback: use any detected face
        active = [f for f in faces if f.get("detected")]
    
    if not active:
        return (0.5, 0.5)
    
    # Priority: speaking_score > listener_score > bbox size
    best = max(active, key=_speaker_priority)
    return (best["center_x"], best["center_y"])


def _dominant_track_id(tracks):
    weights = {}
    for item in tracks:
        for face in item.get("faces", []) or []:
            if not face.get("detected"):
                continue
            track_id = int(face.get("track_id", -1))
            weights[track_id] = weights.get(track_id, 0.0) + max(
                0.02,
                float(face.get("speaking_score", 0.0)) * 0.55
                + float(face.get("listener_score", 0.0)) * 0.20
                + face["box_w"] * face["box_h"],
            )
    if not weights:
        return None
    return max(weights.items(), key=lambda pair: pair[1])[0]


def _dominant_anchor(tracks, reframe_mode):
    detected = [item for item in tracks if item["detected"]]
    if not detected:
        return (0.5, 0.5), 0.0
    if reframe_mode == "speaker_focus":
        best = max(detected, key=lambda item: item["box_w"] * item["box_h"])
        return (best["center_x"], best["center_y"]), best["box_w"] * best["box_h"]
    weight_sum = 0.0
    sum_x = 0.0
    sum_y = 0.0
    for item in detected:
        weight = max(0.05, item["box_w"] * item["box_h"])
        sum_x += item["center_x"] * weight
        sum_y += item["center_y"] * weight
        weight_sum += weight
    if weight_sum <= 0.0:
        return (0.5, 0.5), 0.0
    return (sum_x / weight_sum, sum_y / weight_sum), weight_sum / len(detected)


def _smooth(prev_center, current_center, max_delta=0.05, blend=0.32):
    if prev_center is None:
        return current_center
    px, py = prev_center
    cx, cy = current_center
    if abs(cx - px) < 0.03 and abs(cy - py) < 0.03:
        return prev_center
    dx = _clamp(cx - px, -max_delta, max_delta)
    dy = _clamp(cy - py, -max_delta, max_delta)
    smoothed = (px + dx * blend, py + dy * blend)
    return _clamp(smoothed[0], 0.0, 1.0), _clamp(smoothed[1], 0.0, 1.0)


def _blend_towards(prev_center, target_center, blend=0.24, max_delta=0.045):
    return _smooth(prev_center, target_center, max_delta=max_delta, blend=blend)


def _track_key(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return str(value)


def _percentile(values, q):
    values = [float(item) for item in values if isinstance(item, (int, float))]
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    q = max(0.0, min(1.0, float(q)))
    pos = (len(ordered) - 1) * q
    left = int(pos)
    right = min(len(ordered) - 1, int(pos + 0.999999))
    if left == right:
        return ordered[left]
    alpha = pos - left
    return ordered[left] * (1.0 - alpha) + ordered[right] * alpha


def _scene_interest_center(global_anchor, last_detected=None, anchor_mode="stable_primary"):
    safe_center = (0.5, 0.46 if anchor_mode == "dialogue_center" else 0.5)
    if last_detected is None:
        return (
            safe_center[0] * 0.82 + global_anchor[0] * 0.18,
            safe_center[1] * 0.76 + global_anchor[1] * 0.24,
        )
    return (
        last_detected[0] * 0.45 + global_anchor[0] * 0.30 + safe_center[0] * 0.25,
        last_detected[1] * 0.40 + global_anchor[1] * 0.35 + safe_center[1] * 0.25,
    )


def _subject_person_center(person, global_anchor):
    return (
        float(person["center_x"]) * 0.82 + global_anchor[0] * 0.18,
        float(person["center_y"]) * 0.78 + global_anchor[1] * 0.22,
    )


def _framing_inner_height_ratio(framing, role=None):
    framing = str(framing or "face_locked").lower()
    role = str(role or "").lower()
    if framing == "tight_crop":
        return 0.96
    if framing == "square_canvas":
        return 0.72
    if framing == "context_padded":
        return 0.90
    if framing == "wide_subject":
        return 0.88
    if framing in {"shot_lock", "scene_lock"}:
        return 0.89
    if framing == "dialogue_dual" or role in {"dialogue_center", "listener_hold"}:
        return 0.93
    return 0.78


def _target_switch_score(target):
    role = str(target.get("target_role", "speaker"))
    speaker_confidence = float(target.get("speaker_confidence", 0.0) or 0.0)
    listener_confidence = float(target.get("listener_confidence", 0.0) or 0.0)
    subject_confidence = float(target.get("subject_confidence", 0.0) or 0.0)
    dialogue_likelihood = float(target.get("dialogue_likelihood", 0.0) or 0.0)
    strength = float(target.get("strength", 0.0) or 0.0)
    if role == "speaker":
        return speaker_confidence * 0.68 + subject_confidence * 0.16 + min(1.0, strength * 6.0) * 0.16
    if role == "dialogue_center":
        return dialogue_likelihood * 0.62 + listener_confidence * 0.12 + min(1.0, strength * 5.0) * 0.10 + subject_confidence * 0.16
    if role == "listener":
        return listener_confidence * 0.58 + dialogue_likelihood * 0.18 + subject_confidence * 0.24
    if role == "subject_person":
        return subject_confidence * 0.82 + min(1.0, strength * 4.0) * 0.18
    if role == "scene_interest":
        return 0.18 + subject_confidence * 0.15
    return subject_confidence * 0.35


def _speaker_confidence_score(target):
    subtitle_alignment = float(target.get("subtitle_turn_alignment_score", 0.0) or 0.0)
    voice_activity = max(
        float(target.get("speaker_confidence", 0.0) or 0.0),
        float(target.get("speaker_turn_strength", 0.0) or 0.0),
    )
    lip_motion = max(
        float(target.get("mouth_motion_proxy", 0.0) or 0.0),
        float((target.get("speaker_evidence_summary") or {}).get("mouth_motion_proxy", 0.0) or 0.0),
    )
    speaking_face_motion = max(
        float(target.get("lock_confidence", 0.0) or 0.0),
        float(target.get("subject_confidence", 0.0) or 0.0),
    )
    dialogue_context = max(
        float(target.get("dialogue_likelihood", 0.0) or 0.0),
        float(target.get("listener_confidence", 0.0) or 0.0) * 0.42,
    )
    return _clamp01(
        subtitle_alignment * 0.30
        + voice_activity * 0.26
        + lip_motion * 0.22
        + speaking_face_motion * 0.14
        + dialogue_context * 0.08
    )


def _visual_conversation_score(
    speaker_switches,
    windows_count,
    *,
    speaker_centered_rate=0.0,
    dialogue_center_windows=0,
    listener_fallback_windows=0,
    subject_person_fallback_windows=0,
    center_fallback_used=False,
    face_preserving_fallback_used=False,
):
    windows_count = max(1, int(windows_count))
    switch_rate = min(1.0, float(max(0, speaker_switches)) / max(1.0, windows_count - 1))
    switch_rhythm = 1.0 - min(1.0, abs(switch_rate - 0.32) / 0.32)
    dialogue_ratio = min(1.0, float(max(0, dialogue_center_windows)) / windows_count)
    listener_ratio = min(1.0, float(max(0, listener_fallback_windows + subject_person_fallback_windows)) / windows_count)
    centered_rate = _clamp01(float(speaker_centered_rate))
    fallback_penalty = min(
        1.0,
        (0.42 if center_fallback_used else 0.0)
        + (0.24 if face_preserving_fallback_used else 0.0)
        + max(0.0, 1.0 - centered_rate) * 0.32,
    )
    score = (
        centered_rate * 0.24
        + dialogue_ratio * 0.24
        + listener_ratio * 0.16
        + switch_rhythm * 0.24
        + min(1.0, switch_rate * 1.2) * 0.12
        - fallback_penalty * 0.22
    )
    return round(_clamp01(score), 4)


def _best_evidence_summary(targets):
    summaries = []
    for item in targets:
        summary = item.get("speaker_evidence_summary")
        if isinstance(summary, dict):
            summaries.append(summary)
    if not summaries:
        return {}
    return max(
        summaries,
        key=lambda summary: (
            int(summary.get("visible_faces", 0) or 0),
            int(summary.get("recent_face_memory_count", 0) or 0),
            float(summary.get("lock_confidence", 0.0) or 0.0),
            len(summary.get("top_tracks", []) or []),
            int(summary.get("visible_persons", 0) or 0),
        ),
    )


def _resolve_window_anchor(local_tracks, reframe_mode, anchor_mode, global_anchor, dominant_track_id, allow_wide_dialogue_center, track_limit=3, dual_face_margin=0.14, speaker_center_max_offset=0.18, strict_lock=False, strict_center=False):
    visible_faces = _visible_faces(local_tracks, track_limit=track_limit)
    if not visible_faces:
        return global_anchor, 0.0, None, [], [], None, None

    by_track = {}
    for face in visible_faces:
        track_id = int(face.get("track_id", -1))
        best = by_track.get(track_id)
        if best is None or (face["box_w"] * face["box_h"]) > (best["box_w"] * best["box_h"]):
            by_track[track_id] = face
    stable_faces = list(by_track.values())
    stable_faces.sort(key=_speaker_priority, reverse=True)

    if strict_center:
        best_speaker = max(stable_faces, key=_speaker_priority)
        best_listener = max(stable_faces, key=_listener_priority)
        return (
            (best_speaker["center_x"], best_speaker["center_y"]),
            max(best_speaker["box_w"] * best_speaker["box_h"], float(best_speaker.get("speaking_score", 0.0))),
            int(best_speaker.get("track_id", -1)),
            [int(face["track_id"]) for face in stable_faces],
            stable_faces,
            best_speaker,
            best_listener,
        )

    if allow_wide_dialogue_center and len(stable_faces) >= 2:
        top_two = stable_faces[:2]
        xs = [item["center_x"] for item in top_two]
        sizes = [item["box_w"] * item["box_h"] for item in top_two]
        balanced_pair = min(sizes) >= max(sizes) * 0.35
        spread = max(xs) - min(xs)
        top_speaker = max(top_two, key=lambda item: float(item.get("speaking_score", 0.0)))
        second_speaker = min(top_two, key=lambda item: float(item.get("speaking_score", 0.0)))
        speaker_gap = float(top_speaker.get("speaking_score", 0.0)) - float(second_speaker.get("speaking_score", 0.0))
        dialogue_ready = (
            (anchor_mode == "dialogue_center" and not strict_center)
            or (
                anchor_mode == "stable_primary"
                and balanced_pair
                and spread >= dual_face_margin * 0.78
                and speaker_gap <= 0.12
            )
        )
        if spread >= dual_face_margin and dialogue_ready:
            pair_center = ((max(xs) + min(xs)) / 2.0, sum(item["center_y"] for item in top_two) / len(top_two))
            if strict_lock or strict_center:
                speaker_offset = max(
                    abs(float(top_speaker["center_x"]) - float(pair_center[0])),
                    abs(float(top_speaker["center_y"]) - float(pair_center[1])),
                )
                if strict_center or speaker_offset > float(speaker_center_max_offset):
                    center = (top_speaker["center_x"], top_speaker["center_y"])
                else:
                    speaker_bias = 0.88 if reframe_mode == "balanced" else 0.82
                    center = (
                        top_speaker["center_x"] * speaker_bias + pair_center[0] * (1.0 - speaker_bias),
                        top_speaker["center_y"] * speaker_bias + pair_center[1] * (1.0 - speaker_bias),
                    )
            else:
                center = pair_center
            strength = sum(item["box_w"] * item["box_h"] for item in top_two) / len(top_two)
            best_listener = max(stable_faces, key=_listener_priority)
            return center, strength, "dialogue_center", [int(item["track_id"]) for item in top_two], stable_faces, top_speaker, best_listener

    best_speaker = max(stable_faces, key=_speaker_priority)
    best_listener = max(stable_faces, key=_listener_priority)
    selected = best_speaker
    if dominant_track_id is not None:
        dominant_face = None
        for face in stable_faces:
            if int(face.get("track_id", -1)) == int(dominant_track_id):
                dominant_face = face
                break
        if dominant_face is not None:
            dominant_score = _speaker_priority(dominant_face)
            best_score = _speaker_priority(best_speaker)
            dominant_speaking = float(dominant_face.get("speaking_score", 0.0))
            best_speaking = float(best_speaker.get("speaking_score", 0.0))
            if best_speaker is dominant_face:
                selected = dominant_face
            elif best_speaking >= dominant_speaking + 0.14 or best_score >= dominant_score + 0.12:
                selected = best_speaker
            else:
                selected = dominant_face

    center = (selected["center_x"], selected["center_y"])
    if anchor_mode == "stable_primary":
        blend = 0.72 if reframe_mode == "balanced" else 0.45
        if strict_lock:
            blend = min(blend, 0.24 if reframe_mode == "balanced" else 0.32)
        if strict_center:
            blend = min(blend, 0.06)
        center = (
            center[0] * (1.0 - blend) + global_anchor[0] * blend,
            center[1] * (1.0 - blend) + global_anchor[1] * blend,
        )
    return (
        center,
        max(selected["box_w"] * selected["box_h"], float(selected.get("speaking_score", 0.0))),
        int(selected.get("track_id", -1)),
        [int(face["track_id"]) for face in stable_faces],
        stable_faces,
        best_speaker,
        best_listener,
    )


def _build_window_targets(
    tracks,
    start_t,
    end_t,
    window_sec,
    reframe_mode,
    turn_timeline=None,  # PHASE 3C: Turn-first speaker switching
    anchor_mode="stable_primary",
    allow_wide_dialogue_center=True,
    track_limit=3,
    dual_face_margin=0.14,
    scene_interest_fallback=True,
    scene_interest_fallback_mode="normal",
    listener_face_fallback=True,
    speaker_lock_strict_mode=False,
    speaker_center_strict_mode=False,
    speaker_center_max_offset=0.16,
    speaker_face_lock_min_margin=0.12,
    dialogue_center_use_threshold=0.70,
    listener_fallback_max_hold_seconds=0.65,
    listener_fallback_speech_hold_max_seconds=0.40,
    dialogue_center_min_likelihood=0.48,
    dialogue_center_balance_margin=0.08,
    subject_confidence_floor=0.42,
    subject_visibility_threshold=0.46,
    reframe_subject_mode="subject_first",
    dialogue_two_shot_preferred=True,
    new_face_fast_acquire_threshold=0.78,
):
    targets = []
    cursor = start_t
    last_detected = None
    last_stable_faces = []
    last_stable_persons = []
    dialogue_memory = 0
    global_anchor, _ = _dominant_anchor(tracks, reframe_mode)
    dominant_track_id = _dominant_track_id(tracks)
    strict_lock = bool(speaker_lock_strict_mode)
    strict_center = bool(speaker_center_strict_mode)
    balance_margin = max(0.03, float(dialogue_center_balance_margin))
    speaker_center_max_offset = max(0.08, float(speaker_center_max_offset))
    speaker_face_lock_min_margin = max(0.03, float(speaker_face_lock_min_margin))
    dialogue_center_use_threshold = max(
        0.82 if (strict_lock or strict_center) else 0.0,
        float(dialogue_center_min_likelihood),
        float(dialogue_center_use_threshold),
    )
    listener_fallback_hold_cap = min(
        float(listener_fallback_max_hold_seconds),
        float(listener_fallback_speech_hold_max_seconds),
    )
    while cursor < end_t:
        window_end = min(end_t, cursor + max(0.6, float(window_sec)))
        local = [item for item in tracks if cursor <= item["t"] < window_end]
        
        # PHASE 3C: Turn-first speaker switching - determine active turn
        active_turn = None
        subtitle_turn_changed = False
        turn_boundary_force_switch = False
        if turn_timeline:
            for turn in turn_timeline:
                if turn["start"] <= cursor < turn["end"]:
                    active_turn = turn
                    break
            
            # Track turn changes
            if len(targets) > 0:
                prev_time = targets[-1]["start"]
                prev_turn_speaker = None
                for turn in turn_timeline:
                    if turn["start"] <= prev_time < turn["end"]:
                        prev_turn_speaker = turn["speaker"]
                        break
                
                current_turn_speaker = active_turn["speaker"] if active_turn else None
                subtitle_turn_changed = (
                    prev_turn_speaker is not None
                    and current_turn_speaker is not None
                    and current_turn_speaker != prev_turn_speaker
                )
                
                # PHASE 3C: Turn boundary becomes primary switch authority
                if subtitle_turn_changed:
                    turn_boundary_force_switch = True
                    # Boost dialogue memory to maintain context during turn transition
                    dialogue_memory = max(dialogue_memory, 2)
        
        local_persons = _visible_persons(local, track_limit=max(1, int(track_limit) - 1))
        evidence_windows = [item.get("speaker_evidence_summary", {}) for item in local if isinstance(item.get("speaker_evidence_summary"), dict)]
        recent_face_memory_count = max([int(item.get("recent_face_memory_count", 0) or 0) for item in evidence_windows] or [0])
        face_hold_available = any(bool(item.get("face_hold_available")) for item in evidence_windows)
        scene_change_score = max([float(item.get("scene_change_score", 0.0) or 0.0) for item in local] or [0.0])
        scene_change_detected = any(bool(item.get("scene_change_detected")) for item in local) or scene_change_score >= 0.18
        if scene_change_detected:
            # Re-evaluate on cuts, but keep a short subject memory so the crop can
            # hand off naturally instead of briefly collapsing into scene-interest.
            dialogue_memory = max(0, dialogue_memory - 1)
        center, strength, anchor_track_id, visible_track_ids, stable_faces, best_speaker_face, best_listener_face = _resolve_window_anchor(
            local,
            reframe_mode,
            anchor_mode,
            global_anchor,
            dominant_track_id,
            allow_wide_dialogue_center,
            track_limit=track_limit,
            dual_face_margin=dual_face_margin,
            speaker_center_max_offset=speaker_center_max_offset,
        strict_lock=strict_lock,
        strict_center=strict_center,
    )
        listener_fallback_used = False
        scene_interest_used = False
        target_role = "speaker"
        visible_face_count = len(visible_track_ids)
        visible_person_count = len(local_persons)
        recoverable_subject = bool(
            recent_face_memory_count > 0
            or face_hold_available
            or last_stable_faces
            or last_stable_persons
            or (dialogue_memory > 0 and last_detected is not None)
        )
        no_subject_detected = bool(
            not visible_face_count
            and not visible_person_count
            and not recoverable_subject
            and not (dialogue_memory > 0 and last_detected is not None)
        )
        speaker_confidence = max(
            [float(item.get("primary_speaking_score", 0.0)) for item in evidence_windows]
            + ([float(best_speaker_face.get("speaking_score", 0.0))] if best_speaker_face else [])
            or [0.0]
        )
        subtitle_alignment_score = max(
            [float(item.get("dialogue_scene_likelihood", 0.0)) for item in evidence_windows]
            + [max([float(track.get("mouth_motion_proxy", 0.0)) for track in (item.get("top_tracks", []) or [])] or [0.0]) for item in evidence_windows]
            or [0.0]
        )
        mouth_motion_proxy = max(
            [float(track.get("mouth_motion_proxy", 0.0)) for item in evidence_windows for track in (item.get("top_tracks", []) or [])]
            + ([float(best_speaker_face.get("mouth_motion_proxy", 0.0))] if best_speaker_face else [])
            or [0.0]
        )
        dialogue_likelihood = max(
            [float(item.get("dialogue_scene_likelihood", 0.0)) for item in evidence_windows] or [0.0]
        )
        listener_confidence = max(
            [float(face.get("listener_score", 0.0)) for face in stable_faces]
            + ([float(best_listener_face.get("listener_score", 0.0))] if best_listener_face else [])
            or [0.0]
        )
        lock_confidence = max(
            [float(item.get("lock_confidence", 0.0)) for item in evidence_windows]
            + ([float(best_speaker_face.get("lock_confidence", 0.0))] if best_speaker_face else [])
            or [0.0]
        )
        subject_confidence = max(
            speaker_confidence,
            listener_confidence * 0.88,
            max([float(person.get("confidence", 0.0)) for person in local_persons] or [0.0]),
        )
        speaker_turn_strength = 0.0
        if best_speaker_face is not None:
            speaker_turn_strength = max(
                0.0,
                float(best_speaker_face.get("speaking_score", 0.0))
                - float(best_speaker_face.get("listener_score", 0.0)) * 0.45,
            )
        speaker_confidence_score = _speaker_confidence_score(
            {
                "subtitle_turn_alignment_score": subtitle_alignment_score,
                "speaker_confidence": speaker_confidence,
                "speaker_turn_strength": speaker_turn_strength,
                "mouth_motion_proxy": mouth_motion_proxy,
                "lock_confidence": lock_confidence,
                "subject_confidence": subject_confidence,
                "dialogue_likelihood": dialogue_likelihood,
                "listener_confidence": listener_confidence,
                "speaker_evidence_summary": evidence_windows[-1] if evidence_windows else {},
            }
        )
        if visible_face_count > 0:
            last_detected = center
            last_stable_faces = list(stable_faces or [])
            if local_persons:
                last_stable_persons = list(local_persons)
        elif local_persons:
            primary_person = local_persons[0]
            if strict_lock:
                center = last_detected or global_anchor
                anchor_track_id = "hold_last_face" if last_detected is not None else "safe_center"
                target_role = "hold_last_face" if last_detected is not None else "safe_center"
                strength = max(strength, float(primary_person["box_w"]) * float(primary_person["box_h"]) * max(0.22, float(primary_person.get("confidence", 0.5)) * 0.45))
                last_detected = center
                subject_confidence = max(subject_confidence, float(primary_person.get("confidence", 0.0)) * 0.72)
            else:
                center = _subject_person_center(primary_person, global_anchor)
                anchor_track_id = "subject_person"
                strength = max(strength, float(primary_person["box_w"]) * float(primary_person["box_h"]) * max(0.35, float(primary_person.get("confidence", 0.5))))
                target_role = "subject_person"
                last_detected = center
                subject_confidence = max(subject_confidence, float(primary_person.get("confidence", 0.0)))
            last_stable_persons = list(local_persons)
        elif listener_face_fallback and last_stable_faces:
            listener_candidates = sorted(last_stable_faces, key=_listener_priority, reverse=True)
            fallback_face = best_listener_face or listener_candidates[0]
            fallback_listener_conf = max(listener_confidence, float(fallback_face.get("listener_score", 0.0)))
            fallback_speaker_conf = max(
                speaker_confidence,
                float(best_speaker_face.get("speaking_score", 0.0)) if best_speaker_face is not None else 0.0,
            )
            if strict_lock and fallback_speaker_conf >= fallback_listener_conf - balance_margin:
                speaker_face = best_speaker_face or fallback_face
                center = (speaker_face["center_x"], speaker_face["center_y"])
                anchor_track_id = int(speaker_face.get("track_id", -1))
                target_role = "speaker"
                last_detected = center
                speaker_confidence = max(speaker_confidence, float(speaker_face.get("speaking_score", 0.0)))
            else:
                center = (
                    fallback_face["center_x"] * 0.78 + global_anchor[0] * 0.22,
                    fallback_face["center_y"] * 0.78 + global_anchor[1] * 0.22,
                )
                anchor_track_id = int(fallback_face.get("track_id", -1))
                if strict_lock or strict_center:
                    target_role = "hold_last_face" if last_detected is not None else "safe_center"
                    center = last_detected or global_anchor
                    anchor_track_id = "hold_last_face" if last_detected is not None else "safe_center"
                else:
                    listener_fallback_used = True
                    target_role = "listener"
                    last_detected = center
                    listener_confidence = max(listener_confidence, fallback_listener_conf)
                    subject_confidence = max(subject_confidence, listener_confidence * 0.92)
        elif last_stable_persons and not (strict_lock or strict_center):
            fallback_person = last_stable_persons[0]
            center = _subject_person_center(fallback_person, global_anchor)
            anchor_track_id = "subject_person"
            strength = max(strength, float(fallback_person["box_w"]) * float(fallback_person["box_h"]) * max(0.3, float(fallback_person.get("confidence", 0.45))))
            target_role = "subject_person"
            last_detected = center
            subject_confidence = max(subject_confidence, float(fallback_person.get("confidence", 0.0)))
        elif scene_interest_fallback:
            allow_scene_interest = True
            if str(scene_interest_fallback_mode or "normal").lower() == "emergency_only":
                allow_scene_interest = not bool(
                    last_detected
                    or last_stable_faces
                    or last_stable_persons
                    or recent_face_memory_count
                    or face_hold_available
                    or dialogue_memory > 0
                )
            if not allow_scene_interest:
                if len(last_stable_faces) >= 2 and (
                    dialogue_likelihood >= dialogue_center_use_threshold * 0.72
                    or dialogue_memory > 0
                    or scene_change_detected
                ):
                    top_two = last_stable_faces[:2]
                    xs = [face["center_x"] for face in top_two]
                    ys = [face["center_y"] for face in top_two]
                    pair_center = ((max(xs) + min(xs)) / 2.0, sum(ys) / len(ys))
                    top_speaker = max(top_two, key=_speaker_priority)
                    if strict_center:
                        speaker_bias = 0.78 if reframe_mode == "balanced" else 0.70
                        center = (
                            top_speaker["center_x"] * speaker_bias + pair_center[0] * (1.0 - speaker_bias),
                            top_speaker["center_y"] * speaker_bias + pair_center[1] * (1.0 - speaker_bias),
                        )
                    else:
                        center = pair_center
                    target_role = "dialogue_center"
                    anchor_track_id = "dialogue_center"
                elif last_stable_faces:
                    fallback_face = sorted(last_stable_faces, key=_listener_priority, reverse=True)[0]
                    if strict_lock or strict_center:
                        best_speaker_face = max(last_stable_faces, key=_speaker_priority)
                        center = (best_speaker_face["center_x"], best_speaker_face["center_y"])
                        target_role = "speaker"
                        anchor_track_id = int(best_speaker_face.get("track_id", -1))
                    else:
                        center = (
                            fallback_face["center_x"] * 0.74 + global_anchor[0] * 0.26,
                            fallback_face["center_y"] * 0.74 + global_anchor[1] * 0.26,
                        )
                        target_role = "listener"
                        anchor_track_id = int(fallback_face.get("track_id", -1))
                        listener_fallback_used = True
                else:
                    center = last_detected or global_anchor
                    target_role = "hold_last_face" if last_detected is not None else "safe_center"
                    anchor_track_id = "hold_last_face" if last_detected is not None else "safe_center"
            else:
                center = _scene_interest_center(global_anchor, last_detected=last_detected, anchor_mode=anchor_mode)
                anchor_track_id = "scene_interest"
                scene_interest_used = True
                target_role = "scene_interest"
        elif last_detected is not None:
            center = last_detected
            target_role = "hold_last_face"
        recent_dual_memory = len(last_stable_faces) >= 2 and (
            dialogue_likelihood >= dialogue_center_use_threshold * 0.82
            or speaker_confidence >= 0.18
            or listener_confidence >= 0.18
        )
        if dialogue_likelihood >= dialogue_center_use_threshold and (
            visible_face_count >= 2
            or (best_listener_face is not None and best_speaker_face is not None and float(best_listener_face.get("listener_score", 0.0)) >= 0.24)
        ) or recent_dual_memory:
            dialogue_memory = max(dialogue_memory, 2)
        elif dialogue_memory > 0:
            dialogue_memory -= 1
        if (
            anchor_track_id != "dialogue_center"
            and (
                visible_face_count >= 2
                or dialogue_memory > 0
                or (dialogue_likelihood >= dialogue_center_use_threshold and speaker_confidence < 0.78)
            )
        ):
            pair_candidates = []
            if best_speaker_face is not None:
                pair_candidates.append(best_speaker_face)
            if best_listener_face is not None and best_listener_face not in pair_candidates:
                pair_candidates.append(best_listener_face)
            if len(pair_candidates) < 2 and len(last_stable_faces) >= 2:
                pair_candidates = last_stable_faces[:2]
                if len(pair_candidates) >= 2:
                    xs = [face["center_x"] for face in pair_candidates[:2]]
                    ys = [face["center_y"] for face in pair_candidates[:2]]
                    if strict_center:
                        top_speaker = max(pair_candidates[:2], key=_speaker_priority)
                        center = (top_speaker["center_x"], top_speaker["center_y"])
                    else:
                        center = ((max(xs) + min(xs)) / 2.0, sum(ys) / len(ys))
                    if (not strict_center) and bool(dialogue_two_shot_preferred) and dialogue_likelihood >= dialogue_center_use_threshold and abs(speaker_confidence - listener_confidence) <= balance_margin:
                        anchor_track_id = "dialogue_center"
                        target_role = "dialogue_center"
                        strength = max(strength, dialogue_likelihood * 0.92, 0.22)
                        subject_confidence = max(subject_confidence, dialogue_likelihood * 0.96)
        if (not strict_center) and visible_face_count >= 2 and dialogue_likelihood >= dialogue_center_use_threshold and abs(speaker_confidence - listener_confidence) <= balance_margin and speaker_confidence < 0.64:
            anchor_track_id = "dialogue_center"
            target_role = "dialogue_center"
            dialogue_likelihood = max(dialogue_likelihood, float(dialogue_center_use_threshold))
            subject_confidence = max(subject_confidence, dialogue_likelihood * 0.96)
        speaker_face_edge_clipped = False
        if best_speaker_face is not None:
            sx = float(best_speaker_face.get("center_x", 0.5))
            sy = float(best_speaker_face.get("center_y", 0.5))
            sw = float(best_speaker_face.get("box_w", 0.22))
            sh = float(best_speaker_face.get("box_h", 0.28))
            speaker_face_edge_clipped = (
                (sx - sw * 0.5) < 0.05
                or (sx + sw * 0.5) > 0.95
                or (sy - sh * 0.5) < 0.05
                or (sy + sh * 0.5) > 0.95
            )
        if speaker_face_edge_clipped and target_role == "speaker":
            if (not strict_center) and visible_face_count >= 2 and abs(speaker_confidence - listener_confidence) <= balance_margin:
                target_role = "dialogue_center"
                anchor_track_id = "dialogue_center"
            elif (not strict_lock) and best_listener_face is not None and listener_confidence >= speaker_confidence + balance_margin:
                target_role = "listener"
                anchor_track_id = int(best_listener_face.get("track_id", -1))
                center = (float(best_listener_face["center_x"]), float(best_listener_face["center_y"]))
                listener_confidence = max(listener_confidence, float(best_listener_face.get("listener_score", 0.0)))
        if str(reframe_subject_mode or "subject_first").lower() == "subject_first":
            low_subject_confidence = subject_confidence < float(subject_visibility_threshold)
            if target_role == "speaker" and (low_subject_confidence or speaker_face_edge_clipped):
                if visible_face_count >= 2 and bool(dialogue_two_shot_preferred) and dialogue_likelihood >= dialogue_center_use_threshold:
                    if abs(speaker_confidence - listener_confidence) <= balance_margin and not strict_center:
                        anchor_track_id = "dialogue_center"
                        target_role = "dialogue_center"
                        subject_confidence = max(subject_confidence, dialogue_likelihood * 0.96)
                elif (not (strict_lock or strict_center)) and best_listener_face is not None and listener_confidence >= 0.24 and speaker_confidence < 0.52:
                    anchor_track_id = int(best_listener_face.get("track_id", -1))
                    target_role = "listener"
                    center = (float(best_listener_face["center_x"]), float(best_listener_face["center_y"]))
                    subject_confidence = max(subject_confidence, listener_confidence * 0.92)
                elif (not (strict_lock or strict_center)) and (local_persons or last_stable_persons):
                    fallback_person = (local_persons or last_stable_persons)[0]
                    anchor_track_id = "subject_person"
                    target_role = "subject_person"
                    center = _subject_person_center(fallback_person, global_anchor)
                    subject_confidence = max(subject_confidence, float(fallback_person.get("confidence", 0.0)))
        if anchor_track_id == "dialogue_center":
            target_role = "dialogue_center"
        subject_mode = {
            "speaker": "speaker_face",
            "listener": "listener_face",
            "dialogue_center": "dialogue_center",
            "subject_person": "person",
            "scene_interest": "scene_interest",
            "safe_center": "safe_center",
            "hold_last_face": "speaker_face",
        }.get(target_role, "safe_center")
        subject_visible = bool(
            not no_subject_detected
            or target_role in {"listener", "dialogue_center", "subject_person", "hold_last_face"}
            or recent_face_memory_count > 0
            or face_hold_available
        )
        fast_reacquire_candidate = bool(
            scene_change_detected
            and visible_face_count > 0
            and (
                (
                    target_role == "speaker"
                    and speaker_confidence >= float(new_face_fast_acquire_threshold) * 0.88
                    and not speaker_face_edge_clipped
                )
                or (
                    target_role == "dialogue_center"
                    and dialogue_likelihood >= float(dialogue_center_min_likelihood) * 0.92
                    and visible_face_count >= 2
                )
                or (
                    target_role == "listener"
                    and listener_confidence >= 0.50
                    and visible_face_count >= 1
                )
            )
        )
        targets.append(
            {
                "start": cursor,
                "end": window_end,
                "center": center,
                "strength": strength,
                "detected_count": sum(1 for item in local if item["detected"]),
                "visible_face_count": visible_face_count,
                "visible_person_count": visible_person_count,
                "visible_subject_count": visible_face_count + visible_person_count + (1 if recoverable_subject and not (visible_face_count or visible_person_count) else 0),
                "primary_visible": visible_face_count > 0,
                "anchor_track_id": anchor_track_id,
                "visible_track_ids": visible_track_ids,
                "listener_fallback_used": listener_fallback_used,
                "scene_interest_fallback_used": scene_interest_used,
                "target_role": target_role,
                "speaker_confidence": speaker_confidence,
                "listener_confidence": listener_confidence,
                "lock_confidence": lock_confidence,
                "subject_confidence": max(subject_confidence, float(subject_confidence_floor) if not no_subject_detected else 0.0),
                "speaker_face_edge_clipped": bool(speaker_face_edge_clipped),
                "subject_mode": subject_mode,
                "subject_visible": subject_visible,
                "speaker_turn_strength": round(float(speaker_turn_strength), 4),
                "speaker_confidence_score": round(float(speaker_confidence_score), 4),
                "mouth_motion_proxy": round(float(mouth_motion_proxy), 4),
                "subtitle_turn_alignment_score": round(float(subtitle_alignment_score), 4),
                "dialogue_likelihood": dialogue_likelihood,
                "no_subject_detected": no_subject_detected,
                "recoverable_subject": recoverable_subject,
                "recent_face_memory_count": int(recent_face_memory_count),
                "face_hold_available": bool(face_hold_available),
                "speaker_evidence_summary": evidence_windows[-1] if evidence_windows else {},
                "listener_candidate_center": (
                    (best_listener_face["center_x"], best_listener_face["center_y"])
                    if best_listener_face is not None
                    else (
                        (stable_faces[1]["center_x"], stable_faces[1]["center_y"])
                        if len(stable_faces) >= 2
                        else ((stable_faces[0]["center_x"], stable_faces[0]["center_y"]) if stable_faces else None)
                    )
                ),
                "subject_candidate_center": (
                    (_subject_person_center(local_persons[0], global_anchor))
                    if local_persons
                    else ((_subject_person_center(last_stable_persons[0], global_anchor)) if last_stable_persons else None)
                ),
                "speaker_candidate_center": (
                    (best_speaker_face["center_x"], best_speaker_face["center_y"])
                    if best_speaker_face is not None
                    else None
                ),
                "speaker_candidate_track_id": (
                    int(best_speaker_face.get("track_id", -1))
                    if best_speaker_face is not None
                    else None
                ),
                "scene_change_score": round(float(scene_change_score), 4),
                "scene_change_detected": bool(scene_change_detected),
                "fast_reacquire_candidate": bool(fast_reacquire_candidate),
                "switch_score": 0.0,
                "subtitle_turn_changed": bool(subtitle_turn_changed),
                "active_turn_speaker": active_turn["speaker"] if active_turn else None,
            }
        )
        targets[-1]["switch_score"] = round(_target_switch_score(targets[-1]), 4)
        targets[-1]["speaker_confidence_score"] = round(float(speaker_confidence_score), 4)
        cursor = window_end
    return targets


def _turn_based_targets(
    targets,
    reframe_mode,
    transition_mode="smooth",
    hold_windows=2,
    accent_frame_hold_windows=0,
    switch_min_visibility=0.38,
    lost_face_hold_seconds=1.5,
    reframe_priority="stability_first",
    speaker_min_hold_seconds=0.9,
    listener_hold_seconds=0.65,
    speaker_lock_strict_mode=True,
    listener_fallback_max_hold_seconds=0.65,
    dialogue_center_balance_margin=0.08,
    empty_frame_guard_enabled=True,
    max_crop_delta_per_window=0.05,
    motion_blend_normal=0.2,
    motion_blend_switch=0.32,
    switch_score_margin=0.08,
    scene_recenter_hold_windows=4,
    scene_change_threshold=0.18,
    target_deadband=0.02,
    lock_confidence_threshold=0.72,
    speaker_confidence_threshold=0.62,
    handoff_min_hold_windows=2,
    confident_lock_min_hold_windows=4,
    target_deadband_handoff=0.028,
    target_deadband_lock=0.018,
    max_delta_handoff=0.028,
    max_delta_lock=0.020,
    motion_blend_switch_handoff=0.22,
    motion_blend_normal_handoff=0.14,
    shot_reacquire_boost_windows=2,
    new_face_fast_acquire_threshold=0.78,
    speaker_center_strict_mode=False,
    speaker_center_max_offset=0.16,
    speaker_face_lock_min_margin=0.12,
    dialogue_center_use_threshold=0.70,
    listener_fallback_speech_hold_max_seconds=0.40,
):
    if not targets:
        return [], {}

    current = targets[0]["center"]
    pending = None
    pending_count = 0
    priority = str(reframe_priority or "stability_first").lower()
    transition_mode = str(transition_mode or "smooth").lower()
    strict_lock = bool(speaker_lock_strict_mode)
    strict_center = bool(speaker_center_strict_mode)
    hard_switch_mode = transition_mode in {"hard_switch", "strict_switch"}
    strict_switch_margin = float(switch_score_margin) + (0.05 if strict_center else 0.0)
    balance_margin = max(0.03, float(dialogue_center_balance_margin))
    speaker_center_max_offset = max(0.08, float(speaker_center_max_offset))
    speaker_face_lock_min_margin = max(0.03, float(speaker_face_lock_min_margin))
    dialogue_center_use_threshold = max(0.82 if strict_center else 0.42, float(dialogue_center_use_threshold))
    hysteresis = 0.10 if priority == "stability_first" and reframe_mode == "balanced" else (0.08 if reframe_mode == "balanced" else 0.05)
    if strict_center:
        hysteresis = max(hysteresis, 0.08)
    if hard_switch_mode:
        max_delta = 0.012
        blend = 0.0
    elif transition_mode == "hold_frame":
        max_delta = 0.015
        blend = 0.12
    elif transition_mode == "fast_smooth":
        max_delta = 0.06 if reframe_mode == "balanced" else 0.09
        blend = 0.42
    else:
        max_delta = 0.03 if reframe_mode == "balanced" else 0.055
        blend = 0.20 if reframe_mode == "balanced" else 0.35
    if priority == "stability_first":
        max_delta *= 0.78
        blend *= 0.82
        hold_windows = max(hold_windows, 3)
    if strict_center:
        # Strict speaker-first mode should react faster and cling less to the previous anchor.
        hold_windows = 0
    max_delta = min(max_delta, float(max_crop_delta_per_window))
    blend = min(blend, float(motion_blend_normal))
    if strict_center:
        max_delta = max(max_delta, min(0.075, float(max_crop_delta_per_window)))
        blend = 0.0 if hard_switch_mode else min(blend, 0.12)
    resolved = []
    hold_windows = max(0, int(hold_windows))
    accent_frame_hold_windows = max(0, int(accent_frame_hold_windows))
    invisible_streak = 0
    current_track_id = targets[0].get("anchor_track_id")
    current_role = targets[0].get("target_role", "speaker")
    current_switch_score = float(targets[0].get("switch_score", 0.0) or 0.0)
    face_lock_windows = hold_windows + (2 if priority == "stability_first" and not strict_center else 1)
    lock_state = "speaker_locked"
    state_usage = {"speaker_locked": 0, "listener_hold": 0, "dialogue_center": 0, "lost_face_recover": 0, "scene_interest_fallback": 0, "subject_person_hold": 0, "hard_switch": 0}
    role_hold_counter = 0
    scene_hold_counter = 0
    speaker_hold_windows = 0 if strict_center else max(hold_windows, int(round(max(0.6, float(speaker_min_hold_seconds)) / 0.6)))
    listener_fallback_hold_cap = min(float(listener_fallback_max_hold_seconds), float(listener_fallback_speech_hold_max_seconds))
    listener_hold_seconds = min(float(listener_hold_seconds), listener_fallback_hold_cap, float(listener_fallback_speech_hold_max_seconds))
    listener_hold_windows = 1 if strict_center else max(1, int(round(max(0.5, listener_hold_seconds) / 0.6)))
    scene_recenter_hold_windows = 0 if strict_center else max(0, int(scene_recenter_hold_windows))
    if hard_switch_mode and strict_center:
        speaker_hold_windows = 0
        listener_hold_windows = 1
        scene_recenter_hold_windows = 0
    scene_change_threshold = max(0.08, float(scene_change_threshold))
    target_deadband = max(0.005, float(target_deadband))
    scene_recenter_count = 0
    confident_lock_windows = 0
    handoff_glide_windows = 0
    hard_switch_windows = 0
    switch_latency_windows = 0
    reacquire_boost_counter = 0
    reacquire_cooldown = 0
    fast_reacquire_attempted = 0
    fast_reacquire_success = 0
    new_face_acquire_count = 0
    
    # PHASE 3C: Turn-first hold/cooldown counters
    speaker_hold_counter = 0
    speaker_switch_cooldown = 0
    last_turn_speaker = None
    forced_turn_switches = 0
    cooldown_blocked_switches = 0

    for target in targets:
        candidate = target["center"]
        shift = abs(candidate[0] - current[0]) + abs(candidate[1] - current[1])
        candidate_track_id = target.get("anchor_track_id")
        candidate_role = target.get("target_role", "speaker")
        candidate_switch_score = float(target.get("speaker_confidence_score", target.get("switch_score", 0.0)) or 0.0)
        visible_face_count = int(target.get("visible_face_count", 0) or 0)
        visible_subject_count = int(target.get("visible_subject_count", visible_face_count) or 0)
        scene_change_score = float(target.get("scene_change_score", 0.0) or 0.0)
        scene_change_detected = bool(target.get("scene_change_detected")) or scene_change_score >= scene_change_threshold
        lock_confidence = float(target.get("lock_confidence", 0.0) or 0.0)
        speaker_confidence = float(target.get("speaker_confidence_score", target.get("speaker_confidence", 0.0)) or 0.0)
        speaker_face_edge_clipped = bool(target.get("speaker_face_edge_clipped", False))
        accent_hold_active = bool(
            accent_frame_hold_windows > 0
            and current_role == "speaker"
            and candidate_role == "speaker"
            and not scene_change_detected
            and not speaker_face_edge_clipped
        )
        confident_lock = bool(
            lock_confidence >= float(lock_confidence_threshold)
            and speaker_confidence >= float(speaker_confidence_threshold)
            and visible_face_count >= 1
            and not speaker_face_edge_clipped
        )
        if confident_lock:
            target_deadband_local = float(target_deadband_lock)
            max_delta_local = min(max_delta, float(max_delta_lock))
            blend_switch_local = min(float(motion_blend_switch), 0.18)
            blend_normal_local = min(float(motion_blend_normal), 0.12)
            required_hold_floor = 1 if strict_center else max(int(confident_lock_min_hold_windows), hold_windows)
            confident_lock_windows += 1
            handoff_mode = "confident_lock"
        else:
            target_deadband_local = float(target_deadband_handoff)
            max_delta_local = min(max_delta, float(max_delta_handoff))
            blend_switch_local = float(motion_blend_switch_handoff)
            blend_normal_local = float(motion_blend_normal_handoff)
            if hard_switch_mode and strict_center:
                required_hold_floor = 1
                handoff_mode = "hard_switch"
            else:
                required_hold_floor = 1 if strict_center else max(int(handoff_min_hold_windows), 1)
                handoff_glide_windows += 1
                handoff_mode = "handoff_glide"
        strong_turn_switch = False
        if scene_change_detected:
            scene_hold_counter = 0
            if candidate_role == "speaker":
                role_hold_counter = 0
            reacquire_boost_counter = max(reacquire_boost_counter, int(shot_reacquire_boost_windows))
        recoverable_subject = bool(target.get("recoverable_subject")) or bool(target.get("face_hold_available"))
        if visible_subject_count == 0 or bool(target.get("no_subject_detected")):
            invisible_streak += 1
            if recoverable_subject and current_track_id not in {None, "scene_interest"} and bool(empty_frame_guard_enabled):
                candidate = current
                candidate_role = current_role
                candidate_track_id = current_track_id
                lock_state = "lost_face_recover"
                candidate_switch_score = current_switch_score
                invisible_streak = min(invisible_streak, 1)
            elif invisible_streak <= face_lock_windows and current_track_id not in {None, "scene_interest"} and bool(empty_frame_guard_enabled):
                candidate = current
                lock_state = "lost_face_recover"
                candidate_switch_score = current_switch_score
            else:
                listener_center = target.get("listener_candidate_center")
                subject_center = target.get("subject_candidate_center")
                if listener_center is not None and bool(empty_frame_guard_enabled):
                    candidate = listener_center
                    candidate_role = "listener"
                    lock_state = "listener_hold"
                    candidate_switch_score = max(candidate_switch_score, float(target.get("listener_confidence", 0.0)) * 0.9)
                elif subject_center is not None and bool(empty_frame_guard_enabled):
                    candidate = subject_center
                    candidate_role = "subject_person"
                    lock_state = "subject_person_hold"
                    candidate_switch_score = max(candidate_switch_score, float(target.get("subject_confidence", 0.0)) * 0.92)
                else:
                    if recoverable_subject and bool(empty_frame_guard_enabled):
                        candidate = current
                        candidate_role = current_role
                        candidate_track_id = current_track_id
                        candidate_switch_score = current_switch_score
                        lock_state = "lost_face_recover"
                    pending = None
                    pending_count = 0
                    if lock_state != "lost_face_recover":
                        lock_state = "scene_interest_fallback" if candidate_track_id == "scene_interest" else "lost_face_recover"
        else:
            invisible_streak = 0
            if (
                candidate_role == "speaker"
                and target.get("speaker_candidate_center") is not None
                and target.get("speaker_candidate_track_id") is not None
            ):
                candidate = target.get("speaker_candidate_center")
                candidate_track_id = target.get("speaker_candidate_track_id")
            visible_enough = bool(
                target["strength"] >= switch_min_visibility * 0.12
                or visible_subject_count >= 1
                or float(target.get("speaker_confidence", 0.0)) >= 0.48
            )
            track_changed = _track_key(candidate_track_id) != _track_key(current_track_id)
            fast_reacquire_candidate = bool(target.get("fast_reacquire_candidate"))
            
            # PHASE 3C: Turn-first switching - subtitle_turn_changed is PRIMARY trigger
            subtitle_turn_changed = bool(target.get("subtitle_turn_changed", False))
            active_turn_speaker = target.get("active_turn_speaker")
            
            # PHASE 3C: Track turn speaker changes for hold/cooldown logic
            if subtitle_turn_changed and active_turn_speaker:
                forced_turn_switches += 1
                speaker_hold_counter = 0  # Reset hold on turn boundary
                speaker_switch_cooldown = 0  # Turn boundary bypasses cooldown
                last_turn_speaker = active_turn_speaker
            
            # PHASE 3C: Decrement cooldown counter
            if speaker_switch_cooldown > 0:
                speaker_switch_cooldown -= 1
            
            if candidate_track_id == "scene_interest" and (
                current_track_id not in {None, "scene_interest"} or recoverable_subject
            ):
                visible_enough = False
            score_margin_ok = candidate_switch_score >= (current_switch_score + strict_switch_margin)
            should_switch = visible_enough and (track_changed and score_margin_ok or shift > hysteresis and score_margin_ok)
            
            # PHASE 3C: strong_turn_switch now uses subtitle_turn_changed as PRIMARY
            strong_turn_switch = (
                candidate_role == "speaker"
                and subtitle_turn_changed
                and float(target.get("speaker_turn_strength", 0.0)) >= 0.20
            )
            
            # PHASE 5: Turn boundary becomes UNCONDITIONAL authority
            # Confidence gates CANNOT block legitimate turn switches
            if subtitle_turn_changed and candidate_role == "speaker":
                should_switch = True  # Force evaluation on turn boundary
                score_margin_ok = True  # PHASE 5: BYPASS confidence gate on turn
                required_hold = 0  # PHASE 5: INSTANT switch (was 1)
            elif speaker_switch_cooldown > 0 and track_changed and not strong_turn_switch:
                # Cooldown blocks non-turn switches
                cooldown_blocked_switches += 1
                should_switch = False
            hard_switch_candidate = bool(
                strict_center
                and candidate_role == "speaker"
                and track_changed
                and visible_enough
                and score_margin_ok
                and not speaker_face_edge_clipped
                and (
                    strong_turn_switch
                    or fast_reacquire_candidate
                    or float(target.get("speaker_confidence", 0.0)) >= max(
                        float(new_face_fast_acquire_threshold) * 0.92,
                        float(speaker_confidence_threshold) * 0.96,
                    )
                )
            )
            if hard_switch_candidate:
                current = candidate
                current_track_id = candidate_track_id
                current_role = "speaker"
                current_switch_score = candidate_switch_score
                role_hold_counter = 0
                scene_hold_counter = 0
                lock_state = "speaker_locked"
                hard_switch_windows += 1
                if not confident_lock and handoff_glide_windows > 0:
                    handoff_glide_windows -= 1
                handoff_mode = "hard_switch"
                switch_latency_windows += 0
                if fast_reacquire_candidate:
                    fast_reacquire_success += 1
                    new_face_acquire_count += 1
                    reacquire_boost_counter = max(0, reacquire_boost_counter - 1)
                    reacquire_cooldown = max(reacquire_cooldown, 1 if strict_center else 0)
                pending = None
                pending_count = 0
                candidate_track_id = current_track_id
                candidate_role = current_role
                candidate_switch_score = current_switch_score
                should_switch = False
                accent_hold_active = False
            if scene_hold_counter > 0 and not strong_turn_switch:
                scene_hold_counter -= 1
                should_switch = False
            if priority == "stability_first" and current_role == "speaker" and role_hold_counter < speaker_hold_windows:
                should_switch = should_switch and (candidate_role in {"speaker", "dialogue_center", "subject_person"} or strong_turn_switch)
            if priority == "stability_first" and candidate_role == "listener" and current_role == "speaker":
                listener_conf = float(target.get("listener_confidence", 0.0))
                speaker_conf = float(target.get("speaker_confidence", 0.0))
                should_switch = should_switch and float(target.get("dialogue_likelihood", 0.0)) >= 0.45
                if strict_lock or strict_center:
                    should_switch = should_switch and speaker_conf < 0.42 and listener_conf >= speaker_conf + balance_margin
            if priority == "stability_first" and candidate_role == "subject_person" and current_role == "speaker":
                speaker_conf = float(target.get("speaker_confidence", 0.0))
                should_switch = should_switch and speaker_conf < 0.46
            if priority == "stability_first" and candidate_track_id == "dialogue_center" and current_track_id not in {None, "dialogue_center"}:
                should_switch = should_switch and shift > (hysteresis * 0.9)
                if strict_lock or strict_center:
                    should_switch = should_switch and False
            required_hold = max(hold_windows, required_hold_floor)
            if accent_hold_active:
                required_hold = max(required_hold, accent_frame_hold_windows)
            if strong_turn_switch:
                required_hold = max(1, hold_windows - 1)
                score_margin_ok = candidate_switch_score >= (current_switch_score + float(switch_score_margin) * 0.65)
                should_switch = should_switch or (visible_enough and score_margin_ok)
            if candidate_role == "listener" and current_role == "speaker" and invisible_streak > 0:
                required_hold = 1
            if scene_change_detected and confident_lock:
                required_hold = max(1, required_hold - 1)
            if (
                fast_reacquire_candidate
                and reacquire_boost_counter > 0
                and track_changed
                and visible_enough
            ):
                fast_reacquire_attempted += 1
                score_margin_ok = candidate_switch_score >= (current_switch_score - 0.02)
                required_hold = 1
                if candidate_role == "speaker":
                    should_switch = should_switch or (
                        speaker_confidence >= float(new_face_fast_acquire_threshold) * 0.88
                        and not speaker_face_edge_clipped
                        and score_margin_ok
                    )
                elif candidate_role == "dialogue_center":
                    should_switch = should_switch or (
                        float(target.get("dialogue_likelihood", 0.0)) >= max(0.44, float(dialogue_center_use_threshold) * 0.84)
                        and abs(float(target.get("speaker_confidence", 0.0)) - float(target.get("listener_confidence", 0.0))) <= balance_margin
                        and score_margin_ok
                    )
                elif candidate_role == "listener":
                    should_switch = should_switch or (
                        float(target.get("listener_confidence", 0.0)) >= 0.42
                        and (not strict_lock and not strict_center or float(target.get("speaker_confidence", 0.0)) < 0.50)
                        and score_margin_ok
                    )
            if should_switch:
                if pending and abs(candidate[0] - pending[0]) + abs(candidate[1] - pending[1]) < 0.06:
                    pending_count += 1
                else:
                    pending = candidate
                    pending_count = 1
                if pending_count >= required_hold:
                    if strict_center and (candidate_role == "speaker" or confident_lock or fast_reacquire_candidate or strong_turn_switch):
                        current = pending
                        if candidate_role == "speaker" or strong_turn_switch:
                            hard_switch_windows += 1
                    else:
                        current = _blend_towards(
                            current,
                            pending,
                            blend=min(0.50, float(blend_switch_local) * (1.08 if scene_change_detected else 1.0)),
                            max_delta=max_delta_local,
                        )
                    current_track_id = candidate_track_id if candidate_track_id is not None else current_track_id
                    current_role = candidate_role
                    current_switch_score = candidate_switch_score
                    role_hold_counter = 0
                    scene_hold_counter = 0 if strict_center or hard_switch_mode else max(scene_recenter_hold_windows, 5 if candidate_role == "dialogue_center" else scene_recenter_hold_windows)
                    if scene_change_detected and not hard_switch_mode:
                        scene_recenter_count += 1
                    if fast_reacquire_candidate and track_changed:
                        fast_reacquire_success += 1
                        new_face_acquire_count += 1
                        reacquire_boost_counter = max(0, reacquire_boost_counter - 1)
                        reacquire_cooldown = max(reacquire_cooldown, 1 if strict_center or hard_switch_mode else 2)
                    switch_latency_windows += max(0, pending_count - 1)
                    lock_state = (
                        "dialogue_center"
                        if candidate_role == "dialogue_center"
                        else "listener_hold"
                        if candidate_role == "listener"
                        else "subject_person_hold"
                        if candidate_role == "subject_person"
                        else "speaker_locked"
                    )
                    pending = None
                    pending_count = 0
            else:
                pending = None
                pending_count = 0
                if reacquire_cooldown > 0 and track_changed and not strong_turn_switch:
                    candidate = current
                    candidate_track_id = current_track_id
                    candidate_role = current_role
                    candidate_switch_score = current_switch_score
                if strict_center and candidate_role == "speaker" and track_changed:
                    strong_strict_turn = (
                        scene_change_detected
                        or fast_reacquire_candidate
                        or float(target.get("speaker_turn_strength", 0.0)) >= 0.22
                    )
                    if strong_strict_turn and score_margin_ok:
                        should_switch = True
                        required_hold = 1
                if scene_hold_counter > 0 and not scene_change_detected:
                    pass
                elif abs(shift) <= target_deadband_local and candidate_track_id == current_track_id:
                    pass
                elif candidate_track_id != current_track_id and scene_change_detected:
                    if strict_center and (candidate_role == "speaker" or confident_lock or fast_reacquire_candidate or strong_turn_switch):
                        current = candidate
                        current_track_id = candidate_track_id
                        current_role = candidate_role
                        current_switch_score = candidate_switch_score
                        if candidate_role == "speaker" or strong_turn_switch:
                            hard_switch_windows += 1
                    else:
                        current = _blend_towards(
                            current,
                            candidate,
                            blend=max(0.18, float(blend_switch_local) * 0.9),
                            max_delta=max_delta_local * 0.9,
                        )
                        current_switch_score = max(current_switch_score * 0.92, candidate_switch_score * 0.90)
                if candidate_role == "dialogue_center":
                    lock_state = "dialogue_center"
                elif candidate_role == "listener" and role_hold_counter >= listener_hold_windows:
                    lock_state = "listener_hold"
                elif candidate_role == "subject_person":
                    lock_state = "subject_person_hold"
                else:
                    lock_state = "speaker_locked"
        if not strict_center and invisible_streak >= max(hold_windows, int(round(max(1.0, lost_face_hold_seconds)))):
            current = _blend_towards(current, candidate, blend=max(0.08, float(blend_normal_local) * 0.55), max_delta=max_delta_local * 0.5)
        if reacquire_boost_counter > 0 and not scene_change_detected:
            reacquire_boost_counter -= 1
        if reacquire_cooldown > 0:
            reacquire_cooldown -= 1
        role_hold_counter += 1
        state_usage[lock_state] = state_usage.get(lock_state, 0) + 1
        resolved.append(
            {
                "start": target["start"],
                "end": target["end"],
                "center": current,
                "track_id": current_track_id,
                "state": lock_state,
                "role": current_role,
                "handoff_mode": handoff_mode,
                "confident_lock": bool(confident_lock),
                "speaker_candidate_center": target.get("speaker_candidate_center"),
                "speaker_candidate_track_id": target.get("speaker_candidate_track_id"),
                "listener_candidate_center": target.get("listener_candidate_center"),
                "subject_candidate_center": target.get("subject_candidate_center"),
                "speaker_face_edge_clipped": bool(target.get("speaker_face_edge_clipped", False)),
                "speaker_confidence": float(target.get("speaker_confidence", 0.0) or 0.0),
                "listener_confidence": float(target.get("listener_confidence", 0.0) or 0.0),
                "dialogue_likelihood": float(target.get("dialogue_likelihood", 0.0) or 0.0),
                "subject_visible": bool(target.get("subject_visible", False)),
                "no_subject_detected": bool(target.get("no_subject_detected", False)),
                "recoverable_subject": bool(target.get("recoverable_subject", False)),
                "face_hold_available": bool(target.get("face_hold_available", False)),
                "scene_change_detected": bool(target.get("scene_change_detected", False)),
            }
        )
    state_usage["scene_recenter_count"] = int(scene_recenter_count)
    state_usage["confident_lock_windows"] = int(confident_lock_windows)
    state_usage["handoff_glide_windows"] = int(handoff_glide_windows)
    state_usage["hard_switch_windows"] = int(hard_switch_windows)
    state_usage["switch_latency_windows"] = int(switch_latency_windows)
    state_usage["fast_reacquire_attempted"] = int(fast_reacquire_attempted)
    state_usage["fast_reacquire_success"] = int(fast_reacquire_success)
    state_usage["new_face_acquire_count"] = int(new_face_acquire_count)
    state_usage["speaker_transition_direct_windows"] = int(hard_switch_windows)
    
    # PHASE 3C: Turn-first metrics
    state_usage["forced_turn_switches"] = int(forced_turn_switches)
    state_usage["cooldown_blocked_switches"] = int(cooldown_blocked_switches)
    state_usage["turn_first_enabled"] = bool(forced_turn_switches > 0 or cooldown_blocked_switches > 0)
    
    return resolved, state_usage


def _apply_camera_glide_plan(resolved, glide_windows=2):
    glide_windows = max(1, int(glide_windows or 1))
    if glide_windows <= 1 or len(resolved) <= 1:
        return resolved
    planned = [dict(item) for item in resolved]
    for index in range(1, len(planned)):
        prev = planned[index - 1]
        current = planned[index]
        prev_key = (_track_key(prev.get("track_id")), str(prev.get("role", "")))
        current_key = (_track_key(current.get("track_id")), str(current.get("role", "")))
        if prev_key == current_key:
            continue
        span = min(glide_windows, len(planned) - index)
        if span <= 0:
            continue
        start_center = tuple(prev.get("center", (0.5, 0.5)))
        end_center = tuple(current.get("center", start_center))
        for offset in range(span):
            item = planned[index + offset]
            item_center = tuple(item.get("center", end_center))
            alpha = float(offset + 1) / float(span + 1)
            blend_center = (
                start_center[0] * (1.0 - alpha) + item_center[0] * alpha,
                start_center[1] * (1.0 - alpha) + item_center[1] * alpha,
            )
            item["center"] = (_clamp(blend_center[0], 0.0, 1.0), _clamp(blend_center[1], 0.0, 1.0))
    return planned


def _merge_reframe_windows(windows, max_center_delta=0.02, max_role_change_gap=0):
    if not windows:
        return windows
    merged = [dict(windows[0])]
    for window in windows[1:]:
        prev = merged[-1]
        prev_key = (
            _track_key(prev.get("track_id")),
            str(prev.get("role", "")),
            str(prev.get("state", "")),
        )
        current_key = (
            _track_key(window.get("track_id")),
            str(window.get("role", "")),
            str(window.get("state", "")),
        )
        prev_center = tuple(prev.get("center", (0.5, 0.5)))
        current_center = tuple(window.get("center", prev_center))
        center_delta = abs(current_center[0] - prev_center[0]) + abs(current_center[1] - prev_center[1])
        contiguous = abs(float(window.get("start", 0.0)) - float(prev.get("end", 0.0))) <= 1e-3
        if contiguous and prev_key == current_key and center_delta <= float(max_center_delta):
            prev["end"] = window.get("end", prev["end"])
            span = max(0.001, float(prev["end"]) - float(prev["start"]))
            prev_weight = max(0.001, float(prev.get("span_weight", float(prev.get("end", 0.0)) - float(prev.get("start", 0.0)) or 0.001)))
            curr_weight = max(0.001, float(window.get("end", 0.0)) - float(window.get("start", 0.0)))
            total = prev_weight + curr_weight
            prev["center"] = (
                (prev_center[0] * prev_weight + current_center[0] * curr_weight) / total,
                (prev_center[1] * prev_weight + current_center[1] * curr_weight) / total,
            )
            prev["span_weight"] = total
            prev["confident_lock"] = bool(prev.get("confident_lock")) or bool(window.get("confident_lock"))
            prev["track_id"] = window.get("track_id", prev.get("track_id"))
            prev["role"] = window.get("role", prev.get("role"))
            prev["state"] = window.get("state", prev.get("state"))
            prev["handoff_mode"] = window.get("handoff_mode", prev.get("handoff_mode"))
            continue
        merged.append(dict(window))
    for item in merged:
        item.pop("span_weight", None)
    return merged


def _strict_face_pass_needed(debug, strong_subject_pass=False):
    return bool(
        strong_subject_pass
        or float(debug.get("subject_visibility_ratio", 0.0) or 0.0) < 0.45
        or int(debug.get("evidence_visible_faces_peak", 0) or 0) <= 0
        or int(debug.get("speaker_face_centered_windows", 0) or 0) <= 0
    )


def _subject_acquisition_status(tracks):
    detected_tracks = [item for item in tracks if item.get("detected")]
    face_presence = len(detected_tracks) / max(1, len(tracks))
    person_presence = sum(1 for item in tracks if item.get("persons")) / max(1, len(tracks))
    visible_faces_peak = max(
        [sum(1 for face in (item.get("faces", []) or []) if face.get("detected")) for item in tracks] or [0]
    )
    visible_persons_peak = max([len(item.get("persons", []) or []) for item in tracks] or [0])
    recent_face_memory_peak = max(
        [int((item.get("speaker_evidence_summary") or {}).get("recent_face_memory_count", 0) or 0) for item in tracks] or [0]
    )
    subject_presence = max(face_presence, person_presence * 0.88)
    if visible_faces_peak <= 0 and visible_persons_peak <= 0:
        state = "speaker_lock_uncertain" if (face_presence > 0.0 or person_presence > 0.0 or recent_face_memory_peak > 0) else "no_visible_subject"
    elif face_presence >= 0.18 or visible_faces_peak >= 2 or subject_presence >= 0.30:
        state = "speaker_lock_ready"
    else:
        state = "speaker_lock_uncertain"
    return {
        "state": state,
        "face_presence": round(face_presence, 4),
        "person_presence": round(person_presence, 4),
        "subject_presence": round(subject_presence, 4),
        "visible_faces_peak": int(visible_faces_peak),
        "visible_persons_peak": int(visible_persons_peak),
        "recent_face_memory_peak": int(recent_face_memory_peak),
    }


def create_vertical_crop(
    video_path,
    start,
    end,
    out_path,
    subtitle_segments=None,  # PHASE 3C: Turn-first speaker switching
    target_w=720,
    target_h=1280,
    use_active_speaker=True,
    reframe_mode="balanced",
    reframe_transition_mode="smooth",
    reframe_anchor_mode="stable_primary",
    reframe_subject_mode="subject_first",
    window_sec=0.8,
    sample_fps=3,
    speaker_switch_hold_windows=2,
    accent_frame_hold_windows=0,
    reframe_switch_min_visibility=0.38,
    reframe_allow_wide_dialogue_center=True,
    reframe_track_count_limit=3,
    reframe_dual_face_margin=0.14,
    reframe_lost_face_hold_seconds=1.5,
    reframe_scene_interest_fallback=False,
    scene_interest_fallback_mode="normal",
    reframe_listener_face_fallback=False,
    dialogue_two_shot_preferred=True,
    reframe_priority="stability_first",
    speaker_lock_mode="state_machine",
    speaker_lock_strict_mode=False,
    speaker_center_strict_mode=False,
    speaker_center_max_offset=0.16,
    speaker_face_lock_min_margin=0.12,
    dialogue_center_use_threshold=0.70,
    listener_fallback_max_hold_seconds=0.65,
    listener_fallback_speech_hold_max_seconds=0.40,
    speaker_min_hold_seconds=0.9,
    listener_hold_seconds=0.55,
    dialogue_center_min_likelihood=0.56,
    dialogue_center_balance_margin=0.08,
    empty_frame_guard_enabled=True,
    force_center_crop=False,
    force_face_preserving_crop=False,
    face_preserving_anchor_center=None,
    face_preserving_face_size=0.0,
    face_preserving_safe_margin=0.12,
    max_crop_delta_per_window=0.05,
    motion_blend_normal=0.2,
    motion_blend_switch=0.32,
    reframe_glide_windows=1,
    framing_mode="face_locked",
    switch_score_margin=0.08,
    subject_confidence_floor=0.42,
    subject_visibility_threshold=0.46,
    scene_recenter_hold_windows=2,
    lock_confidence_threshold=0.72,
    speaker_confidence_threshold=0.62,
    handoff_min_hold_windows=2,
    confident_lock_min_hold_windows=4,
    target_deadband_handoff=0.028,
    target_deadband_lock=0.018,
    max_delta_handoff=0.028,
    max_delta_lock=0.020,
    motion_blend_switch_handoff=0.22,
    motion_blend_normal_handoff=0.14,
    subject_detector_pass="light",
    shot_reacquire_boost_windows=2,
    new_face_fast_acquire_threshold=0.78,
    debug_info=None,
    progress_callback=None,
):
    """Create a robust 9:16 crop with windowed smart reframe and center fallback."""
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration
        source_w = clip.w
        source_h = clip.h
        clip.close()
    except Exception:
        return False

    start_t = max(0.0, float(start or 0.0))
    end_t = min(duration, float(end) if end is not None else duration)
    if end_t <= start_t:
        return False

    def _write_center_crop(output_path: str, reason: str) -> bool:
        crop_w, crop_h = _center_crop_geometry(source_w, source_h, target_w, target_h)
        crop_x = max(0, (source_w - crop_w) // 2)
        crop_y = max(0, (source_h - crop_h) // 2)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(round(start_t, 3)),
            "-to",
            str(round(end_t, 3)),
            "-i",
            video_path,
            "-vf",
            f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-ac",
            "2",
            "-c:a",
            "aac",
            output_path,
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            debug = debug_info if isinstance(debug_info, dict) else {}
            debug["center_safe_fallback_used"] = True
            debug["center_safe_fallback_reason"] = reason
            debug["center_safe_fallback_mode"] = "center_crop"
            debug["framing_mode"] = "center_safe"
            debug["reframe_transition_mode"] = "center_safe"
            debug["reframe_anchor_mode"] = "center_safe"
            debug["subject_mode"] = "safe_center"
            debug["subject_acquisition_state"] = "no_visible_subject" if reason == "no_visible_subject" else "center_safe_fallback"
            debug["subject_acquisition_dense_scan_used"] = bool(debug.get("subject_acquisition_dense_scan_used", False))
            debug["subject_visibility_ratio"] = 0.0
            debug["speaker_centered_rate"] = 0.0
            debug["speaker_face_centered_windows"] = 0
            debug["dialogue_center_windows"] = 0
            debug["listener_fallback_windows"] = 0
            debug["subject_person_fallback_windows"] = 0
            debug["anchor_switches"] = 0
            debug["handoff_glide_windows"] = 0
            debug["face_preserving_fallback_used"] = False
            debug["face_preserving_fallback_reason"] = ""
            debug["subject_acquisition_outcome"] = "center_safe_fallback"
            return True
        return False

    def _write_face_preserving_crop(
        output_path: str,
        reason: str,
        anchor_center=None,
        face_size=None,
    ) -> bool:
        crop_w, crop_h = _center_crop_geometry(source_w, source_h, target_w, target_h)
        anchor_x, anchor_y = anchor_center if isinstance(anchor_center, (list, tuple)) and len(anchor_center) >= 2 else (0.5, 0.5)
        anchor_x = _clamp(float(anchor_x), 0.0, 1.0)
        anchor_y = _clamp(float(anchor_y), 0.0, 1.0)
        face_size = max(0.0, float(face_size or 0.0))
        safe_margin_scale = max(0.06, float(face_preserving_safe_margin))
        face_margin_x = int(round(max(crop_w * 0.10, source_w * max(safe_margin_scale, face_size * 0.85))))
        face_margin_y = int(round(max(crop_h * 0.08, source_h * max(safe_margin_scale, face_size * 1.05))))
        face_center_x = anchor_x * source_w
        face_center_y = anchor_y * source_h
        crop_x = _safe_face_preserving_offset(face_center_x, crop_w, source_w, face_margin_x)
        crop_y = _safe_face_preserving_offset(face_center_y, crop_h, source_h, face_margin_y)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(round(start_t, 3)),
            "-to",
            str(round(end_t, 3)),
            "-i",
            video_path,
            "-vf",
            f"crop={crop_w}:{crop_h}:{int(crop_x)}:{int(crop_y)},scale={target_w}:{target_h}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-ac",
            "2",
            "-c:a",
            "aac",
            output_path,
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            debug = debug_info if isinstance(debug_info, dict) else {}
            debug["face_preserving_fallback_used"] = True
            debug["face_preserving_fallback_reason"] = reason
            debug["face_preserving_fallback_mode"] = "face_preserving_crop"
            debug["face_safe_margin_applied"] = True
            debug["face_preserving_anchor_x"] = round(anchor_x, 4)
            debug["face_preserving_anchor_y"] = round(anchor_y, 4)
            debug["face_preserving_face_size"] = round(face_size, 4)
            debug["center_safe_fallback_used"] = False
            debug["center_safe_fallback_reason"] = ""
            debug["framing_mode"] = "face_preserving"
            debug["reframe_transition_mode"] = "face_preserving"
            debug["reframe_anchor_mode"] = "face_preserving"
            debug["subject_mode"] = "face_preserving"
            debug["subject_acquisition_outcome"] = "face_preserving_fallback"
            return True
        return False

    if bool(force_center_crop):
        return _write_center_crop(out_path, "forced_center_crop")
    if bool(force_face_preserving_crop) and face_preserving_anchor_center is not None:
        if _write_face_preserving_crop(
            out_path,
            "forced_face_preserving_fallback",
            anchor_center=face_preserving_anchor_center,
            face_size=face_preserving_face_size,
        ):
            debug = debug_info if isinstance(debug_info, dict) else {}
            debug["subject_acquisition_state"] = "speaker_lock_uncertain"
            debug["subject_acquisition_outcome"] = "face_preserving_fallback"
            debug["subject_acquisition_source_anchor_used"] = True
            return True

    framing = str(framing_mode or "face_locked").lower()
    if framing == "context_blur":
        framing = "context_padded"
    if framing == "human_handoff":
        framing = "shot_lock"
    if framing == "center_safe":
        framing = "face_locked"
    square_canvas_mode = framing == "square_canvas"
    if bool(speaker_center_strict_mode) and not square_canvas_mode:
        framing = "face_locked"
        if reframe_anchor_mode == "dialogue_center":
            reframe_anchor_mode = "stable_primary"
        reframe_allow_wide_dialogue_center = False
        dialogue_two_shot_preferred = False
        speaker_switch_hold_windows = 0 if str(reframe_transition_mode or "").lower() in {"hard_switch", "strict_switch"} else max(1, min(int(speaker_switch_hold_windows), 2))
    if framing == "face_locked" and not square_canvas_mode:
        max_crop_delta_per_window = min(float(max_crop_delta_per_window), 0.025)
        motion_blend_normal = min(float(motion_blend_normal), 0.12)
        motion_blend_switch = min(float(motion_blend_switch), 0.18)
        if not bool(speaker_center_strict_mode):
            speaker_switch_hold_windows = max(int(speaker_switch_hold_windows), 4)
        sample_fps = max(int(sample_fps), 4)
    if framing in {"shot_lock", "scene_lock"} and not square_canvas_mode:
        max_crop_delta_per_window = min(float(max_crop_delta_per_window), 0.018)
        motion_blend_normal = min(float(motion_blend_normal), 0.10)
        motion_blend_switch = min(float(motion_blend_switch), 0.14)
        speaker_switch_hold_windows = max(int(speaker_switch_hold_windows), 5)
        scene_recenter_hold_windows = max(int(scene_recenter_hold_windows), 5)
        sample_fps = max(int(sample_fps), 4)
    if framing == "wide_subject" and not square_canvas_mode:
        scene_recenter_hold_windows = max(int(scene_recenter_hold_windows), 4)
    elif framing == "dialogue_dual" and not square_canvas_mode:
        scene_recenter_hold_windows = max(int(scene_recenter_hold_windows), 4)
    if bool(speaker_center_strict_mode):
        sample_fps = max(int(sample_fps), 8)
        window_sec = min(float(window_sec), 0.24 if end_t - start_t < 30.0 else 0.28)
    if square_canvas_mode:
        sample_fps = max(int(sample_fps), 6)
        window_sec = min(float(window_sec), 0.28)

    debug = debug_info if isinstance(debug_info, dict) else {}
    debug["reframe_transition_mode"] = str(reframe_transition_mode)
    debug["reframe_anchor_mode"] = str(reframe_anchor_mode)
    effective_listener_hold_seconds = min(float(listener_hold_seconds), float(listener_fallback_max_hold_seconds))
    # PHASE 5: Use cached face detection for overlapping candidates
    tracks = (
        estimate_face_tracks_cached(
            video_path,
            start_t,
            end_t,
            sample_fps=sample_fps,
            detector_profile=subject_detector_pass,
        )
        if use_active_speaker
        else []
    )
    
    # PHASE 3C: Build turn timeline for turn-first speaker switching
    turn_timeline = _build_turn_timeline(subtitle_segments, start_t, end_t) if subtitle_segments else []
    
    acquisition = _subject_acquisition_status(tracks)
    acquisition["scan_profile"] = str(subject_detector_pass or "light")
    acquisition["dense_scan_used"] = False
    acquisition_rescue_used = False
    if use_active_speaker and bool(speaker_center_strict_mode):
        if _strict_face_pass_needed(
            {
                "subject_visibility_ratio": acquisition["subject_presence"],
                "evidence_visible_faces_peak": acquisition["visible_faces_peak"],
                "speaker_face_centered_windows": 0,
            },
            strong_subject_pass=str(subject_detector_pass or "").lower() in {"strong", "final_clip_strong", "refine"},
        ):
            dense_sample_fps = max(int(sample_fps), 8 if acquisition["state"] != "no_visible_subject" else 10)
            # PHASE 5: Use cached face detection for dense scan pass
            strong_tracks = estimate_face_tracks_cached(
                video_path,
                start_t,
                end_t,
                sample_fps=dense_sample_fps,
                detector_profile="final_clip_strong",
            )
            strong_acquisition = _subject_acquisition_status(strong_tracks)
            strong_acquisition["scan_profile"] = "final_clip_strong"
            strong_acquisition["dense_scan_used"] = True
            if (
                strong_acquisition["visible_faces_peak"] > acquisition["visible_faces_peak"]
                or strong_acquisition["visible_persons_peak"] > acquisition["visible_persons_peak"]
                or strong_acquisition["face_presence"] >= acquisition["face_presence"] + 0.05
                or strong_acquisition["subject_presence"] >= acquisition["subject_presence"] + 0.05
            ):
                tracks = strong_tracks
                acquisition = strong_acquisition
        if acquisition["state"] == "no_visible_subject":
            rescue_sample_fps = max(int(sample_fps), 12)
            rescue_tracks = estimate_face_tracks(
                video_path,
                start_t,
                end_t,
                sample_fps=rescue_sample_fps,
                detector_profile="refine",
            )
            rescue_acquisition = _subject_acquisition_status(rescue_tracks)
            rescue_acquisition["scan_profile"] = "refine"
            rescue_acquisition["dense_scan_used"] = True
            if (
                rescue_acquisition["visible_faces_peak"] > 0
                or rescue_acquisition["visible_persons_peak"] > 0
                or rescue_acquisition["recent_face_memory_peak"] > 0
                or rescue_acquisition["face_presence"] > 0.02
                or rescue_acquisition["person_presence"] > 0.02
                or rescue_acquisition["subject_presence"] > 0.04
            ):
                tracks = rescue_tracks
                acquisition = rescue_acquisition
                acquisition["state"] = "speaker_lock_uncertain"
                acquisition_rescue_used = True
        if acquisition["state"] == "no_visible_subject":
            debug["subject_acquisition_state"] = acquisition["state"]
            debug["subject_acquisition_dense_scan_used"] = bool(acquisition.get("dense_scan_used", False))
            debug["subject_acquisition_scan_profile"] = acquisition.get("scan_profile", "light")
            debug["subject_acquisition_face_presence"] = float(acquisition.get("face_presence", 0.0) or 0.0)
            debug["subject_acquisition_person_presence"] = float(acquisition.get("person_presence", 0.0) or 0.0)
            debug["subject_acquisition_subject_presence"] = float(acquisition.get("subject_presence", 0.0) or 0.0)
            debug["subject_acquisition_visible_faces_peak"] = int(acquisition.get("visible_faces_peak", 0) or 0)
            debug["subject_acquisition_visible_persons_peak"] = int(acquisition.get("visible_persons_peak", 0) or 0)
            debug["subject_acquisition_recent_face_memory_peak"] = int(acquisition.get("recent_face_memory_peak", 0) or 0)
            debug["subject_acquisition_rescue_used"] = bool(acquisition_rescue_used)
            return _write_center_crop(out_path, "no_visible_subject")
        if acquisition["state"] == "speaker_lock_uncertain":
            sample_fps = max(int(sample_fps), 8)
            window_sec = min(float(window_sec), 0.22)
        if bool(force_face_preserving_crop) and acquisition["state"] != "no_visible_subject" and not square_canvas_mode:
            face_candidates = _visible_faces(tracks, track_limit=max(1, int(reframe_track_count_limit)))
            if face_candidates:
                best_face = max(face_candidates, key=_speaker_priority)
                if face_preserving_anchor_center is None:
                    face_preserving_anchor_center = (best_face["center_x"], best_face["center_y"])
                if not face_preserving_face_size:
                    face_preserving_face_size = float(best_face["box_w"] * best_face["box_h"])
            elif tracks and face_preserving_anchor_center is None:
                face_preserving_anchor_center = _pick_center(tracks, reframe_mode)[0]
            if _write_face_preserving_crop(
                out_path,
                "face_preserving_fallback",
                anchor_center=face_preserving_anchor_center,
                face_size=face_preserving_face_size,
            ):
                debug["subject_acquisition_state"] = acquisition["state"]
                debug["subject_acquisition_dense_scan_used"] = bool(acquisition.get("dense_scan_used", False))
                debug["subject_acquisition_scan_profile"] = acquisition.get("scan_profile", "light")
                debug["subject_acquisition_face_presence"] = float(acquisition.get("face_presence", 0.0) or 0.0)
                debug["subject_acquisition_person_presence"] = float(acquisition.get("person_presence", 0.0) or 0.0)
                debug["subject_acquisition_subject_presence"] = float(acquisition.get("subject_presence", 0.0) or 0.0)
                debug["subject_acquisition_visible_faces_peak"] = int(acquisition.get("visible_faces_peak", 0) or 0)
                debug["subject_acquisition_visible_persons_peak"] = int(acquisition.get("visible_persons_peak", 0) or 0)
                debug["subject_acquisition_recent_face_memory_peak"] = int(acquisition.get("recent_face_memory_peak", 0) or 0)
                debug["subject_acquisition_rescue_used"] = bool(acquisition_rescue_used)
                debug["face_preserving_fallback_used"] = True
                debug["face_preserving_fallback_reason"] = "forced_face_preserving_crop"
                debug["subject_acquisition_outcome"] = "face_preserving_fallback"
                return True
            return False
    debug["subject_acquisition_state"] = acquisition["state"]
    debug["subject_acquisition_dense_scan_used"] = bool(acquisition.get("dense_scan_used", False))
    debug["subject_acquisition_scan_profile"] = acquisition.get("scan_profile", "light")
    debug["subject_acquisition_face_presence"] = float(acquisition.get("face_presence", 0.0) or 0.0)
    debug["subject_acquisition_person_presence"] = float(acquisition.get("person_presence", 0.0) or 0.0)
    debug["subject_acquisition_subject_presence"] = float(acquisition.get("subject_presence", 0.0) or 0.0)
    debug["subject_acquisition_visible_faces_peak"] = int(acquisition.get("visible_faces_peak", 0) or 0)
    debug["subject_acquisition_visible_persons_peak"] = int(acquisition.get("visible_persons_peak", 0) or 0)
    debug["subject_acquisition_recent_face_memory_peak"] = int(acquisition.get("recent_face_memory_peak", 0) or 0)
    debug["subject_acquisition_rescue_used"] = bool(acquisition_rescue_used)
    debug["face_preserving_fallback_used"] = bool(debug.get("face_preserving_fallback_used", False))
    debug["face_preserving_fallback_reason"] = str(debug.get("face_preserving_fallback_reason", ""))
    debug["face_safe_margin_applied"] = bool(debug.get("face_safe_margin_applied", False))
    debug["subject_acquisition_outcome"] = str(debug.get("subject_acquisition_outcome", acquisition["state"]))
    debug["square_reframe_mode_used"] = bool(square_canvas_mode)
    if not bool(debug.get("face_preserving_fallback_used", False)) and not bool(debug.get("center_safe_fallback_used", False)):
        debug["subject_acquisition_outcome"] = "strict_face_success" if acquisition["state"] != "no_visible_subject" else "no_visible_subject"
    if acquisition["state"] == "no_visible_subject" and bool(speaker_center_strict_mode):
        return _write_center_crop(out_path, "no_visible_subject")

    targets = _build_window_targets(
        tracks,
        start_t,
        end_t,
        window_sec,
        reframe_mode,
        turn_timeline=turn_timeline,
        anchor_mode=reframe_anchor_mode,
        allow_wide_dialogue_center=bool(reframe_allow_wide_dialogue_center),
        track_limit=int(reframe_track_count_limit),
        dual_face_margin=float(reframe_dual_face_margin),
        scene_interest_fallback=bool(reframe_scene_interest_fallback),
        scene_interest_fallback_mode=str(scene_interest_fallback_mode or "normal"),
        listener_face_fallback=bool(reframe_listener_face_fallback),
        speaker_lock_strict_mode=bool(speaker_lock_strict_mode),
        speaker_center_strict_mode=bool(speaker_center_strict_mode),
        speaker_center_max_offset=float(speaker_center_max_offset),
        speaker_face_lock_min_margin=float(speaker_face_lock_min_margin),
        dialogue_center_use_threshold=float(dialogue_center_use_threshold),
        listener_fallback_max_hold_seconds=float(listener_fallback_max_hold_seconds),
        listener_fallback_speech_hold_max_seconds=float(listener_fallback_speech_hold_max_seconds),
        dialogue_center_min_likelihood=float(dialogue_center_min_likelihood),
        dialogue_center_balance_margin=float(dialogue_center_balance_margin),
        subject_confidence_floor=float(subject_confidence_floor),
        subject_visibility_threshold=float(subject_visibility_threshold),
        reframe_subject_mode=str(reframe_subject_mode or "subject_first"),
        dialogue_two_shot_preferred=bool(dialogue_two_shot_preferred),
        new_face_fast_acquire_threshold=float(new_face_fast_acquire_threshold),
    )
    windows, state_usage = _turn_based_targets(
        targets,
        reframe_mode,
        transition_mode=reframe_transition_mode,
        hold_windows=speaker_switch_hold_windows,
        accent_frame_hold_windows=accent_frame_hold_windows,
        switch_min_visibility=float(reframe_switch_min_visibility),
        lost_face_hold_seconds=float(reframe_lost_face_hold_seconds),
        reframe_priority=str(reframe_priority),
        speaker_min_hold_seconds=float(speaker_min_hold_seconds),
        listener_hold_seconds=float(effective_listener_hold_seconds),
        speaker_lock_strict_mode=bool(speaker_lock_strict_mode),
        speaker_center_strict_mode=bool(speaker_center_strict_mode),
        speaker_center_max_offset=float(speaker_center_max_offset),
        speaker_face_lock_min_margin=float(speaker_face_lock_min_margin),
        dialogue_center_use_threshold=float(dialogue_center_use_threshold),
        listener_fallback_max_hold_seconds=float(listener_fallback_max_hold_seconds),
        listener_fallback_speech_hold_max_seconds=float(listener_fallback_speech_hold_max_seconds),
        dialogue_center_balance_margin=float(dialogue_center_balance_margin),
        empty_frame_guard_enabled=bool(empty_frame_guard_enabled),
        max_crop_delta_per_window=float(max_crop_delta_per_window),
        motion_blend_normal=float(motion_blend_normal),
        motion_blend_switch=float(motion_blend_switch),
        switch_score_margin=float(switch_score_margin),
        scene_recenter_hold_windows=int(scene_recenter_hold_windows),
        lock_confidence_threshold=float(lock_confidence_threshold),
        speaker_confidence_threshold=float(speaker_confidence_threshold),
        handoff_min_hold_windows=int(handoff_min_hold_windows),
        confident_lock_min_hold_windows=int(confident_lock_min_hold_windows),
        target_deadband_handoff=float(target_deadband_handoff),
        target_deadband_lock=float(target_deadband_lock),
        max_delta_handoff=float(max_delta_handoff),
        max_delta_lock=float(max_delta_lock),
        motion_blend_switch_handoff=float(motion_blend_switch_handoff),
        motion_blend_normal_handoff=float(motion_blend_normal_handoff),
        shot_reacquire_boost_windows=int(shot_reacquire_boost_windows),
        new_face_fast_acquire_threshold=float(new_face_fast_acquire_threshold),
    )
    if not bool(speaker_center_strict_mode):
        windows = _apply_camera_glide_plan(windows, glide_windows=int(reframe_glide_windows))
    if bool(speaker_center_strict_mode):
        windows = _merge_reframe_windows(
            windows,
            max_center_delta=min(float(speaker_center_max_offset) * 0.38, 0.025),
        )
    debug["track_count"] = len(tracks)
    debug["subject_detector_pass"] = str(subject_detector_pass or "light")
    debug["dialogue_center_used"] = any(
        item.get("track_id") == "dialogue_center" or str(item.get("subject_mode", "")) == "dialogue_center"
        for item in windows
    )
    debug["anchor_switches"] = 0
    debug["speaker_to_listener_switches"] = 0
    debug["scene_interest_fallback_used"] = any(item.get("scene_interest_fallback_used") for item in targets)
    debug["listener_face_fallback_used"] = any(item.get("listener_fallback_used") for item in targets)
    debug["subject_person_fallback_used"] = any(item.get("target_role") == "subject_person" for item in targets)
    debug["target_selection_mode"] = "evidence_scored_stability_first" if str(reframe_priority).lower() == "stability_first" else "evidence_scored_balanced"
    debug["speaker_lock_mode"] = str(speaker_lock_mode or "state_machine")
    debug["speaker_lock_strict_mode"] = bool(speaker_lock_strict_mode)
    debug["speaker_center_strict_mode"] = bool(speaker_center_strict_mode)
    debug["speaker_center_max_offset"] = float(speaker_center_max_offset)
    debug["speaker_face_lock_min_margin"] = float(speaker_face_lock_min_margin)
    debug["listener_fallback_max_hold_seconds"] = float(listener_fallback_max_hold_seconds)
    debug["dialogue_center_balance_margin"] = float(dialogue_center_balance_margin)
    debug["speaker_lock_state_usage"] = state_usage
    debug["scene_recenter_count"] = int(state_usage.get("scene_recenter_count", 0))
    debug["confident_lock_windows"] = int(state_usage.get("confident_lock_windows", 0))
    debug["handoff_glide_windows"] = int(state_usage.get("handoff_glide_windows", 0))
    debug["speaker_transition_direct_windows"] = int(state_usage.get("speaker_transition_direct_windows", 0))
    debug["speaker_switch_latency_windows"] = int(state_usage.get("switch_latency_windows", 0))
    subject_visibility_ratio = sum(1 for item in targets if bool(item.get("subject_visible"))) / max(1, len(targets))
    face_edge_clip_rate = sum(1 for item in targets if bool(item.get("speaker_face_edge_clipped"))) / max(1, len(targets))
    dialogue_mode_windows = sum(1 for item in targets if str(item.get("subject_mode")) == "dialogue_center")
    scene_interest_windows = sum(1 for item in targets if str(item.get("subject_mode")) == "scene_interest")
    lock_conf_avg = sum(float(item.get("lock_confidence", 0.0) or 0.0) for item in targets) / max(1, len(targets))
    debug["confident_lock_used"] = bool(
        lock_conf_avg >= float(lock_confidence_threshold)
        and int(state_usage.get("confident_lock_windows", 0)) >= max(2, int(round(len(targets) * 0.18)))
    )
    debug["handoff_mode"] = (
        "confident_lock"
        if int(state_usage.get("confident_lock_windows", 0)) > int(state_usage.get("handoff_glide_windows", 0))
        else "handoff_glide"
    )
    debug["lock_confidence_avg"] = round(lock_conf_avg, 4)
    speaker_confidence_score = round(
        sum(float(item.get("speaker_confidence_score", 0.0) or 0.0) for item in targets) / max(1, len(targets)),
        4,
    )
    if speaker_confidence_score <= 0.0 and (
        lock_conf_avg > 0.0
        or speaker_face_centered_windows > 0
        or dialogue_center_windows > 0
        or listener_fallback_windows > 0
        or subject_person_fallback_windows > 0
        or speaker_switches > 0
    ):
        speaker_confidence_score = round(
            min(
                1.0,
                max(
                    lock_conf_avg * 0.55,
                    debug["speaker_centered_rate"] * 0.45,
                    (dialogue_center_windows / max(1, len(windows))) * 0.40,
                ),
            ),
            4,
        )
    debug["speaker_confidence_score"] = speaker_confidence_score
    debug["empty_frame_guard_triggered"] = bool(state_usage.get("lost_face_recover", 0))
    debug["listener_hold_used"] = bool(state_usage.get("listener_hold", 0))
    debug["subject_person_hold_used"] = bool(state_usage.get("subject_person_hold", 0))
    debug["dialogue_center_candidate_count"] = sum(1 for item in targets if item.get("target_role") == "dialogue_center")
    debug["no_subject_windows"] = sum(1 for item in targets if item.get("no_subject_detected"))
    debug["subject_visibility_ratio"] = round(subject_visibility_ratio, 4)
    debug["face_edge_clip_rate"] = round(face_edge_clip_rate, 4)
    debug["dialogue_mode_windows"] = int(dialogue_mode_windows)
    debug["scene_interest_windows"] = int(scene_interest_windows)
    debug["dialogue_center_used"] = bool(debug["dialogue_center_used"] or dialogue_mode_windows > 0 or state_usage.get("dialogue_center", 0))
    debug["scene_interest_fallback_used"] = bool(debug["scene_interest_fallback_used"] or scene_interest_windows > 0)
    speaker_center_offsets = []
    speaker_face_centered_windows = 0
    dialogue_center_windows = 0
    listener_fallback_windows = 0
    subject_person_fallback_windows = 0
    for item in windows:
        role = str(item.get("role", item.get("state", "")) or "")
        if role == "dialogue_center":
            dialogue_center_windows += 1
        elif role == "listener":
            listener_fallback_windows += 1
        elif role == "subject_person":
            subject_person_fallback_windows += 1
        speaker_center = item.get("speaker_candidate_center")
        if speaker_center is None:
            continue
        try:
            offset = max(
                abs(float(item["center"][0]) - float(speaker_center[0])),
                abs(float(item["center"][1]) - float(speaker_center[1])),
            )
        except Exception:
            continue
        speaker_center_offsets.append(offset)
        if offset <= float(speaker_center_max_offset):
            speaker_face_centered_windows += 1
    debug["speaker_face_centered_windows"] = int(speaker_face_centered_windows)
    debug["dialogue_center_windows"] = int(dialogue_center_windows)
    debug["listener_fallback_windows"] = int(listener_fallback_windows)
    debug["subject_person_fallback_windows"] = int(subject_person_fallback_windows)
    speaker_switches = 0
    speaker_switch_log = []
    previous_speaker_anchor = None
    for item in windows:
        current_speaker_anchor = item.get("speaker_candidate_track_id")
        if current_speaker_anchor is None:
            current_speaker_anchor = item.get("track_id")
        current_speaker_anchor = str(current_speaker_anchor)
        if current_speaker_anchor in {"None", "", "dialogue_center", "scene_interest"}:
            current_speaker_anchor = None
        if previous_speaker_anchor is not None and current_speaker_anchor is not None and current_speaker_anchor != previous_speaker_anchor:
            speaker_switches += 1
            switch_confidence = max(
                float(item.get("speaker_confidence_score", 0.0) or 0.0),
                float(item.get("speaker_confidence", 0.0) or 0.0),
                float(item.get("listener_confidence", 0.0) or 0.0),
            )
            switch_label = f"{previous_speaker_anchor}->{current_speaker_anchor}"
            speaker_switch_log.append((switch_label, round(switch_confidence, 4)))
            if progress_callback:
                progress_callback(f"[reframe] speaker_switch={switch_label} confidence={switch_confidence:.2f}")
        if current_speaker_anchor is not None:
            previous_speaker_anchor = current_speaker_anchor
    debug["speaker_center_offset_avg"] = round(mean(speaker_center_offsets), 4) if speaker_center_offsets else 0.0
    debug["speaker_center_offset_p95"] = round(_percentile(speaker_center_offsets, 0.95), 4) if speaker_center_offsets else 0.0
    debug["speaker_centered_rate"] = round(speaker_face_centered_windows / max(1, len(speaker_center_offsets)), 4)
    debug["speaker_center_offset_samples"] = int(len(speaker_center_offsets))
    debug["speaker_switches"] = int(speaker_switches)
    debug["speaker_switch_log"] = [{"switch": label, "confidence": confidence} for label, confidence in speaker_switch_log]
    debug["reframe_fallback_count"] = int(
        int(debug.get("listener_fallback_windows", 0) or 0)
        + int(debug.get("subject_person_fallback_windows", 0) or 0)
        + int(state_usage.get("lost_face_recover", 0))
        + (1 if bool(debug.get("center_safe_fallback_used", False)) else 0)
        + (1 if bool(debug.get("face_preserving_fallback_used", False)) else 0)
    )
    visual_conversation_score = _visual_conversation_score(
        speaker_switches,
        len(windows),
        speaker_centered_rate=debug["speaker_centered_rate"],
        dialogue_center_windows=dialogue_center_windows,
        listener_fallback_windows=listener_fallback_windows,
        subject_person_fallback_windows=subject_person_fallback_windows,
        center_fallback_used=bool(debug.get("center_safe_fallback_used", False)),
        face_preserving_fallback_used=bool(debug.get("face_preserving_fallback_used", False)),
    )
    if visual_conversation_score <= 0.0 and (
        speaker_switches > 0
        or speaker_face_centered_windows > 0
        or dialogue_center_windows > 0
        or listener_fallback_windows > 0
        or subject_person_fallback_windows > 0
    ):
        visual_conversation_score = round(
            min(
                1.0,
                max(
                    debug["speaker_centered_rate"] * 0.32,
                    (dialogue_center_windows / max(1, len(windows))) * 0.30,
                    (speaker_switches / max(1, len(windows) - 1)) * 0.26,
                    0.18,
                ),
            ),
            4,
        )
    debug["visual_conversation_score"] = visual_conversation_score
    if progress_callback:
        if bool(debug.get("center_safe_fallback_used", False)):
            progress_callback(f"[reframe] fallback=center reason={debug.get('center_safe_fallback_reason', 'no_face_confidence')}")
        elif bool(debug.get("face_preserving_fallback_used", False)):
            progress_callback(f"[reframe] fallback=face_preserving reason={debug.get('face_preserving_fallback_reason', 'low_confidence')}")
        elif int(debug.get("reframe_fallback_count", 0) or 0) > 0:
            progress_callback("[reframe] using_two_shot confidence_low")
        progress_callback(f"[reframe] visual_conversation_score={debug['visual_conversation_score']:.2f}")
    debug["subject_mode"] = max(
        [str(item.get("subject_mode", "safe_center")) for item in targets] or ["safe_center"],
        key=lambda mode: sum(1 for item in targets if str(item.get("subject_mode", "safe_center")) == mode),
    )
    debug["scene_change_windows"] = sum(1 for item in targets if bool(item.get("scene_change_detected")))
    debug["shot_reacquire_windows"] = sum(
        1
        for item in targets
        if bool(item.get("scene_change_detected")) and float(item.get("lock_confidence", 0.0) or 0.0) >= float(new_face_fast_acquire_threshold) * 0.7
    )
    debug["subject_loss_windows"] = sum(1 for item in targets if bool(item.get("no_subject_detected")))
    debug["recoverable_subject_windows"] = sum(1 for item in targets if bool(item.get("recoverable_subject")))
    debug["face_hold_windows"] = sum(
        1
        for item in targets
        if bool(item.get("face_hold_available"))
        or str((item.get("speaker_evidence_summary") or {}).get("primary_source", "")) == "face_hold"
    )
    debug["new_face_acquire_count"] = int(state_usage.get("new_face_acquire_count", 0))
    debug["fast_acquire_used"] = bool(state_usage.get("fast_reacquire_success", 0))
    debug["fast_reacquire_attempted"] = int(state_usage.get("fast_reacquire_attempted", 0))
    debug["fast_reacquire_success"] = int(state_usage.get("fast_reacquire_success", 0))
    debug["scene_change_avg_score"] = round(
        sum(float(item.get("scene_change_score", 0.0) or 0.0) for item in targets) / max(1, len(targets)),
        4,
    )
    debug["speaker_evidence_summary"] = _best_evidence_summary(targets)
    debug["evidence_visible_faces_peak"] = max(
        [int((item.get("speaker_evidence_summary") or {}).get("visible_faces", 0) or 0) for item in targets] or [0]
    )
    debug["evidence_recent_face_memory_peak"] = max(
        [int((item.get("speaker_evidence_summary") or {}).get("recent_face_memory_count", 0) or 0) for item in targets] or [0]
    )
    debug["evidence_visible_persons_peak"] = max(
        [int((item.get("speaker_evidence_summary") or {}).get("visible_persons", 0) or 0) for item in targets] or [0]
    )
    debug["framing_mode"] = framing
    previous_track_id = None
    previous_role = None
    reacquire_budget = max(0, int(shot_reacquire_boost_windows))
    for item in windows:
        track_id = item.get("track_id")
        role = str(item.get("role", ""))
        if previous_track_id is not None and track_id != previous_track_id:
            debug["anchor_switches"] += 1
            if reacquire_budget > 0 and bool(item.get("confident_lock")):
                debug["new_face_acquire_count"] += 1
                debug["fast_acquire_used"] = True
                reacquire_budget -= 1
            if bool(reframe_listener_face_fallback) and previous_role == "speaker" and role == "listener":
                debug["speaker_to_listener_switches"] += 1
        previous_track_id = track_id
        previous_role = role

    temp_dir = tempfile.mkdtemp(prefix="sf_crop_")
    parts = []
    try:
        for index, window in enumerate(windows):
            window_start = window["start"]
            window_end = window["end"]
            center = window["center"]
            window_role = str(window.get("role", window.get("state", "")) or "")
            window_framing = framing
            if window_role == "dialogue_center" and framing in {"face_locked", "balanced", "wide_subject", "context_padded", "shot_lock", "scene_lock", "human_handoff"}:
                window_framing = "face_locked" if bool(speaker_center_strict_mode) else "dialogue_dual"
            inner_height_ratio = _framing_inner_height_ratio(window_framing, window_role)
            inner_h = int(target_h * inner_height_ratio)
            inner_h = max(680, min(target_h - 120, inner_h))
            aspect = target_w / float(inner_h)
            if (source_w / float(source_h)) >= aspect:
                crop_w = int(source_h * aspect)
                crop_h = source_h
            else:
                crop_w = source_w
                crop_h = int(source_w / aspect)
            _track_id = window.get("track_id")
            if _track_id == "scene_interest":
                debug["scene_interest_fallback_used"] = True
            cx = int(center[0] * source_w)
            cy = int(center[1] * source_h)
            x1 = _clamp(cx - crop_w // 2, 0, max(0, source_w - crop_w))
            y1 = _clamp(cy - crop_h // 2, 0, max(0, source_h - crop_h))
            if square_canvas_mode:
                square_size = max(2, min(source_w, source_h))
                square_size = square_size - (square_size % 2)
                square_x = _clamp(cx - square_size // 2, 0, max(0, source_w - square_size))
                square_y = _clamp(cy - square_size // 2, 0, max(0, source_h - square_size))
                overlay_y = max(180, (target_h - target_w) // 2 - 60)
                vf = (
                    f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                    f"crop={target_w}:{target_h},boxblur=18:1[bg];"
                    f"[0:v]crop={square_size}:{square_size}:{int(square_x)}:{int(square_y)},"
                    f"scale={target_w}:{target_w}:force_original_aspect_ratio=decrease[fg];"
                    f"[bg][fg]overlay=(W-w)/2:{overlay_y}"
                )
            elif window_framing in {"context_padded", "wide_subject", "face_locked", "dialogue_dual", "shot_lock", "scene_lock", "human_handoff"}:
                overlay_y = max(32, (target_h - inner_h) // 2)
                vf = (
                    f"[0:v]crop={crop_w}:{crop_h}:{int(x1)}:{int(y1)},"
                    f"scale={target_w}:{inner_h}:force_original_aspect_ratio=decrease[fg];"
                    f"color=c=black:s={target_w}x{target_h}[bg];"
                    f"[bg][fg]overlay=(W-w)/2:{overlay_y}"
                )
            else:
                vf = f"crop={crop_w}:{crop_h}:{int(x1)}:{int(y1)},scale={target_w}:{target_h}"
            part_path = os.path.join(temp_dir, f"part_{index:04d}.mp4")
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-ss",
                str(round(window_start, 3)),
                "-to",
                str(round(window_end, 3)),
                *(
                    ["-filter_complex", vf]
                    if square_canvas_mode or framing in {"context_padded", "wide_subject", "face_locked", "shot_lock", "scene_lock", "human_handoff"}
                    else ["-vf", vf]
                ),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-ac",
                "2",
                "-c:a",
                "aac",
                part_path,
                "-hide_banner",
                "-loglevel",
                "error",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if proc.returncode == 0 and os.path.exists(part_path) and os.path.getsize(part_path) > 512:
                parts.append(part_path)
            elif progress_callback:
                progress_callback(f"[warning] Reframe window failed: {window_start:.2f}-{window_end:.2f}")

        if not parts:
            return _write_center_crop(out_path, "no_face_windows")

        list_path = os.path.join(temp_dir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for part_path in parts:
                handle.write(f"file '{os.path.abspath(part_path)}'\n")

        concat_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-ac",
            "2",
            "-c:a",
            "aac",
            out_path,
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        proc = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            # PHASE 3C: Compute turn-first metrics
            if subtitle_segments and windows:
                try:
                    metrics = compute_turn_first_metrics(windows, subtitle_segments, start_t, end_t)
                    if isinstance(debug_info, dict) and metrics:
                        debug_info["turn_first_metrics"] = metrics
                except Exception:
                    pass  # Metrics are optional, don't fail the crop
            return True
        return _write_center_crop(out_path, "concat_failed")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
