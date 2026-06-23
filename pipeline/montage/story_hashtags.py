from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Generic name blacklist — names in this set are skipped as character tags
# ---------------------------------------------------------------------------
_GENERIC_NAMES: set[str] = {
    "мужчина",
    "женщина",
    "человек",
    "люди",
    "персонаж",
    "герой",
    "героиня",
    "он",
    "она",
    "они",
    "мы",
    "вы",
    "я",
    "ты",
    "оба",
    "все",
    "man",
    "woman",
    "person",
    "people",
    "character",
    "hero",
    "heroine",
    "she",
    "he",
    "they",
    "we",
    "you",
    "i",
    "both",
    "everyone",
    "speaker",
    "voice",
    "narrator",
    # Whisper/diarization fallback names - must never become hashtags
    "unknown",
    "unkn",
    "noname",
    "безымянный",
    "speaker_0",
    "speaker_1",
    "speaker_2",
    "speaker_3",
    "speaker0",
    "speaker1",
    "speaker2",
    "speaker3",
    "spkr",
    "spkr0",
    "spkr1",
    "s0",
    "s1",
    "s2",
    "s3",
    "person_0",
    "person_1",
    "голос",
    "voice_0",
    "voice_1",
}

# ---------------------------------------------------------------------------
# Conflict-type to hashtag mappings
# ---------------------------------------------------------------------------
_CONFLICT_TAGS_RU: dict[str, str] = {
    "accusation": "#обвинение",
    "denial": "#отрицание",
    "betrayal": "#предательство",
    "revelation": "#развязка",
    "argument": "#конфликт",
    "threat": "#угроза",
}

_CONFLICT_TAGS_EN: dict[str, str] = {
    "accusation": "#accusation",
    "denial": "#denial",
    "betrayal": "#betrayal",
    "revelation": "#revelation",
    "argument": "#conflict",
    "threat": "#threat",
}

# ---------------------------------------------------------------------------
# Emotion keyword fragments matched against story_summary["emotions"][0]
# ---------------------------------------------------------------------------
_EMOTION_MAP_RU: list[tuple[list[str], str]] = [
    (["cry", "crying", "плач", "рыдает", "слёзы", "tear"], "#слёзы"),
    (["laugh", "смеётся", "хохот", "смех", "хохочет"], "#смех"),
    (["angry", "anger", "злится", "злость", "ярость", "rage"], "#злость"),
    (["scared", "fear", "боюсь", "страх", "испуг", "ужас"], "#страх"),
    (["shock", "shocked", "шок", "шокирован", "stunned"], "#шок"),
    (["sad", "grief", "горе", "печаль", "грустн", "sorrow"], "#грусть"),
    (["love", "любовь", "влюблён", "нежность", "tender"], "#любовь"),
    (["betrayed", "предал", "предала", "изменил", "изменила"], "#предательство"),
]

_EMOTION_MAP_EN: list[tuple[list[str], str]] = [
    (["cry", "crying", "tears", "sobbing"], "#tears"),
    (["laugh", "laughter", "funny"], "#laugh"),
    (["angry", "anger", "rage", "furious"], "#anger"),
    (["scared", "fear", "afraid", "panic"], "#fear"),
    (["shock", "shocked", "stunned"], "#shock"),
    (["sad", "grief", "sorrow"], "#sadness"),
    (["love", "tender", "affection"], "#love"),
    (["betray", "betrayed"], "#betrayal"),
]

