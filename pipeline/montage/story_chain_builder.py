"""
story_chain_builder.py
----------------------
Build StoryChains from StoryFragments using semantic criteria.

Pipeline position: StoryFragments -> StoryChains -> MontageAssembly

A StoryChain must have: hook, setup, escalation, payoff.
If payoff is missing, try_extend_chain_for_payoff() searches adjacent
conversation blocks for a matching payoff fragment.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .story_fragments import StoryFragment, build_story_fragments, fragments_to_dicts

# ---------------------------------------------------------------------------
# Stop-word lists (Russian + English)
# ---------------------------------------------------------------------------

_STOP_WORDS: frozenset[str] = frozenset(
    {
        # English
        "the",
        "and",
        "that",
        "this",
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
        "you",
        "him",
        "her",
        "its",
        "but",
        "not",
        "can",
        "was",
        "are",
        "for",
        "had",
        "has",
        "his",
        "all",
        "one",
        "two",
        "our",
        "out",
        "any",
        "too",
        "few",
        "let",
        "put",
        "get",
        "got",
        "see",
        "say",
        "did",
        "use",
        "try",
        "may",
        "now",
        "how",
        "who",
        "why",
        "yet",
        "set",
        "own",
        "way",
        "off",
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
        "свой",
        "своя",
        "своё",
        "свои",
        "который",
        "которые",
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
        "надо",
        "нужно",
        "можно",
        "нельзя",
        "хочу",
        "хочет",
        "знаю",
        "знает",
        "идти",
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
        "что",
        "как",
        "все",
        "для",
        "или",
        "его",
        "она",
        "они",
        "так",
        "но",
        "ну",
        "да",
        "нет",
        "вот",
        "мне",
        "тут",
        "там",
        "где",
        "под",
        "над",
        "при",
        "без",
        "нас",
        "вас",
        "вам",
        "ему",
        "ей",
        "раз",
        "два",
        "три",
        "той",
        "том",
        "тех",
        "тем",
    }
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _join_nonempty(parts: Iterable[str], separator: str = " ") -> str:
    return separator.join(
        part for part in (str(item or "").strip() for item in parts) if part
    )


def _fragment_text_by_role(fragments: list[StoryFragment], role: str) -> str:
    """Return transcript of the first fragment matching *role* (case-insensitive)."""
    for fragment in fragments:
        frag_role = str(getattr(fragment, "role", "") or "").casefold()
        if frag_role == role.casefold():
            text = _clean_text(getattr(fragment, "transcript", "") or "")
            if text:
                return text
    return ""


def _token_set_from_text(text: str) -> frozenset[str]:
    """Extract meaningful content tokens from *text*, excluding stop words."""
    return frozenset(
        token
        for token in re.findall(r"[\w\u0400-\u04FF']+", _clean_text(text).casefold())
        if len(token) > 3 and token not in _STOP_WORDS
    )


def _fragment_topic_tokens(fragment: StoryFragment) -> frozenset[str]:
    """Return topic tokens for *fragment*, preferring the attribute if present."""
    existing = getattr(fragment, "topic_tokens", None)
    if existing is not None and isinstance(existing, (frozenset, set)):
        return frozenset(existing)
    return _token_set_from_text(getattr(fragment, "transcript", "") or "")


def _fragment_payoff_score(fragment: StoryFragment) -> float:
    """Return the payoff_score attribute if present, else 0.0."""
    return float(getattr(fragment, "payoff_score", 0.0) or 0.0)


def _arc_shape(hook: str, setup: str, escalation: str, payoff: str) -> str:
    filled = sum(1 for part in (hook, setup, escalation, payoff) if part)
    if filled >= 4:
        return "hook_setup_escalation_payoff"
    if filled == 3:
        return "hook_setup_escalation"
    if filled == 2:
        return "hook_setup"
    if filled == 1:
        return "hook_only"
    return "incomplete_story_chain"


def _detect_conflict_type(conflict_signals: list[str], escalation_text: str) -> str:
    all_text = _clean_text(
        " ".join(list(conflict_signals or []) + [escalation_text or ""])
    ).casefold()
    if any(t in all_text for t in ("accus", "обвин", "blame", "вина")):
        return "accusation"
    if any(t in all_text for t in ("deny", "отрица", "не правда", "неправда")):
        return "denial"
    if any(t in all_text for t in ("betray", "предал", "предател", "изменил")):
        return "betrayal"
    if any(t in all_text for t in ("reveal", "правд", "узнал", "понял", "truth")):
        return "revelation"
    if any(t in all_text for t in ("threat", "угроз", "danger", "опас")):
        return "threat"
    if any(t in all_text for t in ("fight", "спор", "ссора", "argument")):
        return "argument"
    if conflict_signals:
        return "argument"
    return "none"


def _detect_hook_type(text: str) -> str:
    lowered = _clean_text(text).casefold()
    if any(
        t in lowered
        for t in (
            "?",
            "why",
            "what",
            "who",
            "how",
            "where",
            "почему",
            "что",
            "кто",
            "зачем",
            "когда",
        )
    ):
        return "question"
    if any(
        t in lowered
        for t in ("accus", "обвин", "blame", "вина", "you did", "ты сделал")
    ):
        return "accusation_denial"
    if any(
        t in lowered
        for t in ("threat", "угроз", "danger", "опас", "or else", "или иначе")
    ):
        return "threat_tension"
    if any(
        t in lowered
        for t in ("reveal", "truth", "правд", "узнал", "понял", "found out", "discover")
    ):
        return "reveal_discovery"
    if any(
        t in lowered
        for t in ("laugh", "шут", "смеш", "comedy", "шутк", "funny", "joke")
    ):
        return "funny_setup"
    if any(
        t in lowered for t in ("embarr", "стыд", "awkward", "нелов", "uncomfortable")
    ):
        return "social_awkwardness"
    return "balanced_hook"


def _detect_payoff_type(text: str) -> str:
    lowered = _clean_text(text).casefold()
    if not lowered:
        return "unfinished"
    if any(
        t in lowered
        for t in ("reveal", "truth", "правд", "раскр", "узнал", "понял", "found out")
    ):
        return "reveal"
    if any(
        t in lowered
        for t in ("laugh", "смеш", "шут", "comedy", "punchline", "funny", "joke")
    ):
        return "punchline"
    if any(
        t in lowered for t in ("sorry", "извин", "forgive", "мир", "peace", "прости")
    ):
        return "resolution"
    if any(t in lowered for t in ("agree", "соглас", "окей", "ладно", "fine", "okay")):
        return "resolution"
    if any(t in lowered for t in ("fight", "argument", "спор", "ссора", "blow")):
        return "conflict"
    return "resolution"


def _extract_topic_phrase(text: str, fallback: str = "") -> str:
    """Extract 3-5 meaningful words that describe the story's subject."""
    combined = _clean_text(text or fallback)
    if not combined:
        return ""
    tokens = [
        token
        for token in re.findall(r"[\w\u0400-\u04FF']+", combined.casefold())
        if len(token) > 3 and token not in _STOP_WORDS
    ]
    if not tokens:
        # Relax the filter to any token > 2 chars
        tokens = [
            token
            for token in re.findall(r"[\w\u0400-\u04FF']+", combined.casefold())
            if len(token) > 2
        ]
    seen: list[str] = []
    seen_set: set[str] = set()
    for token in tokens:
        if token not in seen_set:
            seen_set.add(token)
            seen.append(token)
        if len(seen) >= 5:
            break
    return " ".join(seen[:5])


