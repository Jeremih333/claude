from __future__ import annotations


def should_use_remote_fallback(cfg: dict | None, context: dict | None = None) -> bool:
    cfg = cfg or {}
    context = context or {}
    if not bool(cfg.get("remote_quality_enabled", False)):
        return False
    mode = str(cfg.get("remote_quality_fallback", "off") or "off").lower()
    if mode == "off":
        return False
    if mode == "manual":
        return True
    if mode != "difficult_clips_only":
        return False
    subtitle_confidence = float(context.get("subtitle_confidence", 1.0) or 1.0)
    anchor_switches = int(context.get("anchor_switches", 0) or 0)
    empty_frame_risk = float(context.get("empty_frame_risk", 0.0) or 0.0)
    return subtitle_confidence < 0.33 or anchor_switches >= 12 or empty_frame_risk >= 0.65


def enhance_clip_metadata(cfg: dict | None, context: dict | None = None) -> dict:
    cfg = cfg or {}
    context = context or {}
    provider = str(cfg.get("remote_quality_provider", "") or "").strip()
    return {
        "remote_quality_attempted": False,
        "remote_quality_used": False,
        "remote_quality_provider": provider,
        "remote_quality_reason": None,
        "remote_quality_available": bool(provider and bool(cfg.get("remote_quality_enabled", False))),
        "remote_quality_context": {
            "subtitle_confidence": float(context.get("subtitle_confidence", 0.0) or 0.0),
            "anchor_switches": int(context.get("anchor_switches", 0) or 0),
        },
    }