# Topics that are too generic to earn a dedicated hashtag
_GENERIC_TOPICS: set[str] = {
    "none",
    "момент",
    "сцена",
    "scene",
    "moment",
    "dialogue",
    "диалог",
    "разговор",
    "conversation",
    "история",
    "story",
    "episode",
    "эпизод",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_russian_summary(summary: dict) -> bool:
    haystack = " ".join(
        str(summary.get(key, "") or "")
        for key in (
            "hook",
            "setup",
            "escalation",
            "payoff",
            "topic_phrase",
            "title_seed",
        )
    )
    return bool(re.search(r"[\u0400-\u04FF]", haystack))


def _slug_tag(text: str, prefix: str = "#") -> str:
    """Convert a human-readable phrase into a joined hashtag."""
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return ""
    cleaned = cleaned.replace(" ", "_")
    cleaned = re.sub(r"[^\w\u0400-\u04FF_]+", "", cleaned, flags=re.UNICODE)
    cleaned = cleaned.strip("_")
    return f"{prefix}{cleaned}" if cleaned else ""


def _character_tags(summary: dict, russian: bool) -> list[str]:  # noqa: ARG001
    """Return up to 2 hashtags derived from named characters or speakers."""
    chars: list[str] = list(summary.get("characters") or summary.get("speakers") or [])
    tags: list[str] = []
    for name in chars[:4]:
        name_clean = re.sub(r"\s+", " ", str(name or "").strip())
        if len(name_clean) <= 2:
            continue
        if name_clean.lower() in _GENERIC_NAMES:
            continue
        # Accept only strings that look like real names (letters, hyphens, dots)
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё\s\-'\.]{3,}", name_clean):
            continue
        tag = _slug_tag(name_clean)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 2:
            break
    return tags


def _conflict_tag(summary: dict, russian: bool) -> str | None:
    """Map conflict_type to a descriptive hashtag, or None for 'none'."""
    conflict_type = str(summary.get("conflict_type") or "").lower().strip()
    if not conflict_type or conflict_type == "none":
        return None
    mapping = _CONFLICT_TAGS_RU if russian else _CONFLICT_TAGS_EN
    return mapping.get(conflict_type)


def _emotion_tag(summary: dict, russian: bool) -> str | None:
    """Map the first (dominant) emotion string to a hashtag."""
    emotions: list[str] = list(summary.get("emotions") or [])
    if not emotions:
        return None
    dominant = str(emotions[0]).lower()
    keyword_map = _EMOTION_MAP_RU if russian else _EMOTION_MAP_EN
    for keywords, tag in keyword_map:
        if any(kw in dominant for kw in keywords):
            return tag
    return None


def _topic_tag(summary: dict, russian: bool) -> str | None:  # noqa: ARG001
    """Slugify topic_phrase into a hashtag if it is meaningful."""
    topic = str(summary.get("topic_phrase") or "").strip()
    if not topic or len(topic) < 4:
        return None
    if topic.lower() in _GENERIC_TOPICS:
        return None
    return _slug_tag(topic)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_story_hashtags(
    story_summary: dict | None,
    max_hashtags: int = 3,
    *,
    language: str = "auto",
) -> list[str]:
    """Generate hashtags from actual story content — never from word frequency.

    Priority order:
      1. Character / speaker names (up to 2 tags)
      2. Conflict type
      3. Dominant emotion
      4. Topic phrase
      5. Series name
      6. Arc-completion fallback  (#история / #story)
      Always appends #shorts for Russian content if space remains.
    """
    summary = dict(story_summary or {})
    max_hashtags = max(1, int(max_hashtags or 1))
    russian = language == "ru" or (_is_russian_summary(summary) and language == "auto")
    tags: list[str] = []

    def _add(tag: str | None) -> bool:
        """Append tag if non-empty and not a duplicate. Returns True when full."""
        if not tag or tag in tags:
            return False
        tags.append(tag)
        return len(tags) >= max_hashtags

    # 1. Named characters / speakers
    for char_tag in _character_tags(summary, russian):
        if _add(char_tag):
            return tags[:max_hashtags]

    # 2. Conflict type
    if _add(_conflict_tag(summary, russian)):
        return tags[:max_hashtags]

    # 3. Dominant emotion
    if _add(_emotion_tag(summary, russian)):
        return tags[:max_hashtags]

    # 4. Topic phrase
    if _add(_topic_tag(summary, russian)):
        return tags[:max_hashtags]

    # 5. Series name (when embedded in story_summary)
    series_name = str(summary.get("series_name") or "").strip()
    if series_name and len(series_name) > 2:
        if _add(_slug_tag(series_name)):
            return tags[:max_hashtags]

    # 6. Arc completion fallback (only fills remaining slots)
    is_complete = bool(summary.get("is_complete", True))
    arc_shape = str(summary.get("arc_shape") or "").lower()
    if is_complete or arc_shape == "hook_setup_escalation_payoff":
        _add("#история" if russian else "#story")

    # Always try to include #shorts for Russian YouTube Shorts content
    if russian and len(tags) < max_hashtags:
        _add("#shorts")

    return tags[:max_hashtags]