def _extract_topic_terms(text: str, limit: int = 4) -> list[str]:
    """Extract *limit* meaningful content words sorted by frequency."""
    tokens = [
        token
        for token in re.findall(r"[\w\u0400-\u04FF']+", _clean_text(text).casefold())
        if len(token) > 3 and token not in _STOP_WORDS
    ]
    freq = Counter(tokens)
    return [word for word, _ in freq.most_common(limit)]


def _build_title_seed(hook: str, escalation: str, payoff: str) -> str:
    """Build a title seed from actual story content, not technical labels."""
    parts = [hook, escalation]
    if payoff and payoff not in (hook, escalation):
        parts.append(payoff)
    result = _join_nonempty(parts, " ")
    if not result:
        result = hook or escalation or payoff
    # Cap at 20 words so titles stay usable
    words = result.split()
    if len(words) > 20:
        result = " ".join(words[:20])
    return result


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StorySummary:
    conversation_id: str
    story_arc_shape: str
    hook: str
    setup: str
    escalation: str
    payoff: str
    summary_text: str
    title_seed: str  # MUST be actual story content, NOT technical labels
    hook_type: str  # question | accusation_denial | threat_tension |
    # reveal_discovery | funny_setup | social_awkwardness |
    # balanced_hook
    payoff_type: str  # reveal | punchline | resolution | conflict | unfinished
    characters: list[str] = field(default_factory=list)
    topic_terms: list[str] = field(default_factory=list)  # meaningful content words
    emotions: list[str] = field(default_factory=list)
    conflict_signals: list[str] = field(default_factory=list)
    conflict_type: str = "none"  # accusation | denial | betrayal |
    # revelation | argument | threat | none
    topic_phrase: str = ""  # 3-5 word phrase describing the story
    story_deficient: bool = False  # True when is_complete is False
    story_completion_score: float = 0.0
    context_completeness_score: float = 0.0
    confidence: float = 0.0
    is_complete: bool = False

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "story_thread_id": self.conversation_id,
            "story_arc_shape": self.story_arc_shape,
            "hook": self.hook,
            "setup": self.setup,
            "escalation": self.escalation,
            "payoff": self.payoff,
            "summary_text": self.summary_text,
            "title_seed": self.title_seed,
            "hook_type": self.hook_type,
            "payoff_type": self.payoff_type,
            "characters": list(self.characters),
            "topic_terms": list(self.topic_terms),
            "emotions": list(self.emotions),
            "conflict_signals": list(self.conflict_signals),
            "conflict_type": self.conflict_type,
            "topic_phrase": self.topic_phrase,
            "story_deficient": bool(self.story_deficient),
            "story_completion_score": round(float(self.story_completion_score), 4),
            "context_completeness_score": round(
                float(self.context_completeness_score), 4
            ),
            "confidence": round(float(self.confidence), 4),
            "is_complete": bool(self.is_complete),
        }


