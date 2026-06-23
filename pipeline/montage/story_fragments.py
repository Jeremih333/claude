"""
story_fragments.py
──────────────────
The heart of the semantic pipeline.

Converts dialogue turns into StoryFragments — narrative units that carry
scores for hook, conflict, escalation, emotion, and payoff signals.
Multiple StoryFragments can be chained into a StoryChain for export.

Episode → Transcript → DialogueTurns → ConversationBlocks
  → StoryFragments → StoryChains → Export
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Signal dictionaries — bilingual (Russian + English)
# ---------------------------------------------------------------------------

HOOK_SIGNALS: tuple[str, ...] = (
    # English
    "wait",
    "stop",
    "listen",
    "look",
    "what",
    "why",
    "seriously",
    "really",
    "don't",
    "no way",
    "you're kidding",
    "impossible",
    "unbelievable",
    "i can't believe",
    "tell me",
    "how could",
    "what happened",
    "what did",
    "who did",
    "did you",
    "are you",
    "is that",
    "you mean",
    # Russian
    "стой",
    "подожди",
    "слушай",
    "послушай",
    "смотри",
    "что",
    "почему",
    "зачем",
    "серьёзно",
    "серьезно",
    "не может быть",
    "чёрт",
    "черт",
    "ты шутишь",
    "ты серьёзно",
    "скажи мне",
    "как ты мог",
    "что случилось",
    "что произошло",
    "кто это",
    "ты знаешь",
    "ты сказал",
    "ты имеешь в виду",
    "невероятно",
    "не верю",
    "расскажи",
    "объясни",
)

CONFLICT_SIGNALS: tuple[str, ...] = (
    # English
    "no",
    "never",
    "lied",
    "lie",
    "blame",
    "wrong",
    "angry",
    "anger",
    "fight",
    "accuse",
    "accused",
    "betrayed",
    "betray",
    "hate",
    "hated",
    "shut up",
    "leave me",
    "go away",
    "how dare",
    "fault",
    "your fault",
    "you always",
    "you never",
    "you don't",
    "you can't",
    "you won't",
    "it's over",
    "done with",
    "get out",
    "leave",
    "stop it",
    # Russian
    "нет",
    "никогда",
    "ложь",
    "лжёшь",
    "врёшь",
    "обвиняю",
    "обвинять",
    "виноват",
    "виновата",
    "злой",
    "злая",
    "злится",
    "предал",
    "предала",
    "предательство",
    "ненавижу",
    "замолчи",
    "уходи",
    "оставь меня",
    "как ты смеешь",
    "как ты смеёшь",
    "твоя вина",
    "ты всегда",
    "ты никогда",
    "ты не можешь",
    "всё кончено",
    "убирайся",
    "не смей",
    "хватит",
    "не верю тебе",
    "ты лжёшь",
    "это твоя вина",
)

ESCALATION_SIGNALS: tuple[str, ...] = (
    # English
    "you always",
    "you never",
    "every time",
    "every single",
    "again",
    "still",
    "enough",
    "that's it",
    "that's enough",
    "i'm done",
    "i give up",
    "once and for all",
    "for the last time",
    "over and over",
    "constantly",
    "all the time",
    "you never listen",
    "you never care",
    "stop this",
    # Russian
    "ты всегда",
    "ты никогда",
    "каждый раз",
    "снова",
    "опять",
    "хватит",
    "всё",
    "достаточно",
    "я устал",
    "я устала",
    "я сдаюсь",
    "раз и навсегда",
    "в последний раз",
    "постоянно",
    "всё время",
    "ты не слушаешь",
    "ты не слышишь",
    "прекрати это",
    "до чего дошло",
    "так нельзя",
    "сколько можно",
)

EMOTION_SIGNALS: tuple[str, ...] = (
    # English
    "cry",
    "crying",
    "tears",
    "laugh",
    "laughing",
    "smile",
    "smiling",
    "angry",
    "anger",
    "scared",
    "fear",
    "afraid",
    "hurt",
    "hurts",
    "pain",
    "love",
    "loved",
    "hate",
    "hated",
    "miss",
    "missed",
    "lonely",
    "alone",
    "happy",
    "happiness",
    "sad",
    "sadness",
    "broken",
    "shaking",
    "trembling",
    "sobbing",
    "screaming",
    "yelling",
    "whisper",
    # Russian
    "плачет",
    "плачу",
    "слёзы",
    "смеётся",
    "смеюсь",
    "улыбается",
    "злится",
    "злюсь",
    "боюсь",
    "страх",
    "испугался",
    "испугалась",
    "больно",
    "боль",
    "люблю",
    "любишь",
    "ненавижу",
    "скучаю",
    "одиноко",
    "одинок",
    "один",
    "одна",
    "счастлив",
    "счастлива",
    "грустно",
    "грусть",
    "разбит",
    "разбита",
    "дрожит",
    "дрожу",
    "кричит",
    "кричу",
    "шепчет",
    "шепчу",
)

PAYOFF_SIGNALS: tuple[str, ...] = (
    # English
    "finally",
    "that's why",
    "because",
    "the truth",
    "i understand",
    "i see now",
    "i see",
    "resolution",
    "sorry",
    "i'm sorry",
    "forgive",
    "forgive me",
    "you're right",
    "you were right",
    "i was wrong",
    "it makes sense",
    "now i know",
    "i realise",
    "i realize",
    "i admit",
    "i was afraid",
    "i couldn't",
    "thank you",
    "i love you",
    "i needed",
    "it's okay",
    "we're okay",
    "everything will",
    "it will be",
    # Russian
    "наконец",
    "наконец-то",
    "вот почему",
    "потому что",
    "правда",
    "я понял",
    "я поняла",
    "я понимаю",
    "теперь понимаю",
    "извини",
    "извините",
    "прости",
    "простите",
    "ты прав",
    "ты права",
    "ты был прав",
    "ты была права",
    "я был неправ",
    "я была неправа",
    "теперь я знаю",
    "я осознал",
    "я осознала",
    "признаю",
    "я боялся",
    "я боялась",
    "не мог",
    "не могла",
    "спасибо",
    "я люблю тебя",
    "всё будет",
    "всё хорошо",
    "мне нужно было",
)

CAUSAL_MARKERS: tuple[str, ...] = (
    # English
    "because",
    "therefore",
    "so",
    "thus",
    "as a result",
    "that's why",
    "due to",
    "which means",
    "which led",
    "which caused",
    "consequently",
    "hence",
    "for that reason",
    "as a consequence",
    "that is why",
    # Russian
    "потому что",
    "поэтому",
    "из-за",
    "в результате",
    "вот почему",
    "следовательно",
    "значит",
    "отсюда",
    "это означает",
    "по этой причине",
    "в связи с",
    "вследствие",
    "тем самым",
    "что привело",
)


# ---------------------------------------------------------------------------
# Fragmentation tuning constants
# ---------------------------------------------------------------------------

_HARD_TOPIC_GAP: float = 2.5  # seconds
_NARRATIVE_BEAT_GAP: float = 0.5  # minimum gap to consider beat change
_LONG_CHUNK_DURATION: float = 20.0  # seconds
_LONG_CHUNK_MIN_TURNS: int = 3
_LONG_CHUNK_GAP: float = 1.0
_LONG_CHUNK_MAX_OVERLAP: float = (
    1.0  # token_overlap <= this (i.e. always true unless identical)
)
_SPEAKER_PIVOT_HOOK_SCORE: float = 0.2
_SPEAKER_PIVOT_GAP: float = 0.8
_SPEAKER_PIVOT_MIN_TURNS: int = 2

_MIN_TOKEN_LEN: int = 4


# ---------------------------------------------------------------------------
# StoryFragment dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StoryFragment:
    """A semantically coherent narrative unit carved from dialogue turns."""

    start: float
    end: float
    transcript: str
    speakers: list[str]
    emotion_signals: list[str]
    conflict_signals: list[str]
    causal_markers: list[str]
    topic_tokens: frozenset[str]
    silence_metadata: dict[str, Any]
    role: str = "context"  # hook | setup | escalation | payoff | context | hook_setup
    turn_count: int = 0
    hook_score: float = 0.0
    conflict_score: float = 0.0
    emotion_score: float = 0.0
    payoff_score: float = 0.0
    escalation_score: float = 0.0

    # ------------------------------------------------------------------
    @property
    def duration(self) -> float:
        return round(self.end - self.start, 4)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "transcript": self.transcript,
            "speakers": self.speakers,
            "emotion_signals": self.emotion_signals,
            "conflict_signals": self.conflict_signals,
            "causal_markers": self.causal_markers,
            "topic_tokens": sorted(self.topic_tokens),
            "silence_metadata": self.silence_metadata,
            "role": self.role,
            "turn_count": self.turn_count,
            "hook_score": round(self.hook_score, 4),
            "conflict_score": round(self.conflict_score, 4),
            "emotion_score": round(self.emotion_score, 4),
            "payoff_score": round(self.payoff_score, 4),
            "escalation_score": round(self.escalation_score, 4),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_story_fragments(
    turns: list[dict[str, Any]] | None,
    *,
    max_fragments: int = 6,
) -> list[StoryFragment]:
    """Convert ordered dialogue turns into scored StoryFragments.

    Splitting is SEMANTIC (not purely temporal): the algorithm looks at
    topic shift, narrative beat change, chunk length, and speaker pivots.

    Parameters
    ----------
    turns:
        Ordered turn dicts as produced by ``extract_dialogue_turns``.
    max_fragments:
        Hard upper bound on the number of fragments returned.  When more
        candidate chunks are produced, adjacent ones with the lowest
        boundary score are merged until the limit is reached.

    Returns
    -------
    list[StoryFragment]
        Fragments in chronological order with roles assigned semantically.
    """
    if not turns:
        return []

    chunks = _split_into_chunks(turns)

    # Enforce max_fragments by merging weakest boundaries
    while len(chunks) > max_fragments:
        chunks = _merge_weakest_boundary(chunks)

    fragments = [_chunk_to_fragment(chunk) for chunk in chunks]
    _assign_roles(fragments)
    return fragments


def fragments_to_dicts(
    fragments: list[StoryFragment] | None,
) -> list[dict[str, Any]]:
    """Serialise a list of StoryFragment objects to plain dicts."""
    if not fragments:
        return []
    return [f.to_dict() for f in fragments]


# ---------------------------------------------------------------------------
# Splitting logic
# ---------------------------------------------------------------------------


def _split_into_chunks(
    turns: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Apply semantic splitting rules and return a list of turn-chunks."""
    if not turns:
        return []

    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [turns[0]]

    for i in range(1, len(turns)):
        prev = turns[i - 1]
        curr = turns[i]
        gap = curr["start"] - prev["end"]
        current_duration = current[-1]["end"] - current[0]["start"]
        current_tokens = _topic_tokens(current)
        next_tokens = _topic_tokens([curr])
        t_overlap = _jaccard(current_tokens, next_tokens)

        split = False

        # Rule 1 — Hard topic shift
        if (
            gap > _HARD_TOPIC_GAP
            and t_overlap == 0.0
            and curr.get("speaker") != prev.get("speaker")
        ):
            split = True

        # Rule 2 — Narrative beat change
        if not split and gap >= _NARRATIVE_BEAT_GAP:
            chunk_conflict = _chunk_conflict_score(current)
            next_payoff = _score_text(curr.get("text", ""), PAYOFF_SIGNALS)
            if chunk_conflict >= 0.3 and next_payoff >= 0.25:
                split = True

        # Rule 3 — Long chunk with sufficient gap and low topic overlap
        if not split:
            if (
                current_duration >= _LONG_CHUNK_DURATION
                and len(current) >= _LONG_CHUNK_MIN_TURNS
                and t_overlap <= _LONG_CHUNK_MAX_OVERLAP
                and gap > _LONG_CHUNK_GAP
            ):
                split = True

        # Rule 4 — Speaker pivot with hook/conflict signal
        if not split:
            new_speaker = curr.get("speaker") != prev.get("speaker")
            if (
                new_speaker
                and gap > _SPEAKER_PIVOT_GAP
                and len(current) >= _SPEAKER_PIVOT_MIN_TURNS
            ):
                pivot_hook = _score_text(curr.get("text", ""), HOOK_SIGNALS)
                pivot_conflict = _score_text(curr.get("text", ""), CONFLICT_SIGNALS)
                if max(pivot_hook, pivot_conflict) >= _SPEAKER_PIVOT_HOOK_SCORE:
                    split = True

        if split:
            chunks.append(current)
            current = [curr]
        else:
            current.append(curr)

    chunks.append(current)
    return chunks


