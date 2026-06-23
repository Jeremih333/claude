from __future__ import annotations

import re
import unicodedata


TOKEN_PATTERN = re.compile(r"[A-Za-z\u0400-\u04FF0-9']+")
MOJIBAKE_MARKERS = (
    "Гђ",
    "Г‘",
    "Р“С’",
    "Р“вЂ",
    "Р“С“",
    "Р“вЂљ",
    "Р“СћРІвЂљВ¬",
    "Р“СћРІвЂљВ¬РІР‚Сњ",
    "Р“СћРІвЂљВ¬РІР‚Сљ",
    "Р РЋ",
    "Р С›",
    "РЎРЊ",
    "РЎС“",
    "Р С”",
)
RU_COMMON_WORDS = {
    "СЌС‚Рѕ",
    "РІРµРґСЊ",
    "РїСЂР°РІРґР°",
    "РєСЃС‚Р°С‚Рё",
    "С‚РµРїРµСЂСЊ",
    "РІСЃРµ",
    "РЅРµ",
    "РґР°",
    "РЅРµС‚",
    "С‡С‚Рѕ",
    "РєР°Рє",
    "СЃ",
    "РЅР°",
    "СЏ",
    "С‚С‹",
    "РјС‹",
    "РІС‹",
    "РѕРЅ",
    "РѕРЅР°",
    "РѕРЅРё",
    "СЃР»РѕРІРѕРј",
    "РїРѕСЃР»СѓС€Р°Р№",
    "СЃРјРѕС‚СЂРё",
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


def _tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall((text or "").lower())


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
    rs_hits = cleaned.count("Р ") + cleaned.count("РЎ")
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


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"\s+([,.:;!?])", r"\1", cleaned)
    cleaned = cleaned.replace(" - ", " ")
    cleaned = _try_repair_mojibake(cleaned)
    return cleaned