@dataclass(slots=True)
class StoryChain:
    conversation_id: str
    fragments: list[StoryFragment] = field(default_factory=list)
    hook: str = ""
    setup: str = ""
    escalation: str = ""
    payoff: str = ""
    story_arc_shape: str = "incomplete_story_chain"
    summary: StorySummary | None = None
    speakers: list[str] = field(default_factory=list)
    conflict_type: str = "none"
    topic_tokens: frozenset[str] = field(default_factory=frozenset)
    start: float = 0.0
    end: float = 0.0
    is_complete: bool = False
    completion_score: float = 0.0
    search_extended: bool = False  # True when payoff came from extension search

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "story_thread_id": self.conversation_id,
            "fragments": fragments_to_dicts(self.fragments),
            "hook": self.hook,
            "setup": self.setup,
            "escalation": self.escalation,
            "payoff": self.payoff,
            "story_arc_shape": self.story_arc_shape,
            "summary": self.summary.to_dict() if self.summary else None,
            "speakers": list(self.speakers),
            "conflict_type": self.conflict_type,
            "topic_tokens": sorted(self.topic_tokens),
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "duration": round(max(0.0, float(self.end) - float(self.start)), 3),
            "is_complete": bool(self.is_complete),
            "completion_score": round(float(self.completion_score), 4),
            "search_extended": bool(self.search_extended),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_story_chain(
    fragments: Iterable[StoryFragment],
    *,
    conversation_id: str = "",
) -> StoryChain:
    """Build a StoryChain from an ordered iterable of StoryFragments.

    Arc elements are sourced from fragments by their *role* field.
    Completion score = 0.25 * count(non-empty arc elements).
    """
    ordered = list(fragments or [])

    # -- Extract arc text by role ------------------------------------------
    hook = _fragment_text_by_role(ordered, "hook")
    setup = _fragment_text_by_role(ordered, "setup")
    escalation = _fragment_text_by_role(ordered, "escalation")
    payoff = _fragment_text_by_role(ordered, "payoff")

    # Combined hook_setup role (single-fragment stories)
    if not hook:
        hook_setup_text = _fragment_text_by_role(ordered, "hook_setup")
        if hook_setup_text:
            hook = hook_setup_text

    # Positional fallbacks when roles did not yield enough content
    if not hook and ordered:
        hook = _clean_text(getattr(ordered[0], "transcript", "") or "")

    if not setup and len(ordered) > 1:
        for frag in ordered[1:]:
            role = str(getattr(frag, "role", "") or "").casefold()
            text = _clean_text(getattr(frag, "transcript", "") or "")
            if text and role not in ("hook", "payoff", "escalation"):
                setup = text
                break

    if not escalation and len(ordered) > 2:
        for frag in ordered:
            role = str(getattr(frag, "role", "") or "").casefold()
            text = _clean_text(getattr(frag, "transcript", "") or "")
            if text and role not in ("hook", "setup", "payoff", "hook_setup"):
                if text not in (hook, setup):
                    escalation = text
                    break

    if not payoff and ordered:
        last_text = _clean_text(getattr(ordered[-1], "transcript", "") or "")
        # Don't reuse hook text as payoff for single-fragment chains
        if last_text and (len(ordered) > 1 or last_text != hook):
            payoff = last_text

    # -- Collect speakers from all fragments --------------------------------
    speakers: list[str] = []
    seen_sp: set[str] = set()
    for frag in ordered:
        for sp in getattr(frag, "speakers", None) or []:
            sp_clean = _clean_text(sp)
            if sp_clean and sp_clean.casefold() not in seen_sp:
                seen_sp.add(sp_clean.casefold())
                speakers.append(sp_clean)

    # -- Union of topic_tokens from all fragments ---------------------------
    topic_tokens: frozenset[str] = frozenset()
    for frag in ordered:
        topic_tokens = topic_tokens | _fragment_topic_tokens(frag)

    # -- Aggregate conflict signals -----------------------------------------
    all_conflict_signals: list[str] = []
    seen_cs: set[str] = set()
    for frag in ordered:
        for cs in getattr(frag, "conflict_signals", None) or []:
            if cs and cs not in seen_cs:
                seen_cs.add(cs)
                all_conflict_signals.append(cs)

    conflict_type = _detect_conflict_type(all_conflict_signals, escalation)

    # -- Timing ----------------------------------------------------------------
    start = _as_float(getattr(ordered[0], "start", 0.0)) if ordered else 0.0
    end = _as_float(getattr(ordered[-1], "end", start)) if ordered else 0.0

    # -- Completion --------------------------------------------------------
    completion_score = 0.25 * sum(
        1 for part in (hook, setup, escalation, payoff) if part
    )
    is_complete = bool(hook and setup and escalation and payoff)

    return StoryChain(
        conversation_id=str(conversation_id or ""),
        fragments=ordered,
        hook=hook,
        setup=setup,
        escalation=escalation,
        payoff=payoff,
        story_arc_shape=_arc_shape(hook, setup, escalation, payoff),
        summary=None,
        speakers=speakers,
        conflict_type=conflict_type,
        topic_tokens=topic_tokens,
        start=start,
        end=end,
        is_complete=is_complete,
        completion_score=completion_score,
        search_extended=False,
    )