def _merge_weakest_boundary(
    chunks: list[list[dict[str, Any]]],
) -> list[list[dict[str, Any]]]:
    """Merge the pair of adjacent chunks with the smallest gap between them."""
    if len(chunks) <= 1:
        return chunks

    # Find the boundary with the smallest temporal gap
    min_gap = float("inf")
    min_idx = 0
    for i in range(len(chunks) - 1):
        gap = chunks[i + 1][0]["start"] - chunks[i][-1]["end"]
        if gap < min_gap:
            min_gap = gap
            min_idx = i

    merged = chunks[min_idx] + chunks[min_idx + 1]
    return chunks[:min_idx] + [merged] + chunks[min_idx + 2 :]


# ---------------------------------------------------------------------------
# Fragment construction
# ---------------------------------------------------------------------------


def _chunk_to_fragment(chunk: list[dict[str, Any]]) -> StoryFragment:
    """Build a StoryFragment from a list of dialogue turns."""
    start = chunk[0]["start"]
    end = chunk[-1]["end"]
    transcript = " ".join(t.get("text", "") for t in chunk if t.get("text", "")).strip()

    # Speakers — deduplicated, first-appearance order
    seen_speakers: set[str] = set()
    speakers: list[str] = []
    for t in chunk:
        sp = str(t.get("speaker", "UNKNOWN") or "UNKNOWN")
        if sp not in seen_speakers:
            seen_speakers.add(sp)
            speakers.append(sp)

    # Signal extraction
    emotion_signals = _find_signals(transcript, EMOTION_SIGNALS)
    conflict_signals = _find_signals(transcript, CONFLICT_SIGNALS)
    causal_markers_found = _find_signals(transcript, CAUSAL_MARKERS)
    topic_tokens = _topic_tokens(chunk)

    # Silence / gap metadata
    leading_gap = chunk[0]["start"] - 0.0  # relative; caller may adjust
    trailing_gap = 0.0  # unknown without total duration context
    pause_signals: list[float] = []
    for i in range(1, len(chunk)):
        g = chunk[i]["start"] - chunk[i - 1]["end"]
        if g > 0.3:
            pause_signals.append(round(g, 3))

    silence_metadata: dict[str, Any] = {
        "leading_gap": round(leading_gap, 4),
        "trailing_gap": round(trailing_gap, 4),
        "pause_signals": pause_signals,
    }

    fragment = StoryFragment(
        start=round(start, 4),
        end=round(end, 4),
        transcript=transcript,
        speakers=speakers,
        emotion_signals=emotion_signals,
        conflict_signals=conflict_signals,
        causal_markers=causal_markers_found,
        topic_tokens=topic_tokens,
        silence_metadata=silence_metadata,
        turn_count=len(chunk),
    )

    _score_fragment(fragment)
    return fragment


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_fragment(fragment: StoryFragment) -> None:
    """Populate hook / conflict / escalation / emotion / payoff scores in-place."""
    text = fragment.transcript
    fragment.hook_score = _score_text(text, HOOK_SIGNALS)
    fragment.conflict_score = _score_text(text, CONFLICT_SIGNALS)
    fragment.escalation_score = _score_text(text, ESCALATION_SIGNALS)
    fragment.emotion_score = _score_text(text, EMOTION_SIGNALS)
    fragment.payoff_score = _score_text(text, PAYOFF_SIGNALS)


