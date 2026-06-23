from __future__ import annotations

import math
import os
import re
import unicodedata
from typing import Iterable

from utils import seconds_to_hhmmssms
from .text_utils import (
    _clean_text as _shared_clean_text,
    _looks_mojibake as _shared_looks_mojibake,
    _tokenize as _shared_tokenize,
    _try_repair_mojibake as _shared_try_repair_mojibake,
)


PROFILE_TO_MODEL = {
    "fast": "tiny",
    "balanced": "base",
    "quality": "small",
}

PROCESSING_MODE_TO_BEAM = {
    "balanced_local": 5,
    "enhanced_local": 7,
}


RU_STOPWORDS = {
    "это", "как", "так", "что", "чтобы", "только", "потом", "когда", "если", "или", "она", "они",
    "оно", "его", "ему", "тебя", "тебе", "меня", "мне", "тут", "там", "где", "для", "ещё", "уже",
    "просто", "ладно", "всё", "все", "ну", "да", "нет", "ага", "вот", "ты", "мы", "вы", "я", "он",
}
LEAD_CONNECTORS_RU = {"и", "а", "но", "или", "что", "чтобы", "потому", "если", "когда", "тогда"}
LEAD_CONNECTORS_EN = {"and", "but", "or", "because", "if", "when", "then", "that"}
EMOTION_TOKENS_RU = {"почему", "зачем", "стой", "нет", "правда", "смотри", "послушай", "ужас", "убью"}
EMOTION_TOKENS_EN = {"why", "stop", "no", "look", "listen", "truth", "killed", "never", "wait"}

# Override legacy mojibake literals with proper Unicode values.
TERMINAL_PUNCTUATION = (".", "!", "?")
PAUSE_SPLIT_THRESHOLD = 0.45
RU_STOPWORDS = {
    "это", "как", "так", "что", "чтобы", "только", "потом", "когда", "если", "или", "она", "они",
    "оно", "его", "ему", "тебя", "тебе", "меня", "мне", "тут", "там", "где", "для", "ещё", "уже",
    "просто", "ладно", "всё", "все", "ну", "да", "нет", "ага", "вот", "ты", "мы", "вы", "я", "он",
}
LEAD_CONNECTORS_RU = {"и", "а", "но", "или", "что", "чтобы", "потому", "если", "когда", "тогда"}
EMOTION_TOKENS_RU = {"почему", "зачем", "стой", "нет", "правда", "смотри", "послушай", "ужас", "убью"}
QUESTION_WORDS_RU = {"кто", "что", "почему", "зачем", "куда", "где", "как", "когда"}
QUESTION_WORDS_EN = {"who", "what", "why", "where", "when", "how"}
PAYOFF_TOKENS_RU = {"поэтому", "вот", "значит", "теперь", "видишь", "понял", "правда", "давай"}
PAYOFF_TOKENS_EN = {"so", "therefore", "now", "look", "see", "understand", "fine", "okay"}
MOJIBAKE_MARKERS = ("Ð", "Ñ", "Гђ", "Г‘", "Гѓ", "Г‚", "Гўв‚¬", "Гўв‚¬вЂќ", "Гўв‚¬вЂњ", "РЎ", "Рћ", "СЌ", "Сѓ", "Рє")
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


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = text.replace(" - ", " ")
    text = _try_repair_mojibake(text)
    return text


def _repair_candidate_score(text: str) -> float:
    cleaned = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()
    if not cleaned:
        return -999.0
    tokens = _tokenize(cleaned)
    common_hits = sum(1 for token in tokens if token in RU_COMMON_WORDS or token in EN_COMMON_WORDS)
    marker_hits = sum(cleaned.count(marker) for marker in MOJIBAKE_MARKERS)
    alpha_ratio = sum(1 for ch in cleaned if ch.isalpha()) / max(1, len(cleaned))
    word_count = len(tokens)
    punctuation_bonus = 0.10 if re.search(r"[.!?]$", cleaned) else 0.0
    return common_hits * 3.0 + alpha_ratio * 1.4 + min(word_count, 10) * 0.24 + punctuation_bonus - marker_hits * 2.7


