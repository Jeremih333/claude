from __future__ import annotations


def summarize_reframe_debug(reframe_debug: dict | None) -> dict:
    data = dict(reframe_debug or {})
    speaker_switches = int(data.get("speaker_switches", 0) or 0)
    speaker_to_listener_switches = int(data.get("speaker_to_listener_switches", 0) or 0)
    listener_fallback_windows = int(data.get("listener_fallback_windows", 0) or 0)
    return {
        "speaker_switches": speaker_switches,
        "speaker_confidence_score": float(data.get("speaker_confidence_score", 0.0) or 0.0),
        "visual_conversation_score": float(data.get("visual_conversation_score", 0.0) or 0.0),
        "reframe_fallback_count": int(data.get("reframe_fallback_count", 0) or 0),
        "face_preserving_fallback_used": bool(data.get("face_preserving_fallback_used", False)),
        "center_safe_fallback_used": bool(data.get("center_safe_fallback_used", False)),
        "dialogue_center_windows": int(data.get("dialogue_center_windows", 0) or 0),
        "listener_fallback_windows": int(data.get("listener_fallback_windows", 0) or 0),
        "subject_person_fallback_windows": int(data.get("subject_person_fallback_windows", 0) or 0),
        "speaker_to_listener_switches": speaker_to_listener_switches,
        "listener_reaction_count": max(listener_fallback_windows, speaker_to_listener_switches),
    }
