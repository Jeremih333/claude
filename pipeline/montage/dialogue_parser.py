"""
dialogue_parser.py
──────────────────
Extract structured dialogue turns from raw subtitle segments.

Each turn carries timing, speaker attribution, text, and a
semantic turn_type so downstream modules can reason about
narrative structure without re-parsing text.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SHORT_REPLY_MAX_WORDS: int = 3
_INTERRUPTION_MAX_DURATION: float = 0.8  # seconds
_INTERRUPTION_MAX_GAP: float = 0.2  # seconds between turns


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_dialogue_turns(
    subtitle_segments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Convert raw subtitle segments into structured dialogue turns.

    Parameters
    ----------
    subtitle_segments:
        List of dicts, each expected to contain at minimum:
        ``start`` (float), ``end`` (float), ``text`` (str).
        Optional: ``speaker`` (str).

    Returns
    -------
    list[dict]
        Each turn dict contains:
        - turn_id        : int   — 0-based index
        - start          : float — turn start time in seconds
        - end            : float — turn end time in seconds
        - duration       : float — end - start
        - text           : str   — cleaned text
        - speaker        : str   — speaker label or "UNKNOWN"
        - has_speech     : bool  — True when text has printable words
        - turn_type      : str   — "question"|"exclamation"|"short_reply"|
                                   "statement"|"empty"
        - is_interruption: bool  — True when interruption heuristic fires
    """
    if not subtitle_segments:
        return []

    turns: list[dict[str, Any]] = []

    for idx, seg in enumerate(subtitle_segments):
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        if end < start:
            end = start

        raw_text: str = seg.get("text", "") or ""
        text = _clean_text(raw_text)
        speaker: str = (
            str(seg.get("speaker", "UNKNOWN") or "UNKNOWN").strip() or "UNKNOWN"
        )

        has_speech = bool(text.strip())
        turn_type = _classify_turn_type(text)

        turn: dict[str, Any] = {
            "turn_id": idx,
            "start": start,
            "end": end,
            "duration": round(end - start, 4),
            "text": text,
            "speaker": speaker,
            "has_speech": has_speech,
            "turn_type": turn_type,
            "is_interruption": False,  # filled in second pass below
        }
        turns.append(turn)

    # Second pass: detect interruptions
    for i in range(1, len(turns)):
        prev = turns[i - 1]
        curr = turns[i]
        gap = curr["start"] - prev["end"]
        if (
            curr["duration"] < _INTERRUPTION_MAX_DURATION
            and gap < _INTERRUPTION_MAX_GAP
        ):
            curr["is_interruption"] = True

    return turns


def extract_silence_spans(
    turns: list[dict[str, Any]] | None,
    total_duration: float | None = None,
) -> list[dict[str, Any]]:
    """Identify silent gaps between dialogue turns.

    Parameters
    ----------
    turns:
        Ordered list of turn dicts as returned by
        :func:`extract_dialogue_turns`.
    total_duration:
        Optional total media duration. When provided, a trailing
        silence from the last turn to the end of the file is included.

    Returns
    -------
    list[dict]
        Each entry: ``{start, end, duration}``.
        Only gaps with duration > 0 are returned.
    """
    if not turns:
        return []

    spans: list[dict[str, Any]] = []

    for i in range(1, len(turns)):
        gap_start = turns[i - 1]["end"]
        gap_end = turns[i]["start"]
        duration = gap_end - gap_start
        if duration > 0:
            spans.append(
                {
                    "start": round(gap_start, 4),
                    "end": round(gap_end, 4),
                    "duration": round(duration, 4),
                }
            )

    if total_duration is not None and turns:
        trailing = total_duration - turns[-1]["end"]
        if trailing > 0:
            spans.append(
                {
                    "start": round(turns[-1]["end"], 4),
                    "end": round(total_duration, 4),
                    "duration": round(trailing, 4),
                }
            )

    return spans


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clean_text(raw: str) -> str:
    """Strip subtitle formatting tags and normalise whitespace."""
    # Remove common subtitle tags: <i>, <b>, <font ...>, {\\an8}, etc.
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\{[^}]+\}", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _classify_turn_type(text: str) -> str:
    """Return the semantic category of a single dialogue turn.

    Categories
    ----------
    "empty"       — no printable content
    "question"    — ends with "?" or contains interrogative structure
    "exclamation" — ends with "!" (or multiple punctuation involving "!")
    "short_reply" — ≤ _SHORT_REPLY_MAX_WORDS words
    "statement"   — everything else
    """
    stripped = text.strip()
    if not stripped:
        return "empty"

    # Question: ends with ? (possibly followed by closing quotes / spaces)
    if re.search(r"\?\s*[\"'»]?\s*$", stripped):
        return "question"

    # Exclamation
    if re.search(r"!\s*[\"'»]?\s*$", stripped):
        return "exclamation"

    # Count words (split on whitespace)
    word_count = len(stripped.split())
    if word_count <= _SHORT_REPLY_MAX_WORDS:
        return "short_reply"

    return "statement"
