"""
subtitle_speaker_cuts.py
------------------------
Subtitle-driven speaker framing for vertical Shorts.

When face detection fails or is unreliable (DVDRip, low-quality content),
this module uses speaker turn changes in subtitle data to drive crop cuts.

Strategy:
- Identify unique speakers from subtitle segments
- Assign each speaker a horizontal zone of the frame (left/right/center)
- Generate a sequence of crop windows that switch zones when speaker changes
- Guarantees cuts every time speaker changes — creates natural dialogue rhythm

Public API
----------
build_subtitle_speaker_plan(subtitle_segments, video_width, video_height, *, cfg) -> dict
should_use_subtitle_speaker_cuts(subtitle_segments, reframe_debug) -> bool
"""

from __future__ import annotations

import re


def _clean_speaker(speaker: str) -> str:
    return re.sub(r"\\s+", " ", str(speaker or "").strip()).lower()


# Speakers with these names/patterns are treated as "unknown" and won't get
# a dedicated zone - treated as center/neutral
_GENERIC_SPEAKERS: frozenset[str] = frozenset(
    {
        "",
        "unknown",
        "unkn",
        "speaker",
        "speaker_0",
        "speaker_1",
        "speaker0",
        "speaker1",
        "s0",
        "s1",
        "spkr",
        "narrator",
        "голос",
        "voice",
        "voice_0",
        "voice_1",
        "person_0",
        "person_1",
        "безымянный",
        "мужчина",
        "женщина",
        "человек",
    }
)


def _is_generic_speaker(speaker: str) -> bool:
    clean = _clean_speaker(speaker)
    if clean in _GENERIC_SPEAKERS:
        return True
    # Pattern: speaker_N, speakerN, sN, etc.
    if re.match(r"^(speaker|spkr|s|person|voice|голос|нарратор)_?\d+$", clean):
        return True
    return False


def _extract_speaker_sequence(
    subtitle_segments: list[dict],
) -> list[tuple[str, float, float]]:
    """
    Returns list of (speaker, start, end) for each subtitle segment.
    Segments with no speaker get "NEUTRAL".
    """
    result = []
    for seg in subtitle_segments or []:
        speaker = str(seg.get("speaker", "") or "")
        if _is_generic_speaker(speaker):
            speaker = "NEUTRAL"
        start = float(seg.get("start", 0.0) or 0.0)
        end = float(seg.get("end", start) or start)
        if end > start:
            result.append((speaker, start, end))
    return result


