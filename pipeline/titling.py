from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from .montage.story_hashtags import build_story_hashtags

INVALID_WINDOWS_CHARS = r'[<>:"/\\|?*]'
MOJIBAKE_MARKERS = (
    "Ð",
    "Ñ",
    "Ã",
    "Â",
    "â€",
    "â€”",
    "â€“",
    "РЃ",
    "РЂ",
    "вЂ",
    "РЎ",
    "Рќ",
    "Рї",
    "СЌ",
    "è",
    "÷",
    "ò",
    "à",
    "ü",
)

RU_STOPWORDS = {
    "это",
    "как",
    "так",
    "что",
    "чтобы",
    "только",
    "потом",
    "когда",
    "если",
    "или",
    "она",
    "они",
    "оно",
    "его",
    "ему",
    "тебя",
    "тебе",
    "меня",
    "мне",
    "тут",
    "там",
    "где",
    "для",
    "еще",
    "ещё",
    "уже",
    "просто",
    "ладно",
    "все",
    "всё",
    "ну",
    "да",
    "нет",
    "ага",
    "вот",
    "ты",
    "мы",
    "вы",
    "я",
    "он",
}

EN_STOPWORDS = {
    "this",
    "that",
    "with",
    "have",
    "what",
    "when",
    "where",
    "your",
    "from",
    "they",
    "them",
    "just",
    "then",
    "into",
    "about",
    "because",
    "there",
    "their",
    "would",
    "could",
    "should",
    "will",
}

RU_GENERIC_TAGS = {
    "идти",
    "уйти",
    "пойти",
    "сказать",
    "сказал",
    "сказала",
    "сказали",
    "быть",
    "есть",
    "мочь",
    "хотеть",
    "видеть",
    "знать",
    "думать",
    "делать",
    "сделать",
    "взять",
    "дать",
    "слушать",
    "слушай",
    "послушай",
    "смотреть",
    "смотри",
    "поймать",
    "узнать",
}

EN_GENERIC_TAGS = {
    "go",
    "goes",
    "went",
    "say",
    "said",
    "make",
    "made",
    "do",
    "did",
    "be",
    "have",
    "get",
    "got",
    "see",
    "look",
    "listen",
    "know",
    "want",
    "need",
    "tell",
    "take",
    "come",
    "going",
}

RU_GENERIC_TAGS_CANONICAL = {
    "иди",
    "уйти",
    "пойти",
    "сказать",
    "сказал",
    "сказала",
    "сказали",
    "быть",
    "есть",
    "мочь",
    "хотеть",
    "видеть",
    "знать",
    "думать",
    "делать",
    "сделать",
    "взять",
    "дать",
    "слушать",
    "слушай",
    "послушай",
    "смотреть",
    "смотри",
    "понять",
    "узнать",
}

EN_GENERIC_TAGS_CANONICAL = {
    "go",
    "goes",
    "went",
    "say",
    "said",
    "make",
    "made",
    "do",
    "did",
    "be",
    "have",
    "get",
    "got",
    "see",
    "look",
    "listen",
    "know",
    "want",
    "need",
    "tell",
    "take",
    "come",
    "going",
}

RU_COMMON_WORDS = {
    "это",
    "ведь",
    "правда",
    "кстати",
    "теперь",
    "все",
    "не",
    "да",
    "нет",
    "что",
    "как",
    "с",
    "на",
    "я",
    "ты",
    "мы",
    "вы",
    "он",
    "она",
    "они",
    "словом",
    "послушай",
    "смотри",
}
EN_COMMON_WORDS = {
    "this",
    "that",
    "what",
    "why",
    "look",
    "listen",
    "now",
    "okay",
    "right",
    "yes",
    "no",
    "we",
    "you",
    "they",
    "it",
}
TECHNICAL_TITLE_PHRASES = (
    "fallback window",
    "dialogue cluster",
    "balanced hook opening",
)