def build_story_summary(
    fragments: Iterable[StoryFragment],
    *,
    conversation_id: str = "",
    source_text: str = "",
    language: str = "auto",
) -> StorySummary:
    """Build a StorySummary from StoryFragments.

    Derives all semantic metadata (hook_type, payoff_type, conflict_type,
    topic_phrase, topic_terms, title_seed) from actual story text, never
    from technical field names or labels.
    """
    ordered = list(fragments or [])
    chain = build_story_chain(ordered, conversation_id=conversation_id)

    hook = chain.hook.strip()
    setup = chain.setup.strip()
    escalation = chain.escalation.strip()
    payoff = chain.payoff.strip()

    # title_seed: hook + escalation (or payoff) — actual story content
    title_seed = _build_title_seed(hook, escalation, payoff)
    if not title_seed:
        title_seed = _clean_text(source_text) or setup

    # topic_phrase: 3-5 meaningful words from escalation/hook text
    topic_phrase = _extract_topic_phrase(
        _join_nonempty([escalation, hook], " "),
        fallback=_join_nonempty([setup, payoff], " "),
    )

    # topic_terms: 3-4 meaningful content words from escalation + conflict text
    escalation_and_conflict = _join_nonempty([escalation, source_text], " ")
    topic_terms = _extract_topic_terms(
        _join_nonempty([escalation_and_conflict, hook], " "), limit=4
    )
    if not topic_terms:
        topic_terms = _extract_topic_terms(
            _join_nonempty([hook, setup, escalation, payoff], " "), limit=4
        )

    # Detect types
    hook_type = _detect_hook_type(hook or setup)
    payoff_type = _detect_payoff_type(payoff)
    conflict_type = chain.conflict_type

    # Characters
    characters = list(chain.speakers)

    # Emotions: sorted union from all fragments
    emotions_set: set[str] = set()
    for frag in ordered:
        for sig in getattr(frag, "emotion_signals", None) or []:
            if sig:
                emotions_set.add(sig)
    emotions = sorted(emotions_set)

    # Conflict signals: sorted union from all fragments
    conflict_signals_set: set[str] = set()
    for frag in ordered:
        for cs in getattr(frag, "conflict_signals", None) or []:
            if cs:
                conflict_signals_set.add(cs)
    conflict_signals = sorted(conflict_signals_set)

    # Scores
    story_completion_score = chain.completion_score
    is_complete = chain.is_complete
    context_completeness_score = min(
        1.0,
        0.25 * (1 if characters else 0)
        + 0.25 * (1 if topic_terms else 0)
        + 0.25 * (1 if setup else 0)
        + 0.25 * (1 if escalation else 0),
    )
    story_deficient = not is_complete
    confidence = min(
        1.0,
        0.25 + story_completion_score * 0.35 + context_completeness_score * 0.35,
    )

    summary_text = _join_nonempty([hook, setup, escalation, payoff], " ")

    return StorySummary(
        conversation_id=chain.conversation_id,
        story_arc_shape=chain.story_arc_shape,
        hook=hook,
        setup=setup,
        escalation=escalation,
        payoff=payoff,
        summary_text=summary_text,
        title_seed=title_seed,
        hook_type=hook_type,
        payoff_type=payoff_type,
        characters=characters,
        topic_terms=topic_terms,
        emotions=emotions,
        conflict_signals=conflict_signals,
        conflict_type=conflict_type,
        topic_phrase=topic_phrase,
        story_deficient=story_deficient,
        story_completion_score=story_completion_score,
        context_completeness_score=context_completeness_score,
        confidence=confidence,
        is_complete=is_complete,
    )


