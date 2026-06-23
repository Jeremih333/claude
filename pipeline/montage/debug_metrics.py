from __future__ import annotations


def build_montage_debug_snapshot(meta: dict) -> dict:
    meta = dict(meta or {})
    return {
        "candidate_id": meta.get("candidate_id", ""),
        "conversation_id": meta.get("conversation_id", ""),
        "story_thread_id": meta.get("story_thread_id", ""),
        "story_arc_shape": meta.get("story_arc_shape", ""),
        "story_completion_score": float(meta.get("story_completion_score", 0.0) or 0.0),
        "context_completeness_score": float(meta.get("context_completeness_score", 0.0) or 0.0),
        "hook_score": float(meta.get("hook_score", 0.0) or 0.0),
        "payoff_score": float(meta.get("payoff_score", meta.get("payoff_strength", 0.0)) or 0.0),
        "story_coherence_score": float(meta.get("story_coherence_score", 0.0) or 0.0),
        "speaker_confidence_score": float(meta.get("speaker_confidence_score", 0.0) or 0.0),
        "visual_conversation_score": float(meta.get("visual_conversation_score", 0.0) or 0.0),
        "needs_review": bool(meta.get("needs_review", False)),
    }