def _looks_mojibake(text: str) -> bool:
    if not text:
        return False
    cleaned = unicodedata.normalize("NFC", str(text))
    marker_hits = sum(cleaned.count(marker) for marker in MOJIBAKE_MARKERS)
    if marker_hits >= 2:
        return True
    if re.search(r"[A-Za-z] — [è÷òàü]", cleaned):
        return True
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", cleaned.lower())
    common_hits = sum(
        1 for token in tokens if token in RU_COMMON_WORDS or token in EN_COMMON_WORDS
    )
    weird_cyrillic_ratio = sum(1 for ch in cleaned if "\u0400" <= ch <= "\u04ff") / max(
        1, len(cleaned)
    )
    rs_hits = cleaned.count("Р") + cleaned.count("С")
    if common_hits == 0 and rs_hits >= max(4, len(cleaned) // 6):
        return True
    if (
        common_hits == 0
        and marker_hits >= 1
        and weird_cyrillic_ratio > 0.32
        and len(tokens) >= 2
    ):
        return True
    if len(tokens) <= 2 and marker_hits >= 1 and weird_cyrillic_ratio > 0.25:
        return True
    return False


def _try_repair_mojibake(text: str) -> str:
    cleaned = unicodedata.normalize("NFC", str(text or ""))
    if not cleaned or not _looks_mojibake(cleaned):
        return cleaned

    def _score(item: str) -> float:
        normalized = unicodedata.normalize("NFC", str(item or ""))
        tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", normalized.lower())
        common_hits = sum(
            1
            for token in tokens
            if token in RU_COMMON_WORDS or token in EN_COMMON_WORDS
        )
        marker_hits = sum(normalized.count(marker) for marker in MOJIBAKE_MARKERS)
        alpha_ratio = sum(1 for ch in normalized if ch.isalpha()) / max(
            1, len(normalized)
        )
        return (
            common_hits * 3.0
            + alpha_ratio * 1.4
            - marker_hits * 2.8
            + min(len(tokens), 8) * 0.15
        )

    candidates = [cleaned]
    for source_encoding in ("latin1", "cp1251", "cp1252"):
        try:
            repaired = cleaned.encode(source_encoding, errors="strict").decode(
                "utf-8", errors="strict"
            )
        except Exception:
            continue
        candidates.append(repaired)
    best = max(candidates, key=_score)
    return best if _score(best) >= _score(cleaned) else cleaned


def _clean_text(text: str) -> str:
    cleaned = unicodedata.normalize("NFC", str(text or ""))
    cleaned = _try_repair_mojibake(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned.strip())
    cleaned = re.sub(r"\s+([,.:;!?])", r"\1", cleaned)
    cleaned = cleaned.replace(" - ", " ")
    for phrase in TECHNICAL_TITLE_PHRASES:
        cleaned = re.sub(
            rf"\b{re.escape(phrase)}\b\s*[:;,-]?\s*", "", cleaned, flags=re.IGNORECASE
        )
    return cleaned


def _title_case_sentence(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


def _trim_sentence(text: str, max_length: int) -> str:
    cleaned = _title_case_sentence(text)
    if len(cleaned) <= max_length:
        return cleaned
    shortened = cleaned[: max_length - 1].rsplit(" ", 1)[0].strip()
    return (shortened or cleaned[: max_length - 1]).rstrip(",.:;!?") + "..."


def _collapse_repeated_words(text: str) -> str:
    tokens = _clean_text(text).split()
    if not tokens:
        return ""
    collapsed = [tokens[0]]
    for token in tokens[1:]:
        if token.casefold() == collapsed[-1].casefold():
            continue
        collapsed.append(token)
    return " ".join(collapsed)


def _strip_opening_fillers(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    fillers = (
        "ну ",
        "так ",
        "ладно ",
        "в общем ",
        "к слову ",
        "смотри ",
        "слушай ",
        "послушай ",
        "well ",
        "okay ",
        "ok ",
        "so ",
        "then ",
        "look ",
        "listen ",
    )
    changed = True
    while changed:
        changed = False
        for filler in fillers:
            if lowered.startswith(filler):
                cleaned = cleaned[len(filler) :].lstrip(" ,:;-—")
                lowered = cleaned.lower()
                changed = True
    return cleaned.strip(" ,:;-—")


def _normalize_title_candidate(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"([!?.,;:])\1{1,}", r"\1", cleaned)
    cleaned = _collapse_repeated_words(cleaned)
    cleaned = _strip_opening_fillers(cleaned)
    cleaned = cleaned.strip(" ,:;-—")
    if not cleaned:
        return ""
    return _title_case_sentence(cleaned)


def _title_quality_score(text: str) -> float:
    cleaned = _clean_text(text)
    if not cleaned:
        return 0.0
    tokens = [token for token in cleaned.split() if token]
    if not tokens:
        return 0.0
    length = len(cleaned)
    letters = sum(1 for ch in cleaned if ch.isalpha())
    alpha_ratio = letters / max(1, length)
    ideal_length = 34.0
    length_score = max(0.0, 1.0 - min(1.0, abs(length - ideal_length) / ideal_length))
    token_score = max(0.0, 1.0 - min(1.0, abs(len(tokens) - 6) / 6.0))
    punctuation_count = (
        cleaned.count(",")
        + cleaned.count(";")
        + cleaned.count(":")
        + cleaned.count("...")
    )
    punctuation_count += cleaned.count("—") + cleaned.count("-")
    punctuation_penalty = min(1.0, punctuation_count / 4.0)
    repetition_penalty = (
        1.0
        if any(
            tokens[index].casefold() == tokens[index - 1].casefold()
            for index in range(1, len(tokens))
        )
        else 0.0
    )
    short_penalty = 1.0 if len(tokens) < 2 else 0.0
    score = (
        length_score * 0.34
        + alpha_ratio * 0.38
        + token_score * 0.22
        - punctuation_penalty * 0.18
        - repetition_penalty * 0.12
        - short_penalty * 0.18
    )
    if _looks_mojibake(cleaned):
        score -= 0.42
    return max(0.0, min(1.0, score))


def _select_best_title_candidate(candidates: list[str]) -> tuple[str, float, bool]:
    seen = set()
    scored = []
    for candidate in candidates:
        normalized = _normalize_title_candidate(candidate)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        scored.append(
            (
                round(_title_quality_score(normalized), 4),
                0 if _looks_mojibake(normalized) else 1,
                normalized,
            )
        )
    if not scored:
        return "", 0.0, False
    scored.sort(key=lambda item: (item[1], item[0], len(item[2])), reverse=True)
    best_score, best_clean_flag, best_title = scored[0]
    cleanup_applied = (
        len(scored) > 1
        or best_title != _normalize_title_candidate(candidates[0])
        or not bool(best_clean_flag)
    )
    return best_title, best_score, cleanup_applied


def _extract_keywords(text: str, language: str = "auto", limit: int = 5) -> list[str]:
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", _clean_text(text).lower())
    stopwords = set(RU_STOPWORDS)
    if str(language).lower().startswith("en"):
        stopwords |= EN_STOPWORDS
        stopwords |= EN_GENERIC_TAGS
        stopwords |= EN_GENERIC_TAGS_CANONICAL
    else:
        stopwords |= RU_GENERIC_TAGS
        stopwords |= RU_GENERIC_TAGS_CANONICAL
    counts: dict[str, int] = {}
    for token in tokens:
        if len(token) < 4 or token in stopwords:
            continue
        counts[token] = counts.get(token, 0) + 1
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return [item[0] for item in ordered[:limit]]


def _clean_hook_seed(text: str, language: str = "auto") -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    prefixes = []
    if str(language).lower().startswith("ru") or language == "auto":
        prefixes.extend(["ну", "так", "ладно", "давай", "смотри", "слушай", "послушай"])
    if str(language).lower().startswith("en") or language == "auto":
        prefixes.extend(["well", "okay", "ok", "listen", "look", "so", "then"])
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if lowered == prefix:
                return ""
            if lowered.startswith(prefix + " "):
                cleaned = cleaned[len(prefix) :].lstrip(" ,:;-—")
                lowered = cleaned.lower()
                changed = True
    if len(cleaned) > 84:
        for sep in (".", "!", "?", "—", "-", ",", ";", ":"):
            pos = cleaned.find(sep, 18)
            if 0 < pos <= 72:
                cleaned = cleaned[: pos + 1]
                break
    return _title_case_sentence(cleaned)


def _detect_mood(text: str, story_score: float) -> str:
    lowered = _clean_text(text).lower()
    if any(
        token in lowered for token in ("убью", "стой", "ужас", "страш", "труп", "кров")
    ):
        return "tension"
    if any(
        token in lowered
        for token in ("почему", "зачем", "смотри", "слушай", "послушай", "правда")
    ):
        return "reveal"
    if any(token in lowered for token in ("ха", "смеш", "шут", "улыб", "прикол")):
        return "humor"
    if story_score >= 0.75:
        return "drama"
    return "conversation"


def _pick_emoji(mood: str) -> str:
    return {
        "tension": "",
        "reveal": "",
        "humor": "",
        "drama": "",
        "conversation": "",
    }.get(mood, "")


def _pick_hashtags(keywords: list[str], mood: str, max_hashtags: int) -> list[str]:
    tags: list[str] = []
    use_russian_tags = any(
        re.search(r"[А-Яа-яЁё]", keyword or "") for keyword in keywords
    )
    for keyword in keywords:
        token = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_]", "", _clean_text(keyword))
        token_key = token.casefold()
        if len(token) < 4:
            continue
        if (
            token_key in RU_GENERIC_TAGS
            or token_key in EN_GENERIC_TAGS
            or token_key in RU_GENERIC_TAGS_CANONICAL
            or token_key in EN_GENERIC_TAGS_CANONICAL
        ):
            continue
        tags.append(f"#{token}")
        if len(tags) >= max_hashtags:
            break
    mood_tag = (
        {
            "tension": "#сцена",
            "reveal": "#развязка",
            "humor": "#диалог",
            "drama": "#момент",
            "conversation": "#shorts",
        }
        if use_russian_tags
        else {
            "tension": "#scene",
            "reveal": "#reveal",
            "humor": "#dialogue",
            "drama": "#moment",
            "conversation": "#shorts",
        }
    ).get(mood)
    if mood == "conversation" and mood_tag and mood_tag not in tags:
        tags.append(mood_tag)
    return tags[:max_hashtags]


def _pick_hashtags_contextual(
    keywords: list[str], mood: str, max_hashtags: int, context_hint: str = ""
) -> list[str]:
    tags: list[str] = []
    use_russian_tags = any(
        re.search(r"[\u0400-\u04FF]", keyword or "") for keyword in keywords
    ) or bool(re.search(r"[\u0400-\u04FF]", context_hint or ""))
    context_tokens = (
        _extract_keywords(
            context_hint, language="ru" if use_russian_tags else "en", limit=3
        )
        if context_hint
        else []
    )
    for keyword in list(keywords) + context_tokens:
        token = re.sub(r"[^A-Za-z\u0400-\u04FF0-9_]", "", _clean_text(keyword))
        token_key = token.casefold()
        if len(token) < 4:
            continue
        if not use_russian_tags and re.search(r"[\u0400-\u04FF]", token):
            continue
        if (
            token_key in RU_GENERIC_TAGS
            or token_key in EN_GENERIC_TAGS
            or token_key in RU_GENERIC_TAGS_CANONICAL
            or token_key in EN_GENERIC_TAGS_CANONICAL
        ):
            continue
        tag = f"#{token}"
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= max_hashtags:
            break
    mood_tag = (
        {
            "tension": "#СЃС†РµРЅР°",
            "reveal": "#СЂР°Р·РІСЏР·РєР°",
            "humor": "#РґРёР°Р»РѕРі",
            "drama": "#РјРѕРјРµРЅС‚",
            "conversation": "#РґРёР°Р»РѕРі",
        }
        if use_russian_tags
        else {
            "tension": "#scene",
            "reveal": "#reveal",
            "humor": "#dialogue",
            "drama": "#moment",
            "conversation": "#dialogue",
        }
    ).get(mood)
    if mood == "conversation" and mood_tag and mood_tag not in tags:
        tags.insert(0, mood_tag)
    elif mood_tag and len(tags) < max_hashtags and mood_tag not in tags:
        tags.append(mood_tag)
    return tags[:max_hashtags]


def _is_russian_language(language: str) -> bool:
    return str(language or "").lower().startswith("ru")


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[\u0400-\u04FF]", text or ""))


def _story_hook_phrase(hook_type: str, language: str) -> str:
    if _is_russian_language(language):
        return {
            "confrontation": "Ссора начинается с первого слова",
            "accusation_denial": "Один вопрос загоняет его в угол",
            "threat_tension": "Угроза сразу меняет сцену",
            "reveal_discovery": "Правда выходит наружу слишком рано",
            "investigation_clue": "Одна улика переворачивает разговор",
            "rescue_urgency": "Каждая секунда уже на счету",
            "danger_escape": "Ещё шаг, и пути назад не будет",
            "impossible_choice": "Выбора почти не осталось",
            "emotional_confession": "Признание меняет всё",
            "dialogue_conflict": "Разговор быстро становится опасным",
            "stakes_first": "Ставки понятны сразу",
            "first_frame_clarity": "Сцена ясна с первого кадра",
            "sound_off_premise": "Даже без звука всё понятно",
            "balanced_hook": "Разговор цепляет сразу",
            "weak_hook": "Напряжение растет с первых секунд",
        }.get(hook_type or "", "Сильный момент из сцены")
    return {
        "confrontation": "The argument starts immediately",
        "accusation_denial": "One question corners him",
        "threat_tension": "A threat changes the whole scene",
        "reveal_discovery": "The truth comes out too soon",
        "investigation_clue": "One clue flips the conversation",
        "rescue_urgency": "Every second already matters",
        "danger_escape": "One more step, and there is no way back",
        "impossible_choice": "There is almost no safe choice left",
        "emotional_confession": "The confession changes everything",
        "dialogue_conflict": "The conversation turns dangerous fast",
        "stakes_first": "The stakes are clear right away",
        "first_frame_clarity": "The scene is clear in the first frame",
        "sound_off_premise": "You understand it even without sound",
        "balanced_hook": "The conversation grabs you immediately",
        "weak_hook": "The tension builds from the first seconds",
    }.get(hook_type or "", "Strong story moment")


def _story_payoff_phrase(payoff_type: str, language: str) -> str:
    if _is_russian_language(language):
        return {
            "reveal": "и правда выходит наружу",
            "conflict": "и всё идет к взрыву",
            "emotional": "и разговор меняет тон",
            "resolution": "и сцена получает развязку",
            "hook_setup_escalation_payoff": "и история получает финал",
            "unfinished": "но развязки не хватает",
        }.get(payoff_type or "", "")
    return {
        "reveal": "and the truth comes out",
        "conflict": "and everything heads toward a blow-up",
        "emotional": "and the tone changes completely",
        "resolution": "and the scene finally lands",
        "hook_setup_escalation_payoff": "and the story lands cleanly",
        "unfinished": "but it never quite resolves",
    }.get(payoff_type or "", "")


def _story_hashtag_pack(
    keywords: list[str],
    mood: str,
    max_hashtags: int,
    *,
    context_hint: str = "",
    language: str = "auto",
    story_arc_shape: str = "",
    hook_type: str = "",
    payoff_type: str = "",
    story_summary: dict | None = None,
) -> list[str]:
    if story_summary:
        summary_tags = build_story_hashtags(
            story_summary, max_hashtags=max_hashtags, language=language
        )
        if summary_tags:
            return summary_tags[:max_hashtags]
    max_hashtags = max(1, int(max_hashtags or 1))
    if not _is_russian_language(language):
        return _pick_hashtags_contextual(
            keywords, mood, max_hashtags, context_hint=context_hint
        )
    max_hashtags = max(max_hashtags, 3)
    tags: list[str] = []
    for tag in ("#сериал", "#shorts"):
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= max_hashtags:
            return tags[:max_hashtags]
    if payoff_type == "reveal":
        contextual_tag = "#развязка"
    elif hook_type in {
        "confrontation",
        "accusation_denial",
        "threat_tension",
        "dialogue_conflict",
    }:
        contextual_tag = "#конфликт"
    elif hook_type in {"reveal_discovery", "investigation_clue"}:
        contextual_tag = "#интрига"
    elif hook_type in {"emotional_confession"}:
        contextual_tag = "#эмоции"
    elif hook_type in {"stakes_first", "first_frame_clarity", "sound_off_premise"}:
        contextual_tag = "#сторителлинг"
    elif story_arc_shape == "hook_setup_escalation_payoff":
        contextual_tag = "#история"
    else:
        contextual_tag = {
            "tension": "#конфликт",
            "reveal": "#развязка",
            "humor": "#реакция",
            "drama": "#момент",
            "conversation": "#диалог",
        }.get(mood, "#реакция")
    if contextual_tag not in tags:
        tags.append(contextual_tag)
    if len(tags) < max_hashtags:
        for tag in _pick_hashtags_contextual(
            keywords, mood, max_hashtags, context_hint=context_hint
        ):
            if tag not in tags:
                tags.append(tag)
            if len(tags) >= max_hashtags:
                break
    return tags[:max_hashtags]


def _build_russian_story_title(
    *,
    hook_type: str,
    payoff_type: str,
    story_arc_shape: str,
    context_hint: str,
    seed: str,
    max_length: int,
) -> str:
    hook_phrase = _story_hook_phrase(hook_type, "ru")
    payoff_phrase = _story_payoff_phrase(payoff_type, "ru")
    context_hint = _trim_sentence(
        _clean_text(context_hint), max(18, min(42, max_length))
    )
    seed = _trim_sentence(_clean_text(seed), max(18, min(42, max_length)))
    if context_hint and hook_type in {"reveal_discovery", "investigation_clue"}:
        title = f"{context_hint} — {hook_phrase}"
    elif context_hint:
        title = f"{hook_phrase}: {context_hint}"
    else:
        title = hook_phrase
    if payoff_phrase and payoff_phrase not in title:
        if story_arc_shape == "hook_setup_escalation_payoff" or not context_hint:
            title = f"{title} {payoff_phrase}"
    if seed and len(title) < 24:
        title = f"{title}: {seed}"
    return _trim_sentence(title, max_length)


def generate_context_title(subtitle_info: dict, meta: dict, cfg: dict) -> dict:  # noqa: C901
    cfg = cfg or {}

    # ------------------------------------------------------------------
    # 1. Source data
    # ------------------------------------------------------------------
    summary = dict(subtitle_info.get("summary") or {})
    story_summary = dict(
        meta.get("story_summary") or subtitle_info.get("story_summary") or {}
    )

    # Flatten story fields for downstream use
    ss_title_seed = _clean_text(story_summary.get("title_seed") or "")
    ss_hook = _clean_text(story_summary.get("hook") or "")
    ss_setup = _clean_text(story_summary.get("setup") or "")
    ss_escalation = _clean_text(story_summary.get("escalation") or "")
    ss_payoff = _clean_text(story_summary.get("payoff") or "")

    story_summary_text = _clean_text(
        story_summary.get("summary_text")
        or " ".join(
            part for part in [ss_hook, ss_setup, ss_escalation, ss_payoff] if part
        )
    )
    text = story_summary_text or _clean_text(summary.get("summary_text") or "")

    # ------------------------------------------------------------------
    # 2. Language & style
    # ------------------------------------------------------------------
    title_language = str(cfg.get("title_language", "") or "").lower()
    language = (
        title_language
        if title_language and title_language != "auto"
        else subtitle_info.get("language", cfg.get("subtitle_language", "auto"))
    )
    is_russian = _is_russian_language(language) or _has_cyrillic(
        text or ss_hook or ss_title_seed
    )
    style = str(cfg.get("title_style", "context_clean"))
    resolved_style = (
        "retention_soft" if style in {"viral_soft", "retention_soft"} else style
    )
    packaging_profile = str(
        cfg.get("packaging_profile", "ru_serial_drama") or "ru_serial_drama"
    )
    title_max_length = int(cfg.get("title_max_length", 72))

    # ------------------------------------------------------------------
    # 3. Score breakdown
    # ------------------------------------------------------------------
    score_breakdown = meta.get("score_breakdown", {}) or {}
    story_score = float(
        score_breakdown.get("story_clarity_score", meta.get("story_clarity_score", 0.0))
        or 0.0
    )
    hook_strength = float(
        meta.get(
            "hook_strength",
            score_breakdown.get(
                "hook_strength", score_breakdown.get("hook_score", 0.0)
            ),
        )
        or 0.0
    )
    payoff_strength = float(
        meta.get(
            "payoff_strength",
            score_breakdown.get(
                "payoff_strength", score_breakdown.get("closure_score", 0.0)
            ),
        )
        or 0.0
    )
    recommendation_readiness = float(
        meta.get(
            "recommendation_readiness_score",
            score_breakdown.get("recommendation_readiness_score", 0.0),
        )
        or 0.0
    )

    # Story shape / type signals (from story_summary first, then meta)
    story_arc_shape = str(
        story_summary.get("arc_shape")
        or meta.get("story_arc_shape", score_breakdown.get("story_arc_shape", ""))
        or ""
    )
    hook_type = str(
        story_summary.get("hook_type")
        or meta.get("hook_type", score_breakdown.get("hook_type", ""))
        or ""
    )
    payoff_type = str(
        story_summary.get("payoff_type")
        or meta.get("payoff_type", score_breakdown.get("payoff_type", ""))
        or ""
    )

    # ------------------------------------------------------------------
    # 4. Forbidden-source guard
    #    Reject any seed that is a technical label rather than story text.
    # ------------------------------------------------------------------
    _FORBIDDEN_LABELS = {
        "dialogue_cluster",
        "dialogue_linear",
        "fallback_window",
        "balanced_hook",
        "story_chain",
        "context_clean",
    }

    def _is_forbidden(s: str) -> bool:
        if not s or not s.strip():
            return True
        low = s.strip().lower()
        for label in _FORBIDDEN_LABELS:
            if label in low:
                return True
        for phrase in TECHNICAL_TITLE_PHRASES:
            if phrase.lower() in low:
                return True
        return False

    # ------------------------------------------------------------------
    # 5. Title seed — priority cascade over actual story content
    # ------------------------------------------------------------------
    def _pick_seed() -> str:
        # Primary: title_seed extracted from story text
        if ss_title_seed and not _is_forbidden(ss_title_seed):
            return _clean_hook_seed(ss_title_seed, language=language)
        # Secondary: hook text trimmed to a reasonable length
        if ss_hook and not _is_forbidden(ss_hook):
            return _clean_hook_seed(_trim_sentence(ss_hook, 72), language=language)
        # Tertiary: first sentence of escalation
        if ss_escalation and not _is_forbidden(ss_escalation):
            first = re.split(r"[.!?]\s+", ss_escalation)[0]
            return _clean_hook_seed(first, language=language)
        # Last resort before generic fallback
        if ss_payoff and not _is_forbidden(ss_payoff):
            return _clean_hook_seed(ss_payoff, language=language)
        return ""

    seed = _pick_seed()
    if not seed or _is_forbidden(seed) or _looks_mojibake(seed):
        seed = "Момент из серии" if is_russian else "Series moment"

    # ------------------------------------------------------------------
    # 6. Build hook-focused variant (title_variant_a)
    # ------------------------------------------------------------------
    hook_raw = _clean_hook_seed(ss_hook or ss_title_seed or seed, language=language)
    if not hook_raw or _is_forbidden(hook_raw) or _looks_mojibake(hook_raw):
        hook_raw = seed
    title_variant_a = _trim_sentence(hook_raw, title_max_length)

    # ------------------------------------------------------------------
    # 7. Build payoff-focused variant (title_variant_b)
    # ------------------------------------------------------------------
    payoff_raw = _clean_hook_seed(ss_payoff or ss_escalation or seed, language=language)
    if not payoff_raw or _is_forbidden(payoff_raw) or _looks_mojibake(payoff_raw):
        payoff_raw = seed
    title_variant_b = _trim_sentence(payoff_raw, title_max_length)

    # hook_line is a short form of the hook variant
    hook_line = _trim_sentence(title_variant_a, min(42, title_max_length))

    # ------------------------------------------------------------------
    # 8. Select best title from candidates
    # ------------------------------------------------------------------
    selected_title, title_quality_score, title_cleanup_applied = (
        _select_best_title_candidate(
            [seed, title_variant_a, title_variant_b, hook_line]
        )
    )
    if selected_title and not _is_forbidden(selected_title):
        title = _trim_sentence(selected_title, title_max_length)
    else:
        fallback_title = "Момент из серии" if is_russian else "Series moment"
        title = _trim_sentence(seed or fallback_title, title_max_length)

    # ------------------------------------------------------------------
    # 9. Russian packaging safety net
    #    If the chosen title looks wrong, rebuild it from story content
    #    using _build_russian_story_title (which uses the hook/payoff
    #    framing phrases together with actual context_hint text).
    # ------------------------------------------------------------------
    russian_packaging = is_russian or packaging_profile == "ru_serial_drama"
    if russian_packaging and (
        not _has_cyrillic(title)
        or _looks_mojibake(title)
        or _is_forbidden(title)
        or re.search(
            r"\b(fallback window|dialogue cluster|balanced hook opening)\b",
            title,
            flags=re.IGNORECASE,
        )
        or float(title_quality_score or 0.0) < 0.58
    ):
        context_for_builder = ss_title_seed or ss_hook or ss_setup or text or hook_line
        seed_for_builder = seed or hook_line or title
        title = _build_russian_story_title(
            hook_type=hook_type or "balanced_hook",
            payoff_type=payoff_type or ("reveal" if payoff_strength >= 0.52 else ""),
            story_arc_shape=story_arc_shape,
            context_hint=context_for_builder,
            seed=seed_for_builder,
            max_length=title_max_length,
        )
        hook_line = title
        title_variant_a = title
        title_variant_b = title
        title_cleanup_applied = True

    # ------------------------------------------------------------------
    # 10. Keywords
    # ------------------------------------------------------------------
    topic_terms = list(story_summary.get("topic_terms") or [])
    keywords = (
        topic_terms
        or list(summary.get("keywords") or [])
        or _extract_keywords(text, language=language)
    )
    if not keywords:
        story_blob = " ".join(
            filter(None, [ss_title_seed, ss_hook, ss_escalation, ss_payoff])
        )
        keywords = _extract_keywords(story_blob, language=language, limit=5)
    keywords = [_clean_text(item) for item in keywords if _clean_text(item)]
    deduped_keywords: list[str] = []
    seen_keywords: set[str] = set()
    for item in keywords:
        key = item.casefold()
        if key in seen_keywords:
            continue
        seen_keywords.add(key)
        deduped_keywords.append(item)
    keywords = deduped_keywords
    keyword_cluster = [
        item
        for item in keywords
        if item.lower() not in RU_GENERIC_TAGS and item.lower() not in EN_GENERIC_TAGS
    ][:3]

    # ------------------------------------------------------------------
    # 11. Mood
    # ------------------------------------------------------------------
    mood = summary.get("mood") or _detect_mood(
        text or ss_hook or ss_escalation, story_score
    )

    # ------------------------------------------------------------------
    # 12. Hashtags
    # ------------------------------------------------------------------
    hashtags = (
        _story_hashtag_pack(
            keywords,
            mood,
            int(cfg.get("title_max_hashtags", 2)),
            context_hint=ss_title_seed or ss_hook or "",
            language=language,
            story_arc_shape=story_arc_shape,
            hook_type=hook_type,
            payoff_type=payoff_type,
            story_summary=story_summary,
        )
        if bool(cfg.get("title_include_hashtags", True))
        else []
    )
    # Strip Cyrillic tags when the title itself is not Russian
    if not is_russian:
        hashtags = [tag for tag in hashtags if not re.search(r"[\u0400-\u04FF]", tag)]

    # ------------------------------------------------------------------
    # 13. Emoji & description
    # ------------------------------------------------------------------
    emoji = _pick_emoji(mood) if bool(cfg.get("title_include_emoji", True)) else ""
    emojis = [emoji] if emoji else []
    description_seed = _trim_sentence(text or hook_line or title, 110)

    # ------------------------------------------------------------------
    # 14. Soft scores
    # ------------------------------------------------------------------
    retention_soft_score = min(
        1.0,
        max(
            0.0,
            hook_strength * 0.36
            + payoff_strength * 0.24
            + recommendation_readiness * 0.26
            + (0.14 if packaging_profile == "ru_serial_drama" else 0.06),
        ),
    )
    packaging_quality_score = min(
        1.0,
        max(
            0.0,
            hook_strength * 0.34
            + payoff_strength * 0.26
            + story_score * 0.22
            + recommendation_readiness * 0.14
            + float(
                meta.get(
                    "subtitle_quality_score",
                    score_breakdown.get("subtitle_quality_score", 0.0),
                )
                or 0.0
            )
            * 0.05
            + title_quality_score * 0.07,
        ),
    )
    confidence = min(
        0.95,
        max(
            0.35,
            0.34
            + (0.18 if text else 0.0)
            + min(0.16, len(keywords) * 0.04)
            + min(0.12, hook_strength * 0.16)
            + min(0.10, payoff_strength * 0.14)
            + (0.10 if story_score >= 0.6 else 0.0),
        ),
    )

    return {
        "title": title,
        "hook_line": hook_line,
        "title_variant_a": title_variant_a,
        "title_variant_b": title_variant_b,
        "description_seed": description_seed,
        "keyword_cluster": keyword_cluster,
        "series_mood": mood,
        "retention_soft_score": round(retention_soft_score, 4),
        "viral_soft_score": round(retention_soft_score, 4),
        "packaging_quality_score": round(packaging_quality_score, 4),
        "title_quality_score": round(title_quality_score, 4),
        "hashtags": hashtags,
        "emojis": emojis[: int(cfg.get("title_max_emojis", 1))],
        "style": resolved_style,
        "confidence": round(confidence, 4),
        "mood": mood,
        "keywords": keywords,
        "title_cleanup_applied": bool(title_cleanup_applied),
        "story_summary": story_summary,
        "story_summary_text": story_summary_text or text,
    }


def build_output_filename(
    index: int, title_payload: dict, extension: str = ".mp4"
) -> str:
    title = _clean_text(str((title_payload or {}).get("title", "")))
    hashtags_list = [
        _clean_text(item) for item in list((title_payload or {}).get("hashtags") or [])
    ]
    emoji_list = [
        _clean_text(item) for item in list((title_payload or {}).get("emojis") or [])
    ]
    if not title and not hashtags_list and not emoji_list:
        return f"short_{index}{extension}"
    readable_parts = []
    if title:
        readable_parts.append(title)
    if hashtags_list:
        readable_parts.append(" ".join(item for item in hashtags_list if item))
    if emoji_list:
        readable_parts.append(" ".join(item for item in emoji_list if item))
    readable_suffix = _clean_text(" ".join(readable_parts))
    safe_suffix = unicodedata.normalize(
        "NFC", re.sub(INVALID_WINDOWS_CHARS, "", readable_suffix)
    )
    safe_suffix = safe_suffix.rstrip(" .")
    safe_suffix = re.sub(r"\s+", " ", safe_suffix).strip()
    if len(safe_suffix) > 110:
        safe_suffix = safe_suffix[:110].rsplit(" ", 1)[0].strip() or safe_suffix[:110]
    if not safe_suffix:
        return f"short_{index}{extension}"
    return f"short_{index} ({safe_suffix}){extension}"


def maybe_rename_output(video_path: str, index: int, title_payload: dict) -> str:
    src = Path(video_path)
    new_name = build_output_filename(index, title_payload, extension=src.suffix)
    dst = src.with_name(new_name)
    if dst == src:
        return str(src)
    dst_stem = dst.stem
    counter = 2
    while dst.exists():
        dst = src.with_name(f"{dst_stem} [{counter}]{src.suffix}")
        counter += 1
    os.replace(src, dst)
    return str(dst)