def build_story_summary_from_turns(
    turns: Iterable[dict],
    *,
    conversation_id: str = "",
    source_text: str = "",
    language: str = "auto",
) -> StorySummary:
    """Convenience wrapper: turns -> fragments -> StorySummary."""
    fragments = build_story_fragments(turns)
    return build_story_summary(
        fragments,
        conversation_id=conversation_id,
        source_text=source_text,
        language=language,
    )


def try_extend_chain_for_payoff(
    chain: StoryChain,
    all_blocks: list[dict],
    *,
    max_extension_seconds: float = 180.0,  # PHASE 4: Increased from 120 to 180
) -> StoryChain:
    """Search adjacent blocks for a payoff fragment when chain.is_complete is False.

    Matching criteria (either must be satisfied):
    - speaker_overlap >= 0.3  (PHASE 4: relaxed from 0.4)
    - topic_token_overlap >= 0.18  (PHASE 4: relaxed from 0.25)

    The first qualifying fragment whose payoff_score >= 0.2 (or whose role is
    "payoff") extends the chain. Returns the chain unmodified when no match is
    found.
    """
    # PHASE 4: Check if payoff is weak before skipping extension
    if chain.is_complete:
        # Only skip extension if payoff is strong (score >= 0.40)
        payoff_fragment = None
        for frag in chain.fragments:
            if str(getattr(frag, "role", "") or "").casefold() == "payoff":
                payoff_fragment = frag
                break
        
        if payoff_fragment:
            payoff_score = _fragment_payoff_score(payoff_fragment)
            if payoff_score >= 0.40:
                return chain  # Strong payoff, no extension needed
            # Weak payoff, continue to try extension
        else:
            return chain  # No payoff fragment found, consider complete

    chain_speakers = frozenset(sp.casefold() for sp in chain.speakers)

    for block in all_blocks or []:
        block = block or {}
        block_start = _as_float(block.get("start", float("inf")))

        # Block must begin after chain ends and within the extension window
        if block_start < chain.end:
            continue
        if block_start > chain.end + max_extension_seconds:
            continue

        # -- Speaker overlap ---------------------------------------------------
        raw_block_speakers: list[str] = list(block.get("speakers", None) or [])
        if not raw_block_speakers:
            raw_block_speakers = [
                str((t or {}).get("speaker", "") or "")
                for t in (block.get("turns", None) or [])
            ]
        block_speakers = frozenset(
            sp.casefold() for sp in raw_block_speakers if sp.strip()
        )

        if chain_speakers and block_speakers:
            speaker_overlap = len(chain_speakers & block_speakers) / max(
                len(chain_speakers), len(block_speakers), 1
            )
        else:
            speaker_overlap = 0.0

        # -- Topic-token overlap -----------------------------------------------
        block_turns = list(block.get("turns", None) or [])
        block_topic_tokens: frozenset[str] = frozenset()
        for turn in block_turns:
            text = _clean_text(
                str(
                    (turn or {}).get("text", "")
                    or (turn or {}).get("caption_text", "")
                    or ""
                )
            )
            block_topic_tokens = block_topic_tokens | _token_set_from_text(text)

        if chain.topic_tokens and block_topic_tokens:
            topic_overlap = len(chain.topic_tokens & block_topic_tokens) / max(
                len(chain.topic_tokens), len(block_topic_tokens), 1
            )
        else:
            topic_overlap = 0.0

        # PHASE 4: Relaxed thresholds from 0.4/0.25 to 0.3/0.18
        if speaker_overlap < 0.3 and topic_overlap < 0.18:
            continue

        # -- Find a payoff fragment in the matching block ----------------------
        block_fragments = build_story_fragments(block_turns)
        payoff_fragment: StoryFragment | None = None
        best_score = 0.19  # must exceed 0.2 to qualify

        for frag in block_fragments:
            score = _fragment_payoff_score(frag)
            role = str(getattr(frag, "role", "") or "").casefold()
            if role == "payoff":
                # Role assignment already identified this as payoff
                score = max(score, 0.25)
            if score > best_score:
                best_score = score
                payoff_fragment = frag

        if payoff_fragment is None:
            continue

        # -- Extend the chain --------------------------------------------------
        new_fragments = list(chain.fragments) + [payoff_fragment]
        new_payoff = chain.payoff or _clean_text(
            getattr(payoff_fragment, "transcript", "") or ""
        )
        new_end = max(
            chain.end,
            _as_float(getattr(payoff_fragment, "end", chain.end)),
        )

        new_completion_score = 0.25 * sum(
            1
            for part in (chain.hook, chain.setup, chain.escalation, new_payoff)
            if part
        )
        new_is_complete = bool(
            chain.hook and chain.setup and chain.escalation and new_payoff
        )

        # Merge speakers from payoff fragment
        new_speakers = list(chain.speakers)
        seen_sp = set(sp.casefold() for sp in chain.speakers)
        for sp in getattr(payoff_fragment, "speakers", None) or []:
            sp_clean = _clean_text(sp)
            if sp_clean and sp_clean.casefold() not in seen_sp:
                seen_sp.add(sp_clean.casefold())
                new_speakers.append(sp_clean)

        new_topic_tokens = chain.topic_tokens | _fragment_topic_tokens(payoff_fragment)

        return StoryChain(
            conversation_id=chain.conversation_id,
            fragments=new_fragments,
            hook=chain.hook,
            setup=chain.setup,
            escalation=chain.escalation,
            payoff=new_payoff,
            story_arc_shape=_arc_shape(
                chain.hook, chain.setup, chain.escalation, new_payoff
            ),
            summary=chain.summary,
            speakers=new_speakers,
            conflict_type=chain.conflict_type,
            topic_tokens=new_topic_tokens,
            start=chain.start,
            end=new_end,
            is_complete=new_is_complete,
            completion_score=new_completion_score,
            search_extended=True,
        )

    return chain