def _assign_speaker_zones(
    speakers: list[str],
    video_width: int,
    video_height: int,
) -> dict[str, dict]:
    """
    Assign horizontal crop zones to each speaker.

    For 2 speakers: left half / right half
    For 3+ speakers: left third / center / right third
    For 1 speaker or NEUTRAL: full center

    Returns dict mapping speaker -> {x, y, w, h} in source pixels
    (before vertical scaling to 9:16)
    """
    real_speakers = [s for s in speakers if s != "NEUTRAL"]
    zones: dict[str, dict] = {}

    if len(real_speakers) == 0:
        # No identified speakers: center crop
        zones["NEUTRAL"] = {
            "x": video_width // 4,
            "y": 0,
            "w": video_width // 2,
            "h": video_height,
        }
    elif len(real_speakers) == 1:
        # Single speaker: center + slight zoom
        zones[real_speakers[0]] = {
            "x": video_width // 4,
            "y": 0,
            "w": video_width // 2,
            "h": video_height,
        }
        zones["NEUTRAL"] = zones[real_speakers[0]]
    else:
        # Multiple speakers: alternate left/right/center
        # For standard 4:3 or 16:9 source, using half-frame gives good vertical framing
        n = min(len(real_speakers), 3)
        zone_w = video_width // n

        for i, sp in enumerate(real_speakers[:n]):
            x = i * zone_w
            # Ensure the crop is wide enough to be meaningful (min 200px on source)
            actual_w = max(zone_w, video_width // 3)
            # Don't exceed frame
            actual_w = min(actual_w, video_width - x)
            zones[sp] = {
                "x": x,
                "y": 0,
                "w": actual_w,
                "h": video_height,
            }

        # NEUTRAL/unknown gets center zone
        center_x = max(0, video_width // 2 - video_width // 4)
        zones["NEUTRAL"] = {
            "x": center_x,
            "y": 0,
            "w": video_width // 2,
            "h": video_height,
        }

    return zones


def build_subtitle_speaker_plan(
    subtitle_segments: list[dict],
    video_width: int = 640,
    video_height: int = 480,
    *,
    cfg: dict | None = None,
) -> dict:
    """
    Build a subtitle-driven speaker framing plan.

    Returns a dict with:
    - "cuts": list of {start, end, speaker, zone: {x, y, w, h}}
    - "speakers": list of unique real speaker names found
    - "speaker_zones": dict mapping speaker name -> zone
    - "has_multiple_speakers": bool
    - "subtitle_driven": True (marker that this is subtitle-driven, not face-driven)
    """
    cfg = cfg or {}

    if not subtitle_segments:
        return {
            "cuts": [],
            "speakers": [],
            "speaker_zones": {},
            "has_multiple_speakers": False,
            "subtitle_driven": True,
        }

    sequence = _extract_speaker_sequence(subtitle_segments)
    if not sequence:
        return {
            "cuts": [],
            "speakers": [],
            "speaker_zones": {},
            "has_multiple_speakers": False,
            "subtitle_driven": True,
        }

    # Find all unique real speakers
    all_speakers = sorted({sp for sp, _, _ in sequence if sp != "NEUTRAL"})
    has_multiple = len(all_speakers) >= 2

    # Assign zones
    zones = _assign_speaker_zones(all_speakers, video_width, video_height)

    # Build cut sequence: merge consecutive same-speaker segments
    cuts: list[dict] = []
    prev_speaker: str | None = None
    cut_start = 0.0
    cut_end = 0.0

    for speaker, seg_start, seg_end in sequence:
        if speaker != prev_speaker:
            # Flush previous cut
            if prev_speaker is not None and cut_end > cut_start:
                zone = zones.get(prev_speaker, zones.get("NEUTRAL", {}))
                cuts.append(
                    {
                        "start": round(cut_start, 3),
                        "end": round(cut_end, 3),
                        "speaker": prev_speaker,
                        "zone": dict(zone),
                    }
                )
            prev_speaker = speaker
            cut_start = seg_start
        cut_end = seg_end

    # Flush final cut
    if prev_speaker is not None and cut_end > cut_start:
        zone = zones.get(prev_speaker, zones.get("NEUTRAL", {}))
        cuts.append(
            {
                "start": round(cut_start, 3),
                "end": round(cut_end, 3),
                "speaker": prev_speaker,
                "zone": dict(zone),
            }
        )

    return {
        "cuts": cuts,
        "speakers": all_speakers,
        "speaker_zones": zones,
        "has_multiple_speakers": has_multiple,
        "subtitle_driven": True,
    }


def should_use_subtitle_speaker_cuts(
    subtitle_segments: list[dict],
    reframe_debug: dict | None = None,
) -> bool:
    """
    Decide whether to use subtitle-driven speaker cuts instead of face detection.

    Returns True when:
    - Face detection failed (center_safe_fallback_used or hard_timeout_triggered)
    - OR person_presence is very low (< 0.15)
    - AND subtitle segments have multiple identifiable speakers

    This ensures we only use subtitle cuts when face detection is unreliable,
    but we always fall back to subtitle cuts rather than a static center crop.
    """
    debug = dict(reframe_debug or {})

    # Face detection indicators of failure
    face_failed = bool(
        debug.get("center_safe_fallback_used", False)
        or debug.get("hard_timeout_triggered", False)
        or float(debug.get("person_presence", 1.0) or 1.0) < 0.15
        or debug.get("subject_acquisition_state") == "no_visible_subject"
    )

    if not face_failed:
        return False

    # Check if we have usable subtitle speaker data
    segs = list(subtitle_segments or [])
    speakers = {
        str(s.get("speaker", "") or "")
        for s in segs
        if str(s.get("speaker", "") or "").strip()
        and not _is_generic_speaker(str(s.get("speaker", "") or ""))
    }

    # Only useful if we have at least 2 real speakers OR just 1 speaker
    # (even 1 identified speaker is better than a static center crop)
    return len(speakers) >= 1