def _score_text(text: str, signals: tuple[str, ...]) -> float:
    """Return a normalised score [0, 1] for how many signals appear in text.

    Each signal match adds 1 / len(signals) to the raw score, which is
    then clamped to [0, 1].  Multi-word signals are matched as substrings
    (case-insensitive).
    """
    if not text or not signals:
        return 0.0
    lower = text.lower()
    hits = sum(1 for sig in signals if sig in lower)
    return min(1.0, hits / max(len(signals), 1))


def _chunk_conflict_score(chunk: list[dict[str, Any]]) -> float:
    """Average conflict score across all turns in a chunk."""
    if not chunk:
        return 0.0
    scores = [_score_text(t.get("text", ""), CONFLICT_SIGNALS) for t in chunk]
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------


def _assign_roles(fragments: list[StoryFragment]) -> None:
    """Assign narrative roles to fragments based on their scores.

    Roles are assigned semantically, NOT by position index:
    - HOOK       : highest (hook_score + 0.15 bonus if first)
    - PAYOFF     : highest (payoff_score + 0.3 * emotion_score + 0.15 bonus if last)
    - ESCALATION : highest (conflict_score + escalation_score) from remaining
    - SETUP      : all remaining fragments

    A fragment that scores highly on both hook and payoff criteria (and is
    also the first fragment) receives the special role "hook_setup".
    """
    if not fragments:
        return

    n = len(fragments)
    last = n - 1

    # --- Hook candidate ---
    hook_scores = []
    for i, f in enumerate(fragments):
        bonus = 0.15 if i == 0 else 0.0
        hook_scores.append(f.hook_score + bonus)

    hook_idx = hook_scores.index(max(hook_scores))

    # --- Payoff candidate ---
    payoff_scores = []
    for i, f in enumerate(fragments):
        bonus = 0.15 if i == last else 0.0
        payoff_scores.append(f.payoff_score + 0.3 * f.emotion_score + bonus)

    payoff_idx = payoff_scores.index(max(payoff_scores))

    # If hook and payoff point to the same fragment and it's the first one,
    # label it hook_setup and find the next best payoff.
    if hook_idx == payoff_idx and hook_idx == 0 and n > 1:
        # Special combined role
        fragments[hook_idx].role = "hook_setup"
        assigned = {hook_idx}

        # Find best remaining payoff
        remaining_payoff = [
            (payoff_scores[i], i) for i in range(n) if i not in assigned
        ]
        if remaining_payoff:
            payoff_idx = max(remaining_payoff)[1]
            fragments[payoff_idx].role = "payoff"
            assigned.add(payoff_idx)
    else:
        assigned: set[int] = set()
        fragments[hook_idx].role = "hook"
        assigned.add(hook_idx)
        fragments[payoff_idx].role = "payoff"
        assigned.add(payoff_idx)

    # --- Escalation candidate from remainder ---
    escalation_scores = [
        (f.conflict_score + f.escalation_score, i)
        for i, f in enumerate(fragments)
        if i not in assigned
    ]
    if escalation_scores:
        best_escalation = max(escalation_scores)
        # Only assign escalation role if the score is non-trivial
        if best_escalation[0] > 0.0:
            escalation_idx = best_escalation[1]
            fragments[escalation_idx].role = "escalation"
            assigned.add(escalation_idx)

    # --- Everything else becomes setup ---
    for i, f in enumerate(fragments):
        if i not in assigned:
            f.role = "setup"


# ---------------------------------------------------------------------------
# Token helpers
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
        # Russian
        "это",
        "этот",
        "этой",
        "эти",
        "тебя",
        "тебе",
        "меня",
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


def _topic_tokens(
    turns: list[dict[str, Any]],
    limit: int = 8,
) -> frozenset[str]:
    """Extract the top *limit* meaningful content tokens from a set of turns."""
    freq: dict[str, int] = {}
    for turn in turns:
        text = str(turn.get("text", "") or "")
        for raw_token in re.split(r"[\s\W]+", text.lower()):
            token = raw_token.strip()
            if len(token) >= _MIN_TOKEN_LEN and token not in _STOP_WORDS:
                freq[token] = freq.get(token, 0) + 1
    top = sorted(freq, key=lambda t: freq[t], reverse=True)[:limit]
    return frozenset(top)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two frozensets."""
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _find_signals(
    text: str,
    signals: tuple[str, ...],
) -> list[str]:
    """Return the subset of signals that appear in *text* (case-insensitive)."""
    if not text:
        return []
    lower = text.lower()
    return [sig for sig in signals if sig in lower]
