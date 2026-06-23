"""
conversation_grouper.py
───────────────────────
Group dialogue turns into ConversationBlocks using semantic criteria.

The primary split criterion is a temporal gap exceeding max_gap_seconds,
but several BRIDGE conditions override the split when semantic continuity
is detected (same speakers, same topic, monologue continuation).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# ---------------------------------------------------------------------------
# Stop-word lists (Russian + English) for topic-token extraction
# ---------------------------------------------------------------------------

_STOP_WORDS: frozenset[str] = frozenset(
    {
        # English
        "that",
        "this",
        "these",
        "those",
        "with",
        "from",
        "have",
        "just",
        "been",
        "were",
        "they",
        "them",
        "their",
        "what",
        "which",
        "when",
        "where",
        "will",
        "would",
        "could",
        "should",
        "does",
        "didn",
        "don",
        "isn",
        "aren",
        "wasn",
        "weren",
        "hasn",
        "haven",
        "hadn",
        "about",
        "into",
        "than",
        "then",
        "there",
        "here",
        "also",
        "only",
        "very",
        "more",
        "some",
        "your",
        "mine",
        "ours",
        "yours",
        "theirs",
        "such",
        "each",
        "both",
        "same",
        "other",
        "back",
        "even",
        "still",
        "well",
        "said",
        "like",
        "know",
        "think",
        "want",
        "come",
        "going",
        "make",
        "take",
        "look",
        "good",
        "right",
        "over",
        "after",
        "before",
        # Russian (transliterated stored as actual Cyrillic)
        "это",
        "этот",
        "этой",
        "эти",
        "тебя",
        "тебе",
        "тебя",
        "тебе",
        "меня",
        "мене",
        "мной",
        "тобой",
        "него",
        "неё",
        "нему",
        "ней",
        "нами",
        "вами",
        "ними",
        "себя",
        "себе",
        "собой",
        "свой",
        "своя",
        "своё",
        "свои",
        "которые",
        "который",
        "которая",
        "которое",
        "когда",
        "куда",
        "откуда",
        "потом",
        "затем",
        "здесь",
        "туда",
        "сюда",
        "тоже",
        "также",
        "очень",
        "более",
        "менее",
        "самый",
        "такой",
        "такая",
        "такие",
        "такое",
        "уже",
        "ещё",
        "даже",
        "если",
        "чтобы",
        "хотя",
        "пока",
        "после",
        "перед",
        "между",
        "через",
        "около",
        "против",
        "будет",
        "будут",
        "есть",
        "была",
        "было",
        "были",
        "быть",
        "иметь",
        "надо",
        "нужно",
        "можно",
        "нельзя",
        "хочу",
        "хочет",
        "хотят",
        "знаю",
        "знает",
        "знают",
        "идти",
        "иди",
        "идём",
        "говорить",
        "скажи",
        "скажет",
        "сказал",
        "сказала",
        "тогда",
        "всегда",
        "никогда",
        "иногда",
        "сейчас",
        "теперь",
        "просто",
        "только",
        "всего",
        "всём",
        "много",
        "мало",
        "вообще",
    }
)

_MIN_TOKEN_LEN: int = 4


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def group_conversations(
    turns: list[dict[str, Any]] | None,
    max_gap_seconds: float = 2.0,
    source_id: str | None = None,
) -> list[dict[str, Any]]:
    """Group ordered dialogue turns into ConversationBlocks.

    Parameters
    ----------
    turns:
        Ordered list of turn dicts (output of ``extract_dialogue_turns``).
    max_gap_seconds:
        Primary temporal threshold for splitting.  Gaps wider than this
        trigger a candidate split, which may then be bridged by the
        semantic conditions below.
    source_id:
        Optional identifier of the source file / episode, forwarded to
        the stable conversation-ID generator.

    Returns
    -------
    list[dict]
        Each block contains:
        - conversation_id : str          — SHA1-based stable ID
        - start           : float
        - end             : float
        - duration        : float
        - turn_count      : int
        - speakers        : list[str]    — deduplicated, ordered by first appearance
        - turns           : list[dict]   — the constituent turn dicts
    """
    if not turns:
        return []

    blocks: list[list[dict[str, Any]]] = []
    current_chunk: list[dict[str, Any]] = [turns[0]]

    for i in range(1, len(turns)):
        prev_turn = turns[i - 1]
        curr_turn = turns[i]
        gap = curr_turn["start"] - prev_turn["end"]

        if gap <= max_gap_seconds:
            # Within primary threshold — always continue
            current_chunk.append(curr_turn)
            continue

        # PHASE 4: Continuation priority — longer chunks get bonus tolerance
        # Chunks with 4+ turns can bridge gaps up to 1.5× normal threshold
        effective_max_gap = max_gap_seconds
        if len(current_chunk) >= 4:
            effective_max_gap = max_gap_seconds * 1.5

        # Gap exceeds threshold — evaluate bridge conditions
        if _should_bridge(current_chunk, curr_turn, gap, effective_max_gap):
            current_chunk.append(curr_turn)
        else:
            blocks.append(current_chunk)
            current_chunk = [curr_turn]

    blocks.append(current_chunk)

    return [_build_block(chunk, source_id) for chunk in blocks if chunk]


def conversation_id_for_turns(
    turns: list[dict[str, Any]],
    source_id: str | None = None,
) -> str:
    """Generate a stable, content-derived conversation ID.

    The ID is a short SHA1 hex digest formed from the source_id (if any),
    the start/end times, and the turn count.
    """
    if not turns:
        return "conv_empty"
    start = turns[0].get("start", 0.0)
    end = turns[-1].get("end", 0.0)
    count = len(turns)
    fingerprint = f"{source_id or ''}|{start:.3f}|{end:.3f}|{count}"
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    return f"conv_{digest}"


# ---------------------------------------------------------------------------
# Internal helpers (exported with leading underscore for introspection)
# ---------------------------------------------------------------------------


def _topic_tokens(
    turns: list[dict[str, Any]],
    limit: int = 8,
) -> frozenset[str]:
    """Extract the top *limit* meaningful content tokens from a set of turns.

    Tokens are lower-cased, stripped of punctuation, and filtered against
    the combined Russian + English stop-word list plus a minimum length
    requirement.  The ``limit`` most frequent tokens are returned.
    """
    freq: dict[str, int] = {}
    for turn in turns:
        text = str(turn.get("text", "") or "")
        for raw_token in re.split(r"[\s\W]+", text.lower()):
            token = raw_token.strip()
            if len(token) >= _MIN_TOKEN_LEN and token not in _STOP_WORDS:
                freq[token] = freq.get(token, 0) + 1

    # Return up to `limit` most frequent tokens
    top = sorted(freq, key=lambda t: freq[t], reverse=True)[:limit]
    return frozenset(top)


def _speaker_overlap(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two speaker sets.

    Returns a value in [0, 1].  Returns 0.0 when both sets are empty.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _topic_overlap(
    tokens_a: frozenset[str],
    tokens_b: frozenset[str],
) -> float:
    """Jaccard similarity between two topic-token sets.

    Returns a value in [0, 1].  Returns 0.0 when both sets are empty.
    """
    if not tokens_a and not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _chunk_speakers(chunk: list[dict[str, Any]]) -> set[str]:
    return {str(t.get("speaker", "UNKNOWN") or "UNKNOWN") for t in chunk}


def _should_bridge(
    current_chunk: list[dict[str, Any]],
    next_turn: dict[str, Any],
    gap: float,
    max_gap: float,
) -> bool:
    """Return True if the gap should be bridged rather than split."""
    speakers_curr = _chunk_speakers(current_chunk)
    speakers_next = {str(next_turn.get("speaker", "UNKNOWN") or "UNKNOWN")}

    # Bridge 1: same speaker monologue (allow up to 3× the normal gap)
    if speakers_curr == speakers_next and gap <= max_gap * 3.0:
        return True

    # PHASE 4: Relaxed speaker overlap from 0.6 → 0.5 and gap from 8.0 → 10.0s
    # Bridge 2: high speaker overlap, moderate gap
    sp_overlap = _speaker_overlap(speakers_curr, speakers_next)
    if sp_overlap >= 0.5 and gap <= 10.0:
        return True

    # PHASE 4: Relaxed topic overlap from 0.3 → 0.25 and gap from 5.0 → 6.5s
    # Bridge 3: topic continuity, moderate gap
    tokens_curr = _topic_tokens(current_chunk)
    tokens_next = _topic_tokens([next_turn])
    t_overlap = _topic_overlap(tokens_curr, tokens_next)
    if t_overlap >= 0.25 and gap <= 6.5:
        return True

    return False


def _build_block(
    chunk: list[dict[str, Any]],
    source_id: str | None,
) -> dict[str, Any]:
    """Assemble the final ConversationBlock dict from a list of turns."""
    start = chunk[0]["start"]
    end = chunk[-1]["end"]

    # Deduplicate speakers while preserving first-appearance order
    seen: set[str] = set()
    speakers: list[str] = []
    for turn in chunk:
        sp = str(turn.get("speaker", "UNKNOWN") or "UNKNOWN")
        if sp not in seen:
            seen.add(sp)
            speakers.append(sp)

    conv_id = conversation_id_for_turns(chunk, source_id)

    return {
        "conversation_id": conv_id,
        "start": round(start, 4),
        "end": round(end, 4),
        "duration": round(end - start, 4),
        "turn_count": len(chunk),
        "speakers": speakers,
        "turns": chunk,
    }