def _looks_mojibake(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()
    if not cleaned:
        return False
    marker_hits = sum(cleaned.count(marker) for marker in MOJIBAKE_MARKERS)
    if marker_hits >= 2:
        return True
    tokens = _tokenize(cleaned)
    common_hits = sum(1 for token in tokens if token in RU_COMMON_WORDS or token in EN_COMMON_WORDS)
    weird_cyrillic_ratio = sum(1 for ch in cleaned if "\u0400" <= ch <= "\u04ff") / max(1, len(cleaned))
    rs_hits = cleaned.count("Р") + cleaned.count("С")
    if common_hits == 0 and rs_hits >= max(4, len(cleaned) // 6):
        return True
    if common_hits == 0 and weird_cyrillic_ratio > 0.32 and len(tokens) >= 2:
        return True
    if len(tokens) <= 2 and marker_hits >= 1 and weird_cyrillic_ratio > 0.25:
        return True
    return False


def _try_repair_mojibake(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()
    if not cleaned or not _looks_mojibake(cleaned):
        return cleaned
    candidates = [cleaned]
    for source_encoding in ("latin1", "cp1251", "cp1252"):
        try:
            repaired = cleaned.encode(source_encoding, errors="strict").decode("utf-8", errors="strict")
        except Exception:
            continue
        candidates.append(repaired)
    best = max(candidates, key=_repair_candidate_score)
    if _repair_candidate_score(best) >= _repair_candidate_score(cleaned):
        return best
    return cleaned


def _processing_mode(cfg=None) -> str:
    cfg = cfg or {}
    mode = str(cfg.get("subtitle_processing_mode", "") or "").strip().lower()
    if mode in {"balanced_local", "enhanced_local"}:
        return mode
    profile = str(cfg.get("transcription_profile", "balanced")).lower()
    return "enhanced_local" if profile == "quality" else "balanced_local"


def _subtitle_confidence_from_logprob(avg_logprob: float) -> float:
    return max(0.0, min(1.0, 1.0 + (float(avg_logprob) / 2.0)))


def _looks_suspicious_text(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return True
    if _looks_mojibake(cleaned):
        return True
    if re.search(r"(.)\1{4,}", cleaned):
        return True
    tokens = _tokenize(cleaned)
    if not tokens:
        return True
    return (len(set(tokens)) / max(1, len(tokens))) < 0.35


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return ordered[0]
    q = max(0.0, min(1.0, float(q)))
    pos = (len(ordered) - 1) * q
    left = int(math.floor(pos))
    right = int(math.ceil(pos))
    if left == right:
        return ordered[left]
    alpha = pos - left
    return ordered[left] * (1.0 - alpha) + ordered[right] * alpha


def _subtitle_language_consistency(text: str, language: str) -> float:
    cleaned = _clean_text(text)
    if not cleaned:
        return 0.0
    cyr = len(re.findall(r"[А-Яа-яЁё]", cleaned))
    lat = len(re.findall(r"[A-Za-z]", cleaned))
    letters = max(1, cyr + lat)
    lang = str(language or "auto").lower()
    if lang.startswith("ru"):
        dominant = cyr / float(letters)
    elif lang.startswith("en"):
        dominant = lat / float(letters)
    else:
        dominant = max(cyr, lat) / float(letters)
    return round(max(0.0, min(1.0, dominant)), 4)


def _subtitle_text_sanity_score(text: str, language: str) -> float:
    cleaned = _clean_text(text)
    if not cleaned:
        return 0.0
    tokens = _tokenize(cleaned)
    if not tokens:
        return 0.0
    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    suspicious_penalty = 0.28 if _looks_suspicious_text(cleaned) else 0.0
    if _looks_mojibake(cleaned):
        suspicious_penalty += 0.22
    short_token_penalty = min(0.20, sum(1 for token in tokens if len(token) <= 2) / max(1, len(tokens)) * 0.22)
    repeated_penalty = min(0.22, max(0.0, 0.58 - unique_ratio) * 0.65)
    lang_consistency = _subtitle_language_consistency(cleaned, language)
    punctuation_bonus = 0.10 if re.search(r"[.!?]$", cleaned) else 0.0
    multi_sentence_bonus = 0.06 if len(tokens) >= 6 else 0.0
    score = 0.50 + lang_consistency * 0.22 + unique_ratio * 0.18 + punctuation_bonus + multi_sentence_bonus
    score -= suspicious_penalty + short_token_penalty + repeated_penalty
    return round(max(0.0, min(1.0, score)), 4)


def _subtitle_correction_pass(segments: list[dict], cfg=None) -> tuple[list[dict], bool]:
    cfg = cfg or {}
    if not bool(cfg.get("subtitle_correction_enabled", True)) or not segments:
        return segments, False
    corrected = []
    changed = False
    for segment in segments:
        words = []
        last_lower = None
        for word in list(segment.get("words") or []):
            token = _clean_text(word.get("text", ""))
            if not token:
                continue
            if last_lower is not None and token.lower() == last_lower:
                changed = True
                continue
            last_lower = token.lower()
            words.append({**word, "text": token})
        text = _clean_text(" ".join(item["text"] for item in words)) if words else _clean_text(segment.get("text", ""))
        if text and text[-1] not in ".!?" and len(text.split()) >= 6 and not _looks_suspicious_text(text):
            text += "."
            changed = True
        updated = dict(segment)
        updated["words"] = words
        updated["text"] = text
        corrected.append(updated)
    corrected = [item for item in corrected if item.get("text")]
    return corrected or segments, changed


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", (text or "").lower())


# Canonical shared helpers for cross-module text logic.
_clean_text = _shared_clean_text
_looks_mojibake = _shared_looks_mojibake
_tokenize = _shared_tokenize
_try_repair_mojibake = _shared_try_repair_mojibake


def _split_caption_lines(text: str, max_chars: int = 34, max_lines: int = 2) -> str:
    words = text.split()
    if len(words) <= 2:
        return " ".join(words)
    lines = []
    current = []
    for word in words:
        candidate = " ".join(current + [word]).strip()
        if current and len(candidate) > max_chars and len(lines) < max(0, max_lines - 1):
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        lines = head + [tail]
    return "\n".join(lines)


def _layout_word_lines(words: list[dict], max_chars: int = 34, max_lines: int = 2) -> list[list[int]]:
    if not words:
        return []
    if max_lines <= 1 or len(words) <= 2:
        return [list(range(len(words)))]
    lengths = [len(str(item.get("text", ""))) for item in words]
    best_split = None
    best_cost = None
    for split in range(1, len(words)):
        left = lengths[:split]
        right = lengths[split:]
        left_len = sum(left) + max(0, len(left) - 1)
        right_len = sum(right) + max(0, len(right) - 1)
        overflow = max(0, left_len - max_chars) + max(0, right_len - max_chars)
        imbalance = abs(left_len - right_len)
        too_short_penalty = 8 if min(len(left), len(right)) <= 0 else (3 if min(len(left), len(right)) == 1 else 0)
        cost = overflow * 20 + imbalance + too_short_penalty
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_split = split
    if best_split is None:
        return [list(range(len(words)))]
    return [list(range(0, best_split)), list(range(best_split, len(words)))]


def _segment_max_chars(cfg) -> int:
    compact = bool((cfg or {}).get("subtitle_compact_mode", True))
    default = 26 if compact else 34
    return max(16, int((cfg or {}).get("subtitle_max_chars_per_block", default)))


def _segment_max_lines(cfg) -> int:
    return max(1, int((cfg or {}).get("subtitle_max_visible_lines", 2)))


def _segment_word_limit(cfg) -> int:
    return max(1, int((cfg or {}).get("subtitle_max_visible_words", 3)))


def _sentence_word_limit(cfg) -> int:
    return max(_segment_word_limit(cfg) + 2, int((cfg or {}).get("subtitle_sentence_max_words", 6)))


def _subtitle_persist_gap_seconds(cfg) -> float:
    return max(0.18, float((cfg or {}).get("subtitle_persist_gap_seconds", 0.55)))


def _subtitle_phrase_ttl_seconds(cfg) -> float:
    return max(0.55, float((cfg or {}).get("subtitle_phrase_ttl_seconds", 1.05)))


def _subtitle_repair_prompt(language: str | None, cfg=None) -> str:
    cfg = cfg or {}
    custom_prompt = _clean_text(cfg.get("subtitle_initial_prompt", ""))
    if custom_prompt:
        return custom_prompt
    lang = str(language or cfg.get("subtitle_language", "auto")).lower()
    if lang.startswith("ru"):
        return "Русский диалог сериала. Сохраняй имена, пунктуацию, границы реплик и окончания фраз."
    if lang.startswith("en"):
        return "English series dialogue. Keep names, punctuation, turn boundaries, and complete sentence endings."
    return "Dialogue from a series. Keep names, punctuation, turn boundaries, and complete sentence endings."


def _subtitle_gap_blink_threshold_seconds(cfg) -> float:
    return max(0.02, float((cfg or {}).get("subtitle_gap_blink_threshold_ms", 180)) / 1000.0)


def _subtitle_state_epsilon_seconds(cfg) -> float:
    return max(0.005, float((cfg or {}).get("subtitle_state_transition_epsilon_ms", 20)) / 1000.0)


def _ass_quantize_time(seconds: float, mode: str = "round") -> float:
    value = max(0.0, float(seconds))
    centiseconds = value * 100.0
    if mode == "floor":
        return math.floor(centiseconds) / 100.0
    if mode == "ceil":
        return math.ceil(centiseconds) / 100.0
    return round(centiseconds) / 100.0


def _split_words_into_sentence_chunks(words: list[dict], cfg=None) -> list[list[dict]]:
    cfg = cfg or {}
    sentence_limit = _sentence_word_limit(cfg)
    chunks = []
    current = []
    prev_word = None
    for word in words:
        if prev_word and current:
            gap = max(0.0, float(word.get("start", 0.0)) - float(prev_word.get("end", 0.0)))
            if gap >= max(PAUSE_SPLIT_THRESHOLD * 0.7, 0.38) and len(current) >= 2:
                chunks.append(current)
                current = []
        current.append(word)
        token = word["text"]
        punctuation_break = token.endswith(TERMINAL_PUNCTUATION) or token.endswith((",", ";", ":"))
        if len(current) >= sentence_limit or (punctuation_break and len(current) >= max(4, sentence_limit // 2)):
            chunks.append(current)
            current = []
        prev_word = word
    if current:
        chunks.append(current)
    return chunks


def _make_sentence_segment(words: list[dict], avg_logprob: float, cfg=None) -> dict:
    cfg = cfg or {}
    text = _clean_text(" ".join(item["text"] for item in words))
    line_groups = _layout_word_lines(
        words,
        max_chars=_segment_max_chars(cfg),
        max_lines=_segment_max_lines(cfg),
    )
    caption_text = "\n".join(
        _clean_text(" ".join(words[index]["text"] for index in group))
        for group in line_groups
        if group
    )
    return {
        "start": float(words[0]["start"]),
        "end": float(words[-1]["end"]),
        "text": text,
        "avg_logprob": float(avg_logprob),
        "words": list(words),
        "caption_text": caption_text or _split_caption_lines(
            text,
            max_chars=_segment_max_chars(cfg),
            max_lines=_segment_max_lines(cfg),
        ),
        "line_groups": line_groups,
    }


def _render_highlighted_sentence(words: list[dict], active_ids: set[int], display_mode: str, line_groups: list[list[int]] | None = None) -> str:
    if display_mode == "active_chunk":
        rendered = " ".join(
            r"{\c&H4FD5FF&\bord3}" + word["text"] + r"{\c&HFFFFFF&\bord2}"
            if index in active_ids
            else word["text"]
            for index, word in enumerate(words)
            if index in active_ids
        )
        return rendered
    line_groups = list(line_groups or [list(range(len(words)))])
    rendered_lines = []
    for group in line_groups:
        pieces = []
        for index in group:
            word = words[index]
            token = word["text"]
            if index in active_ids:
                pieces.append(r"{\c&H4FD5FF&\bord3}" + token + r"{\c&HFFFFFF&\bord2}")
            else:
                pieces.append(token)
        rendered_lines.append(" ".join(pieces))
    return "\n".join(rendered_lines)


def _persistent_sentence_events(segment: dict, batch_size: int, cfg=None) -> list[dict]:
    cfg = cfg or {}
    words = list(segment.get("words") or [])
    if not words:
        return []
    display_mode = str(cfg.get("subtitle_display_mode", cfg.get("subtitle_chunk_mode", "sentence_highlight"))).lower()
    max_chars = max(18, _segment_max_chars(cfg) + 4)
    max_lines = _segment_max_lines(cfg)
    hold_max = max(0.25, float(cfg.get("subtitle_hold_max_seconds", 0.38)))
    tail_hold = min(hold_max, max(0.10, float(cfg.get("subtitle_tail_hold_seconds", 0.12))))
    epsilon = _subtitle_state_epsilon_seconds(cfg)
    states = []
    step = max(1, int(batch_size))
    phrase_start = float(segment["start"])
    phrase_end = float(segment["end"])
    line_groups = list(segment.get("line_groups") or _layout_word_lines(words, max_chars=max_chars, max_lines=max_lines))
    state_start = phrase_start
    for index in range(0, len(words), step):
        active_ids = set(range(index, min(index + step, len(words))))
        next_boundary = float(words[index + step]["start"]) if index + step < len(words) else phrase_end
        raw_end = min(
            phrase_end,
            max(float(words[min(index + step - 1, len(words) - 1)]["end"]), state_start + 0.14),
            state_start + hold_max,
        )
        state_end = min(phrase_end, max(raw_end, next_boundary - epsilon if index + step < len(words) else raw_end))
        if index + step < len(words):
            state_end = max(state_start + 0.10, min(state_end, next_boundary - epsilon))
        else:
            state_end = max(state_start + 0.12, phrase_end)
        rendered = _render_highlighted_sentence(words, active_ids, display_mode, line_groups=line_groups)
        states.append(
            {
                "start": float(state_start),
                "end": float(state_end),
                "text": rendered,
                "state": "advance_highlight" if index > 0 else "show_phrase",
            }
        )
        state_start = max(state_end, state_start + 0.10)
    if states:
        final_cap = min(
            float(phrase_end),
            max(float(words[-1]["end"]) + tail_hold, float(states[-1]["start"]) + hold_max),
        )
        states[-1]["end"] = float(max(states[-1]["start"] + 0.12, final_cap))
        states[-1]["state"] = "hold_phrase"
    return states


def _stabilize_subtitle_timeline(events: list[dict], cfg=None) -> tuple[list[dict], dict]:
    cfg = cfg or {}
    if not events:
        return [], {
            "subtitle_event_overlap_count": 0,
            "subtitle_persisted_gaps_count": 0,
            "subtitle_gap_blink_count": 0,
            "subtitle_turn_retire_count": 0,
        }
    continuity_mode = str(cfg.get("subtitle_continuity_mode", "always_on_short_gaps") or "always_on_short_gaps").lower()
    hide_when_silent = bool(cfg.get("subtitle_hide_when_silent", False))
    if continuity_mode not in {"always_on_short_gaps", "always_on", "off"}:
        continuity_mode = "always_on_short_gaps"
    if hide_when_silent and continuity_mode == "always_on":
        continuity_mode = "always_on_short_gaps"
    persist_all = continuity_mode == "always_on"
    keep_short_gaps = continuity_mode in {"always_on_short_gaps", "always_on"}
    persist_gap = _subtitle_persist_gap_seconds(cfg)
    phrase_ttl = max(persist_gap, _subtitle_phrase_ttl_seconds(cfg))
    clear_gap = max(
        persist_gap,
        float(cfg.get("subtitle_clear_gap_seconds", max(persist_gap * 2.5, 4.0))),
        1.25,
    )
    blink_threshold = _subtitle_gap_blink_threshold_seconds(cfg)
    hold_max = max(0.35, float(cfg.get("subtitle_hold_max_seconds", 0.48)))
    epsilon = max(0.0, _subtitle_state_epsilon_seconds(cfg))
    ordered = sorted((dict(item) for item in events), key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))
    first = dict(ordered[0])
    first["start"] = _ass_quantize_time(first.get("start", 0.0), "floor")
    first["end"] = max(first["start"] + 0.01, _ass_quantize_time(first.get("end", first["start"] + 0.12), "ceil"))
    stabilized = [first]
    persisted_gaps_count = 0
    gap_blink_count = 0
    visual_drop_count = 0
    blackout_count = 0
    phrase_clear_count = 0
    phrase_replace_count = 0
    soft_hold_count = 0
    turn_retire_count = 0
    for event in ordered[1:]:
        current = dict(event)
        prev = stabilized[-1]
        prev_end = _ass_quantize_time(prev["end"], "floor")
        current_start = _ass_quantize_time(current["start"], "floor")
        current_end = max(current_start + 0.01, _ass_quantize_time(current.get("end", current_start + 0.12), "ceil"))
        current["start"] = current_start
        current["end"] = current_end
        if current_start < prev_end:
            current_start = prev_end
            current["start"] = current_start
        gap = max(0.0, current_start - prev_end)
        prev_age = max(0.0, prev_end - float(prev.get("start", prev_end)))
        
        # PHASE 3B: PRIORITY 1 — Hard hold-until-next rule (never allow gap < 0.90s if next exists)
        hold_until_next_max = 0.90
        if gap > 0.0 and gap <= hold_until_next_max:
            # Force bridge: no flicker allowed on short gaps
            prev["end"] = _ass_quantize_time(current_start, "floor")
            persisted_gaps_count += 1
            if str(prev.get("text", "")) != str(current.get("text", "")):
                current["state"] = "replace_phrase"
                phrase_replace_count += 1
        elif gap > 0.0 and prev_age >= phrase_ttl:
            prev["end"] = _ass_quantize_time(current_start, "floor")
            persisted_gaps_count += 1
            soft_hold_count += 1
            phrase_clear_count += 1
            turn_retire_count += 1
            current["state"] = "show_phrase"
        elif gap > 0.0 and (persist_all or (keep_short_gaps and gap <= persist_gap)):
            capped_end = min(current_start, prev_end + hold_max)
            prev["end"] = _ass_quantize_time(max(prev_end, capped_end), "floor")
            persisted_gaps_count += 1
            soft_hold_count += 1
            if str(prev.get("text", "")) != str(current.get("text", "")):
                current["state"] = "replace_phrase"
                phrase_replace_count += 1
        elif gap > 0.0 and gap <= clear_gap:
            prev["end"] = _ass_quantize_time(current_start, "floor")
            persisted_gaps_count += 1
            soft_hold_count += 1
            current["state"] = "replace_phrase"
            phrase_replace_count += 1
        else:
            final_gap = max(0.0, current_start - float(prev["end"]))
            if final_gap > 0.0 and final_gap <= blink_threshold:
                gap_blink_count += 1
            if final_gap > blink_threshold:
                visual_drop_count += 1
                blackout_count += 1
                phrase_clear_count += 1
                current["state"] = "show_phrase"
        if float(current["end"]) <= float(current["start"]):
            current["end"] = _ass_quantize_time(float(current["start"]) + 0.12, "ceil")
        stabilized.append(current)
    actual_overlap_count = 0
    for left, right in zip(stabilized, stabilized[1:]):
        if float(left["end"]) > float(right["start"]):
            left["end"] = _ass_quantize_time(max(float(left["start"]) + 0.01, float(right["start"])), "floor")
    for left, right in zip(stabilized, stabilized[1:]):
        if float(left["end"]) > float(right["start"]):
            actual_overlap_count += 1
    hold_durations = [max(0.0, float(item.get("end", 0.0)) - float(item.get("start", 0.0))) for item in stabilized]
    return stabilized, {
        "subtitle_event_overlap_count": actual_overlap_count,
        "subtitle_persisted_gaps_count": persisted_gaps_count,
        "subtitle_gap_blink_count": gap_blink_count,
        "subtitle_visual_drop_count": visual_drop_count,
        "subtitle_blackout_count": blackout_count,
        "subtitle_phrase_clear_count": phrase_clear_count,
        "subtitle_phrase_replace_count": phrase_replace_count,
        "subtitle_soft_hold_count": soft_hold_count,
        "subtitle_turn_retire_count": turn_retire_count,
        "subtitle_replace_without_clear_count": phrase_replace_count,
        "subtitle_true_clear_count": phrase_clear_count,
        "subtitle_hold_duration_p95": round(_percentile(hold_durations, 0.95), 4),
    }


def build_sentence_segments(segments: list[dict], cfg=None) -> list[dict]:
    cfg = cfg or {}
    if not segments:
        return []
    sentence_segments = []
    current_words = []
    current_avg = []
    for segment in segments:
        words = list(segment.get("words") or [])
        if not words:
            continue
        gap = 0.0
        if current_words:
            gap = max(0.0, float(words[0]["start"]) - float(current_words[-1]["end"]))
        starts_new = False
        if current_words:
            prev_text = current_words[-1]["text"]
            starts_new = (
                gap >= PAUSE_SPLIT_THRESHOLD
                or prev_text.endswith(TERMINAL_PUNCTUATION)
                or len(current_words) >= _sentence_word_limit(cfg)
            )
        if starts_new:
            average_logprob = sum(current_avg) / max(1, len(current_avg))
            for chunk in _split_words_into_sentence_chunks(current_words, cfg=cfg):
                sentence_segments.append(_make_sentence_segment(chunk, average_logprob, cfg=cfg))
            current_words = []
            current_avg = []
        current_words.extend(words)
        current_avg.append(float(segment.get("avg_logprob", -1.2)))
        if segment.get("text", "").strip().endswith(TERMINAL_PUNCTUATION) and current_words:
            average_logprob = sum(current_avg) / max(1, len(current_avg))
            for chunk in _split_words_into_sentence_chunks(current_words, cfg=cfg):
                sentence_segments.append(_make_sentence_segment(chunk, average_logprob, cfg=cfg))
            current_words = []
            current_avg = []
    if current_words:
        average_logprob = sum(current_avg) / max(1, len(current_avg))
        for chunk in _split_words_into_sentence_chunks(current_words, cfg=cfg):
            sentence_segments.append(_make_sentence_segment(chunk, average_logprob, cfg=cfg))
    return sentence_segments or segments


def _split_long_segment(segment: dict, cfg=None) -> list[dict]:
    cfg = cfg or {}
    max_chars = _segment_max_chars(cfg)
    max_lines = _segment_max_lines(cfg)
    text = segment.get("text", "")
    words = list(segment.get("words") or [])
    if len(text) <= max_chars * max_lines:
        segment["caption_text"] = _split_caption_lines(text, max_chars=max_chars, max_lines=max_lines)
        return [segment]
    if not words:
        segment["caption_text"] = _split_caption_lines(text, max_chars=max_chars, max_lines=max_lines)
        return [segment]

    chunks = []
    current_words = []
    current_chars = 0
    target_chars = max_chars * max_lines
    max_visible_words = _segment_word_limit(cfg)
    for word in words:
        token = word["text"]
        projected = current_chars + (1 if current_words else 0) + len(token)
        current_words.append(word)
        current_chars = projected
        boundary = token.endswith((".", "!", "?", ",", ";", ":"))
        if (
            current_chars >= target_chars
            or len(current_words) >= max_visible_words * max_lines
            or (boundary and current_chars >= max_chars)
        ):
            chunks.append(current_words)
            current_words = []
            current_chars = 0
    if current_words:
        chunks.append(current_words)

    result = []
    for chunk in chunks:
        text = _clean_text(" ".join(item["text"] for item in chunk))
        if not text:
            continue
        result.append(
            {
                "start": float(chunk[0]["start"]),
                "end": float(chunk[-1]["end"]),
                "text": text,
                "avg_logprob": float(segment.get("avg_logprob", -1.2)),
                "words": list(chunk),
                "caption_text": _split_caption_lines(text, max_chars=max_chars, max_lines=max_lines),
            }
        )
    return result or [segment]


def _normalize_segments(segments, cfg=None):
    cfg = cfg or {}
    normalized = []
    for segment in segments:
        text = _clean_text(getattr(segment, "text", "") or "")
        if not text:
            continue
        words = []
        for word in getattr(segment, "words", []) or []:
            token = _clean_text(getattr(word, "word", "") or "")
            if not token:
                continue
            words.append(
                {
                    "start": float(getattr(word, "start", getattr(segment, "start", 0.0)) or 0.0),
                    "end": float(getattr(word, "end", getattr(segment, "end", 0.0)) or 0.0),
                    "text": token,
                }
            )
        normalized.append(
            {
                "start": float(getattr(segment, "start", 0.0) or 0.0),
                "end": float(getattr(segment, "end", 0.0) or 0.0),
                "text": text,
                "avg_logprob": float(getattr(segment, "avg_logprob", -1.2) or -1.2),
                "words": words,
            }
        )
    if not normalized:
        return []

    merged = [normalized[0]]
    for segment in normalized[1:]:
        prev = merged[-1]
        gap = max(0.0, segment["start"] - prev["end"])
        short_prev = len(prev["text"]) <= 28
        short_curr = len(segment["text"]) <= 28
        sentence_mode = str(cfg.get("subtitle_display_mode", cfg.get("subtitle_chunk_mode", "sentence_highlight"))).lower() == "sentence_highlight"
        prev_closed = prev["text"].endswith(TERMINAL_PUNCTUATION)
        if gap <= 0.22 and (short_prev or short_curr) and len(prev["text"]) + len(segment["text"]) <= (52 if sentence_mode else 64) and not prev_closed:
            prev["end"] = max(prev["end"], segment["end"])
            prev["text"] = _clean_text(f"{prev['text']} {segment['text']}")
            prev["avg_logprob"] = (prev["avg_logprob"] + segment["avg_logprob"]) / 2.0
            prev["words"].extend(segment["words"])
        else:
            merged.append(segment)

    result = []
    for segment in merged:
        result.extend(_split_long_segment(segment, cfg))
    return result


def _filter_short_silence(segments: list[dict], keep_gap_seconds: float, cfg=None) -> list[dict]:
    cfg = cfg or {}
    if not segments:
        return []
    sentence_mode = str(cfg.get("subtitle_display_mode", cfg.get("subtitle_chunk_mode", "sentence_highlight"))).lower() == "sentence_highlight"
    keep_gap_seconds = max(1.0, float(keep_gap_seconds))
    filtered = [segments[0]]
    for segment in segments[1:]:
        prev = filtered[-1]
        combined_words = len((prev.get("text") or "").split()) + len((segment.get("text") or "").split())
        if (
            segment["start"] - prev["end"] <= keep_gap_seconds
            and combined_words <= (max(6, _segment_word_limit(cfg) * 2) if sentence_mode else max(8, _segment_word_limit(cfg) * 3))
            and not str(prev.get("text", "")).strip().endswith(TERMINAL_PUNCTUATION)
        ):
            prev["end"] = max(prev["end"], segment["end"])
            prev["text"] = _clean_text(f"{prev['text']} {segment['text']}")
            prev["caption_text"] = _split_caption_lines(
                prev["text"],
                max_chars=_segment_max_chars(cfg),
                max_lines=_segment_max_lines(cfg),
            )
            prev["words"].extend(segment["words"])
            prev["avg_logprob"] = (prev["avg_logprob"] + segment["avg_logprob"]) / 2.0
        else:
            filtered.append(segment)
    return filtered


def _estimate_scene_mood(text: str) -> str:
    cleaned = _clean_text(text).lower()
    if not cleaned:
        return "neutral"
    if any(token in cleaned for token in ["убью", "стой", "нет", "нельзя", "труп", "кров", "страш", "ужас"]):
        return "tension"
    if any(token in cleaned for token in ["смешно", "ха", "шут", "улыб", "весело"]):
        return "humor"
    if any(token in cleaned for token in ["почему", "зачем", "правда", "знаешь", "послушай"]):
        return "conversation"
    return "neutral"


def summarize_subtitle_context(segments: list[dict], language: str = "auto", max_keywords: int = 4) -> dict:
    full_text = _clean_text(" ".join((item.get("text", "") or "") for item in segments))
    line_count = len(segments)
    if not full_text:
        return {"summary_text": "", "keywords": [], "mood": "neutral", "line_count": line_count}
    tokens = _tokenize(full_text)
    stopwords = set(RU_STOPWORDS)
    if str(language).lower().startswith("en"):
        stopwords |= {"this", "that", "with", "have", "what", "when", "where", "your", "from", "they", "them"}
    counts = {}
    for token in tokens:
        if len(token) < 4 or token in stopwords:
            continue
        counts[token] = counts.get(token, 0) + 1
    keywords = [item[0] for item in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:max_keywords]]
    summary_source = full_text
    if len(summary_source) > 96:
        cut = summary_source[:96].rsplit(" ", 1)[0].strip()
        summary_source = cut or summary_source[:96]
    return {
        "summary_text": summary_source,
        "keywords": keywords,
        "mood": _estimate_scene_mood(full_text),
        "line_count": line_count,
    }


def build_ass_word_events(
    segments: Iterable[dict],
    batch_size: int = 2,
    cfg=None,
) -> list[dict]:
    cfg = cfg or {}
    display_mode = str(cfg.get("subtitle_display_mode", cfg.get("subtitle_chunk_mode", "sentence_highlight"))).lower()
    renderer_mode = str(cfg.get("subtitle_renderer_mode", "persistent_sentence_layer") or "persistent_sentence_layer").lower()
    events = []
    for segment in segments:
        words = list(segment.get("words") or [])
        if not words:
            events.append(
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment.get("caption_text", segment.get("text", "")),
                }
            )
            continue
        if renderer_mode == "persistent_sentence_layer":
            events.extend(_persistent_sentence_events(segment, batch_size=batch_size, cfg=cfg))
        else:
            max_chars = max(18, _segment_max_chars(cfg) + 4)
            max_lines = _segment_max_lines(cfg)
            step = max(1, int(batch_size))
            for index in range(0, len(words), step):
                active_ids = set(range(index, min(index + step, len(words))))
                rendered = _render_highlighted_sentence(words, active_ids, display_mode)
                start = float(words[index]["start"])
                next_boundary = float(words[index + step]["start"]) if index + step < len(words) else float(segment["end"])
                end = next_boundary if index + step < len(words) else float(segment["end"])
                events.append(
                    {
                        "start": round(start, 3),
                        "end": round(max(start + 0.12, end), 3),
                        "text": _split_caption_lines(rendered, max_chars=max_chars, max_lines=max_lines),
                        "state": "advance_highlight",
                    }
                )
    if not events:
        return events
    stabilized, stats = _stabilize_subtitle_timeline(events, cfg=cfg)
    build_ass_word_events.last_stats = {
        "subtitle_event_overlap_count": int(stats.get("subtitle_event_overlap_count", 0)),
        "subtitle_persisted_gaps_count": int(stats.get("subtitle_persisted_gaps_count", 0)),
        "subtitle_gap_blink_count": int(stats.get("subtitle_gap_blink_count", 0)),
        "subtitle_visual_drop_count": int(stats.get("subtitle_visual_drop_count", 0)),
        "subtitle_phrase_clear_count": int(stats.get("subtitle_phrase_clear_count", 0)),
        "subtitle_phrase_replace_count": int(stats.get("subtitle_phrase_replace_count", 0)),
        "subtitle_soft_hold_count": int(stats.get("subtitle_soft_hold_count", 0)),
        "subtitle_turn_retire_count": int(stats.get("subtitle_turn_retire_count", 0)),
        "subtitle_replace_without_clear_count": int(stats.get("subtitle_replace_without_clear_count", 0)),
        "subtitle_true_clear_count": int(stats.get("subtitle_true_clear_count", 0)),
        "subtitle_hold_duration_p95": float(stats.get("subtitle_hold_duration_p95", 0.0)),
        "subtitle_continuity_mode": str(cfg.get("subtitle_continuity_mode", "always_on_short_gaps")),
        "subtitle_persist_gap_seconds": _subtitle_persist_gap_seconds(cfg),
        "subtitle_renderer_mode": renderer_mode,
    }
    return stabilized


def _estimate_scene_mood(text: str) -> str:
    cleaned = _clean_text(text).lower()
    if not cleaned:
        return "neutral"
    if any(token in cleaned for token in ["убью", "стой", "нет", "нельзя", "труп", "кров", "страш", "ужас"]):
        return "tension"
    if any(token in cleaned for token in ["смешно", "ха", "шут", "улыб", "весело"]):
        return "humor"
    if any(token in cleaned for token in ["почему", "зачем", "правда", "знаешь", "послушай"]):
        return "conversation"
    return "neutral"


def subtitle_story_signals(subtitle_info: dict, cfg=None) -> dict:
    cfg = cfg or {}
    segments = list(subtitle_info.get("segments") or [])
    full_text = _clean_text(" ".join(item.get("text", "") for item in segments))
    tokens = _tokenize(full_text)
    language = str(subtitle_info.get("language", cfg.get("subtitle_language", "auto"))).lower()
    subtitle_confidence = float(subtitle_info.get("confidence", 0.0) or 0.0)
    connectors = LEAD_CONNECTORS_EN if language.startswith("en") else LEAD_CONNECTORS_RU
    emotion_tokens = EMOTION_TOKENS_EN if language.startswith("en") else EMOTION_TOKENS_RU
    question_words = QUESTION_WORDS_EN if language.startswith("en") else QUESTION_WORDS_RU
    payoff_tokens = PAYOFF_TOKENS_EN if language.startswith("en") else PAYOFF_TOKENS_RU
    first_token = tokens[0] if tokens else ""
    first_segment_start = float(segments[0].get("start", 0.0)) if segments else 0.0
    first_segment_text = (segments[0].get("text", "") if segments else "").strip()
    last_text = (segments[-1].get("text", "") if segments else "").strip()
    dialogue_exchange_score = min(1.0, len(segments) / 4.0)
    question_count = full_text.count("?")
    question_answer_bonus = 0.0
    if question_count and len(segments) >= 2 and not last_text.endswith("?"):
        question_answer_bonus = min(1.0, question_count * 0.35)
    emotion_signal = min(1.0, sum(1 for token in tokens if token in emotion_tokens) / 3.0)
    starts_mid_phrase = bool(first_token in connectors or first_segment_start > 0.55)
    line_like_closure = bool(last_text.endswith((".", "!", "?")))
    hook_score = 0.0
    if first_segment_text:
        hook_score = 0.30
        if first_segment_start <= 0.35:
            hook_score += 0.22
        if "?" in first_segment_text or any(token in first_segment_text.lower() for token in question_words):
            hook_score += 0.18
        if any(token in first_segment_text.lower() for token in emotion_tokens):
            hook_score += 0.18
        if len(first_segment_text) <= 48:
            hook_score += 0.12
    hook_score = min(1.0, hook_score)
    development_score = min(
        1.0,
        dialogue_exchange_score * 0.48
        + min(1.0, len(tokens) / 22.0) * 0.24
        + min(1.0, len(segments) / 3.0) * 0.28,
    )
    closure_score = min(
        1.0,
        (0.55 if line_like_closure else 0.18)
        + question_answer_bonus * 0.25
        + (0.20 if any(token in full_text.lower() for token in payoff_tokens) else 0.0),
    )
    story_has_payoff = closure_score >= float(cfg.get("closure_score_threshold", 0.32))
    sentence_start_safe = not starts_mid_phrase and first_segment_start <= 0.45
    sentence_end_safe = line_like_closure or question_answer_bonus > 0.15 or story_has_payoff
    interestingness = min(
        1.0,
        dialogue_exchange_score * 0.18
        + question_answer_bonus * 0.16
        + emotion_signal * 0.14
        + hook_score * 0.20
        + development_score * 0.14
        + closure_score * 0.18,
    )
    ass_stats = dict(getattr(build_ass_word_events, "last_stats", {}) or {})
    language_consistency = _subtitle_language_consistency(full_text, language)
    text_sanity = _subtitle_text_sanity_score(full_text, language)
    hold_p95 = float(ass_stats.get("subtitle_hold_duration_p95", 0.0) or 0.0)
    subtitle_quality_score = min(
        1.0,
        subtitle_confidence * 0.34
        + text_sanity * 0.38
        + language_consistency * 0.18
        + min(1.0, len(segments) / 4.0) * 0.10,
    )
    return {
        "subtitle_confidence": round(subtitle_confidence, 4),
        "dialogue_exchange_score": round(dialogue_exchange_score, 4),
        "question_answer_bonus": round(question_answer_bonus, 4),
        "emotion_signal": round(emotion_signal, 4),
        "hook_score": round(hook_score, 4),
        "development_score": round(development_score, 4),
        "closure_score": round(closure_score, 4),
        "story_has_payoff": bool(story_has_payoff),
        "sentence_start_safe": bool(sentence_start_safe),
        "sentence_end_safe": bool(sentence_end_safe),
        "starts_mid_phrase": starts_mid_phrase,
        "line_like_closure": line_like_closure,
        "interestingness_score": round(interestingness, 4),
        "subtitle_sentence_count": len(segments),
        "meaningful_text_length": len(full_text),
        "avg_sentence_duration": round(sum(max(0.0, item["end"] - item["start"]) for item in segments) / max(1, len(segments)), 4),
        "avg_words_per_sentence": round(sum(len((item.get("text") or "").split()) for item in segments) / max(1, len(segments)), 4),
        "subtitle_text_sanity_score": round(text_sanity, 4),
        "subtitle_language_consistency": round(language_consistency, 4),
        "subtitle_quality_score": round(subtitle_quality_score, 4),
        "subtitle_blackout_count": int(ass_stats.get("subtitle_blackout_count", 0)),
        "subtitle_visible_block_stats": {
            "visible_word_count": _segment_word_limit(cfg),
            "avg_chars_per_visible_block": _segment_max_chars(cfg),
            "max_lines_observed": _segment_max_lines(cfg),
            "subtitle_hold_too_long": hold_p95 > max(1.45, float(cfg.get("subtitle_hold_max_seconds", 0.38)) * 3.5),
            "subtitle_hold_duration_p95": round(hold_p95, 4),
        },
        "subtitle_event_overlap_count": int(ass_stats.get("subtitle_event_overlap_count", 0)),
        "subtitle_persisted_gaps_count": int(ass_stats.get("subtitle_persisted_gaps_count", 0)),
        "subtitle_gap_blink_count": int(ass_stats.get("subtitle_gap_blink_count", 0)),
        "subtitle_visual_drop_count": int(ass_stats.get("subtitle_visual_drop_count", 0)),
        "subtitle_blackout_count": int(ass_stats.get("subtitle_blackout_count", 0)),
        "subtitle_phrase_clear_count": int(ass_stats.get("subtitle_phrase_clear_count", 0)),
        "subtitle_phrase_replace_count": int(ass_stats.get("subtitle_phrase_replace_count", 0)),
        "subtitle_soft_hold_count": int(ass_stats.get("subtitle_soft_hold_count", 0)),
        "subtitle_turn_retire_count": int(ass_stats.get("subtitle_turn_retire_count", 0)),
        "subtitle_replace_without_clear_count": int(ass_stats.get("subtitle_replace_without_clear_count", 0)),
        "subtitle_true_clear_count": int(ass_stats.get("subtitle_true_clear_count", 0)),
        "subtitle_hold_duration_p95": round(hold_p95, 4),
        "subtitle_continuity_mode": ass_stats.get("subtitle_continuity_mode", str(cfg.get("subtitle_continuity_mode", "always_on"))),
        "subtitle_persist_gap_seconds": float(ass_stats.get("subtitle_persist_gap_seconds", _subtitle_persist_gap_seconds(cfg))),
        "subtitle_renderer_mode": ass_stats.get("subtitle_renderer_mode", str(cfg.get("subtitle_renderer_mode", "persistent_sentence_layer"))),
    }


def _remap_time_after_cuts(seconds: float, removed_segments: list[tuple[float, float]]) -> float:
    value = max(0.0, float(seconds))
    shift = 0.0
    for start, end in removed_segments or []:
        start = float(start)
        end = float(end)
        if value <= start:
            break
        shift += max(0.0, min(value, end) - start)
    return max(0.0, value - shift)


def remap_subtitle_info_after_cuts(
    subtitle_info: dict,
    removed_segments: list[tuple[float, float]],
    out_dir: str,
    idx: int,
    cfg=None,
):
    cfg = cfg or {}
    segments = list(subtitle_info.get("segments") or [])
    if not segments:
        return subtitle_info
    remapped = []
    for segment in segments:
        start = _remap_time_after_cuts(float(segment.get("start", 0.0) or 0.0), removed_segments)
        end = _remap_time_after_cuts(float(segment.get("end", 0.0) or 0.0), removed_segments)
        if end <= start:
            continue
        updated = dict(segment)
        updated["start"] = round(start, 3)
        updated["end"] = round(max(start + 0.01, end), 3)
        words = []
        for word in list(segment.get("words") or []):
            word_start = _remap_time_after_cuts(float(word.get("start", 0.0) or 0.0), removed_segments)
            word_end = _remap_time_after_cuts(float(word.get("end", 0.0) or 0.0), removed_segments)
            if word_end <= word_start:
                continue
            words.append(
                {
                    **word,
                    "start": round(word_start, 3),
                    "end": round(max(word_start + 0.01, word_end), 3),
                }
            )
        if words:
            updated["words"] = words
        remapped.append(updated)
    if not remapped:
        return subtitle_info
    srt_path = os.path.join(out_dir, f"cand_{idx}.srt")
    try:
        with open(srt_path, "w", encoding="utf-8") as handle:
            for line_number, segment in enumerate(remapped, start=1):
                handle.write(f"{line_number}\n")
                handle.write(f"{seconds_to_hhmmssms(segment['start'])} --> {seconds_to_hhmmssms(segment['end'])}\n")
                handle.write(segment.get("caption_text", segment.get("text", "")) + "\n\n")
    except Exception:
        pass
    ass_events = build_ass_word_events(
        remapped,
        batch_size=int(cfg.get("subtitle_words_per_batch", cfg.get("subtitle_word_batch_size", 2))),
        cfg=cfg,
    )
    summary = summarize_subtitle_context(remapped, language=str(subtitle_info.get("language", cfg.get("subtitle_language", "auto"))))
    remapped_info = dict(subtitle_info)
    remapped_info["srt_path"] = srt_path if os.path.exists(srt_path) else subtitle_info.get("srt_path")
    remapped_info["segments"] = remapped
    remapped_info["line_count"] = len(remapped)
    remapped_info["ass_word_events"] = ass_events
    remapped_info["summary"] = summary
    remapped_info["signals"] = subtitle_story_signals(remapped_info, cfg=cfg)
    remapped_info["subtitle_remap_used"] = True
    remapped_info["subtitle_remap_after_silence_cut"] = bool(cfg.get("subtitle_remap_after_silence_cut", True))
    return remapped_info


def transcribe_segment(wav_path: str, out_dir: str, idx: int, cfg=None):
    cfg = cfg or {}
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return {
            "srt_path": None,
            "segments": [],
            "line_count": 0,
            "confidence": 0.0,
            "language": str(cfg.get("subtitle_language", "auto")),
        }

    profile = str(cfg.get("transcription_profile", "balanced")).lower()
    processing_mode = _processing_mode(cfg)
    model_size = PROFILE_TO_MODEL.get(profile, "base")
    language = str(cfg.get("subtitle_language", "auto")).lower()
    language = None if language == "auto" else language
    model = None

    for compute_type in ["int8", "int8_float16", None]:
        try:
            if compute_type is None:
                model = WhisperModel(model_size, device="cpu", local_files_only=True)
            else:
                model = WhisperModel(model_size, device="cpu", compute_type=compute_type, local_files_only=True)
            break
        except Exception:
            model = None

    if model is None:
        return {
            "srt_path": None,
            "segments": [],
            "line_count": 0,
            "confidence": 0.0,
            "language": str(cfg.get("subtitle_language", "auto")),
        }

    beam_size = 1 if profile == "fast" else PROCESSING_MODE_TO_BEAM.get(processing_mode, 5)
    normalized = []
    info = None
    subtitle_correction_used = False
    subtitle_quality_retry_used = False
    subtitle_alignment_used = bool(cfg.get("subtitle_alignment_used", False))
    attempted_languages = [language]
    if language is not None:
        attempted_languages.append(None)
    retry_prompt = _subtitle_repair_prompt(language or cfg.get("subtitle_language", "auto"), cfg) if bool(cfg.get("subtitle_context_prompt_enabled", True)) else None

    def _run_pass(lang, beam, condition_on_previous_text=True, prompt=None, temperature=None):
        kwargs = {
            "language": lang,
            "beam_size": int(beam),
            "vad_filter": True,
            "condition_on_previous_text": bool(condition_on_previous_text),
            "word_timestamps": True,
        }
        if prompt:
            kwargs["initial_prompt"] = prompt
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        return model.transcribe(wav_path, **kwargs)

    for lang in attempted_languages:
        try:
            segments, info = _run_pass(lang, beam_size, condition_on_previous_text=True)
            normalized = _normalize_segments(list(segments), cfg=cfg)
            if normalized:
                break
        except Exception:
            normalized = []

    if not normalized:
        return {
            "srt_path": None,
            "segments": [],
            "line_count": 0,
            "confidence": 0.0,
            "language": str(cfg.get("subtitle_language", "auto")),
        }
    normalized = _filter_short_silence(normalized, float(cfg.get("keep_dialogue_gap_seconds", 1.0)), cfg=cfg)
    normalized, subtitle_correction_used = _subtitle_correction_pass(normalized, cfg=cfg)
    avg_logprob = sum(item["avg_logprob"] for item in normalized) / len(normalized)
    confidence = _subtitle_confidence_from_logprob(avg_logprob)
    transcript_text = " ".join(item.get("text", "") for item in normalized)
    transcript_sanity = _subtitle_text_sanity_score(transcript_text, getattr(info, "language", language or cfg.get("subtitle_language", "auto")))
    transcript_consistency = _subtitle_language_consistency(transcript_text, getattr(info, "language", language or cfg.get("subtitle_language", "auto")))
    if processing_mode == "enhanced_local" and (
        confidence < 0.58 and transcript_sanity < float(cfg.get("subtitle_retry_text_sanity_threshold", 0.72))
    ):
        try:
            retry_language = language or getattr(info, "language", None) or cfg.get("subtitle_language", "auto")
            segments, info = _run_pass(retry_language, max(7, beam_size), condition_on_previous_text=False, prompt=retry_prompt, temperature=0)
            retry = _filter_short_silence(_normalize_segments(list(segments), cfg=cfg), float(cfg.get("keep_dialogue_gap_seconds", 1.0)), cfg=cfg)
            retry, correction_retry = _subtitle_correction_pass(retry, cfg=cfg)
            if retry:
                retry_avg = sum(item["avg_logprob"] for item in retry) / len(retry)
                retry_conf = _subtitle_confidence_from_logprob(retry_avg)
                if retry_conf >= confidence or _looks_suspicious_text(" ".join(item.get("text", "") for item in normalized)):
                    normalized = retry
                    avg_logprob = retry_avg
                    confidence = retry_conf
                    subtitle_correction_used = subtitle_correction_used or correction_retry
        except Exception:
            pass
    if language and str(language).startswith("ru") and (
        confidence < 0.45 and transcript_consistency < float(cfg.get("subtitle_retry_language_consistency_threshold", 0.84))
    ):
        try:
            segments, info = _run_pass("ru", max(5, beam_size), condition_on_previous_text=False, prompt=retry_prompt)
            retry = _filter_short_silence(_normalize_segments(list(segments), cfg=cfg), float(cfg.get("keep_dialogue_gap_seconds", 1.0)), cfg=cfg)
            if retry:
                retry, correction_retry = _subtitle_correction_pass(retry, cfg=cfg)
                normalized = retry
                avg_logprob = sum(item["avg_logprob"] for item in normalized) / len(normalized)
                confidence = _subtitle_confidence_from_logprob(avg_logprob)
                subtitle_correction_used = subtitle_correction_used or correction_retry
        except Exception:
            pass
    subtitle_retry_confidence_threshold = float(cfg.get("subtitle_retry_confidence_threshold", 0.70))
    subtitle_retry_text_sanity_threshold = float(cfg.get("subtitle_retry_text_sanity_threshold", 0.72))
    subtitle_retry_language_consistency_threshold = float(cfg.get("subtitle_retry_language_consistency_threshold", 0.84))
    if normalized and not subtitle_quality_retry_used and (
        (
            confidence < subtitle_retry_confidence_threshold
            and transcript_sanity < subtitle_retry_text_sanity_threshold
        )
        or (
            transcript_consistency < subtitle_retry_language_consistency_threshold
            and _looks_suspicious_text(transcript_text)
            and confidence < subtitle_retry_confidence_threshold
        )
        or _looks_suspicious_text(transcript_text)
    ):
        try:
            retry_language = language or getattr(info, "language", None) or cfg.get("subtitle_language", "auto")
            segments, info = _run_pass(retry_language, max(8, beam_size), condition_on_previous_text=False, prompt=retry_prompt, temperature=0)
            retry = _filter_short_silence(_normalize_segments(list(segments), cfg=cfg), float(cfg.get("keep_dialogue_gap_seconds", 1.0)), cfg=cfg)
            retry, correction_retry = _subtitle_correction_pass(retry, cfg=cfg)
            if retry:
                retry_avg = sum(item["avg_logprob"] for item in retry) / len(retry)
                retry_conf = _subtitle_confidence_from_logprob(retry_avg)
                retry_text = " ".join(item.get("text", "") for item in retry)
                retry_sanity = _subtitle_text_sanity_score(retry_text, getattr(info, "language", retry_language or cfg.get("subtitle_language", "auto")))
                retry_consistency = _subtitle_language_consistency(retry_text, getattr(info, "language", retry_language or cfg.get("subtitle_language", "auto")))
                if (
                    retry_conf >= confidence
                    or retry_sanity >= transcript_sanity
                    or retry_consistency >= transcript_consistency
                    or len(retry) > len(normalized)
                ):
                    normalized = retry
                    avg_logprob = retry_avg
                    confidence = retry_conf
                    transcript_text = retry_text
                    transcript_sanity = retry_sanity
                    transcript_consistency = retry_consistency
                    subtitle_quality_retry_used = True
                    subtitle_correction_used = subtitle_correction_used or correction_retry
        except Exception:
            pass
    display_segments = normalized
    if str(cfg.get("subtitle_display_mode", cfg.get("subtitle_chunk_mode", "sentence_highlight"))).lower() == "sentence_highlight":
        display_segments = build_sentence_segments(normalized, cfg=cfg)
    srt_path = os.path.join(out_dir, f"cand_{idx}.srt")
    try:
        with open(srt_path, "w", encoding="utf-8") as handle:
            for line_number, segment in enumerate(display_segments, start=1):
                handle.write(f"{line_number}\n")
                handle.write(
                    f"{seconds_to_hhmmssms(segment['start'])} --> {seconds_to_hhmmssms(segment['end'])}\n"
                )
                handle.write(segment["caption_text"] + "\n\n")
    except Exception:
        try:
            if os.path.exists(srt_path):
                os.remove(srt_path)
        except Exception:
            pass
        srt_path = None

    if not normalized or not srt_path or not os.path.exists(srt_path):
        return {
            "srt_path": None,
            "segments": [],
            "line_count": 0,
            "confidence": 0.0,
            "language": getattr(info, "language", str(cfg.get("subtitle_language", "auto"))),
        }
    ass_events = build_ass_word_events(
        display_segments,
        batch_size=int(cfg.get("subtitle_words_per_batch", cfg.get("subtitle_word_batch_size", 2))),
        cfg=cfg,
    )
    summary = summarize_subtitle_context(display_segments, language=getattr(info, "language", str(cfg.get("subtitle_language", "auto"))))
    tmp_info = {
        "segments": display_segments,
        "language": getattr(info, "language", str(cfg.get("subtitle_language", "auto"))),
        "ass_word_events": ass_events,
        "summary": summary,
    }
    return {
        "srt_path": srt_path,
        "segments": display_segments,
        "line_count": len(display_segments),
        "confidence": round(confidence, 4),
        "language": getattr(info, "language", str(cfg.get("subtitle_language", "auto"))),
        "ass_word_events": ass_events,
        "summary": summary,
        "subtitle_processing_mode": processing_mode,
        "subtitle_correction_used": bool(subtitle_correction_used),
        "subtitle_quality_retry_used": bool(subtitle_quality_retry_used),
        "subtitle_alignment_used": bool(subtitle_alignment_used),
        "signals": subtitle_story_signals(tmp_info, cfg=cfg),
    }
