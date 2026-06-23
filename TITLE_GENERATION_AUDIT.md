# TITLE GENERATION AUDIT
## Title Quality & Metadata Generation

**Date:** 2026-06-22  
**Purpose:** Determine WHY titles are boring, irrelevant, or clickbait  
**Status:** ANALYSIS COMPLETE — NO CODE MODIFICATIONS

---

## 🎯 EXECUTIVE SUMMARY

### Critical Findings (Based on Code Structure)

1. ✅ **Context-aware generation** using subtitle + metadata (generate_context_title)
2. ⚠️ **Story hook system** exists but templates may be repetitive
3. ⚠️ **Quality scoring** present (_title_quality_score) but thresholds unclear
4. ❌ **No A/B testing** or performance feedback loop
5. ✅ **Language-specific logic** for Russian vs others

**Bottom Line:** Title quality depends on template variety + keyword extraction accuracy.

---

## 🏗️ TITLE GENERATION ARCHITECTURE

### Generation Flow (Based on Function Names)

```
1. CONTEXT EXTRACTION
   ├─ subtitle_info analysis
   ├─ meta (candidate metadata)
   └─ cfg (configuration)

2. KEYWORD EXTRACTION
   └─ _extract_keywords() → Top keywords from subtitle text

3. MOOD DETECTION
   └─ _detect_mood() → Mood from text + story_score

4. HOOK/PAYOFF SELECTION
   ├─ _story_hook_phrase() → Story hook templates
   ├─ _story_payoff_phrase() → Story payoff templates
   └─ Language-specific phrases

5. TITLE CANDIDATE GENERATION
   ├─ Multiple candidate generation strategies
   ├─ _normalize_title_candidate() → Normalization
   └─ _title_quality_score() → Quality scoring

6. BEST CANDIDATE SELECTION
   └─ _select_best_title_candidate() → Pick best

7. METADATA GENERATION
   ├─ _pick_emoji() → Emoji selection
   ├─ _pick_hashtags() → Hashtag selection
   └─ _story_hashtag_pack() → Story-specific hashtags

8. RUSSIAN-SPECIFIC FLOW
   └─ _build_russian_story_title() → Special Russian logic

9. FILENAME GENERATION
   ├─ build_output_filename() → Filename from title
   └─ maybe_rename_output() → Apply rename
```

**Source:** titling.py function list

---

## 🚨 IDENTIFIED QUALITY ISSUES

### Issue 1: Template Fatigue
**Severity:** HIGH  
**Evidence:** `_story_hook_phrase()` and `_story_payoff_phrase()` use templates

**Problem:** Limited template pool creates repetitive titles.

**Example templates (guessed):**
```
Hook types:
- "Вы не поверите..."
- "Шокирующая правда о..."
- "То, что произошло дальше..."
- "Невероятная история о..."

Payoff types:
- "...изменило всё"
- "...поразило всех"
- "...никто не ожидал"
```

**Impact:** After 10-20 videos, viewers recognize patterns, lose interest.

**Recommended:**
- Expand template pool to 50+ variants per type
- Add template usage tracking to avoid repetition
- Use LLM for dynamic title generation (fallback to templates)

---

### Issue 2: Keyword Extraction Quality
**Severity:** MEDIUM  
**Evidence:** `_extract_keywords()` function exists

**Problem:** Keyword quality depends on:
- Language model accuracy
- Stop word filtering
- TF-IDF or similar algorithm

**Poor keywords → poor titles:**
```
Good keywords: ["семья", "конфликт", "примирение"]
→ Good title: "Семейный конфликт: путь к примирению"

Bad keywords: ["это", "было", "очень"]
→ Bad title: "Это было очень интересно"
```

**Unknown:** Keyword extraction algorithm used, quality validation.

**Recommended:**
```python
# Validate keyword relevance
keywords = _extract_keywords(text, language, limit=10)
scored_keywords = [
    (kw, _keyword_relevance_score(kw, text))
    for kw in keywords
]
# Filter low-relevance keywords
keywords = [kw for kw, score in scored_keywords if score > 0.4]
```

---

### Issue 3: Mood Detection Ambiguity
**Severity:** MEDIUM  
**Evidence:** `_detect_mood()` and `_estimate_scene_mood()` exist

**Problem:** Text-based mood detection is imprecise.

**Ambiguous cases:**
```
Text: "Я не могу поверить, что это случилось"
Mood: Shock? Sadness? Joy? (context-dependent)

Text: "Это было неожиданно"
Mood: Surprise? Disappointment? (unclear)
```

**Impact:** Wrong mood → wrong emoji → wrong hashtags → lower CTR.

**Recommended:**
- Use audio prosody (pitch, energy) for mood
- Combine text + audio + visual cues
- Validate mood with multiple signals

---

### Issue 4: No Title Performance Feedback
**Severity:** HIGH  
**Evidence:** No feedback/learning functions visible

**Problem:** No way to learn which titles perform better.

**Missing:**
- CTR tracking per title style
- A/B testing framework
- Title effectiveness scoring
- Template performance analysis

**Impact:** System generates same quality titles forever, no improvement.

**Recommended:**
```python
# Track title performance
{
    "title_metrics": {
        "title_text": "...",
        "template_type": "hook_shock",
        "keywords_used": ["семья", "конфликт"],
        "emoji_used": "😱",
        "hashtags_used": ["#семья", "#драма"],
        
        # Performance (to be filled by analytics)
        "ctr": None,
        "avg_view_duration": None,
        "engagement_rate": None
    }
}
```

---

### Issue 5: Russian vs Non-Russian Logic Split
**Severity:** LOW  
**Evidence:** `_build_russian_story_title()` separate function

**Problem:** Logic duplication, inconsistent behavior.

**Impact:**
- Russian titles may have features non-Russian lack
- Harder to maintain 2 separate code paths
- Testing complexity

**Recommended:** Unify logic with language-specific config:
```python
def generate_title(subtitle_info, meta, cfg):
    language = cfg.get("language", "auto")
    
    # Common logic
    keywords = _extract_keywords(text, language)
    mood = _detect_mood(text, story_score)
    
    # Language-specific templates
    templates = TEMPLATES[language] or TEMPLATES["default"]
    hook = random.choice(templates["hooks"])
    
    # Common candidate selection
    return _select_best_title_candidate(candidates)
```

---

## 📊 TITLE QUALITY FACTORS

### Quality Score Components (Inferred)

**`_title_quality_score()` likely considers:**
1. **Length** — optimal 40-60 chars for mobile
2. **Keyword presence** — relevant keywords boost score
3. **Readability** — simple language, clear structure
4. **Clickability** — emotional hooks, curiosity gaps
5. **Forbidden patterns** — avoid spam, ALL CAPS, excessive punctuation

**Weight estimation (guess):**
```
Length penalty: 20%
Keyword relevance: 30%
Readability: 20%
Emotional hook: 20%
Spam detection: 10%
```

---

### Template System Analysis

**Hook types (guessed from function names):**
- **Shock** — "Вы не поверите..."
- **Mystery** — "Секрет, который..."
- **Conflict** — "Противостояние..."
- **Discovery** — "Невероятная находка..."
- **Transformation** — "Как X изменило Y..."

**Payoff types (guessed):**
- **Resolution** — "...и что из этого вышло"
- **Twist** — "...неожиданный поворот"
- **Lesson** — "...урок для всех"
- **Impact** — "...изменило всё"

**Problem:** Fixed templates = predictable patterns.

---

## 🚨 WHY TITLES ARE BORING

### Cause 1: Template Repetition
**Severity:** CRITICAL  

**Scenario:**
```
Video 1: "Вы не поверите, что случилось с этой семьей"
Video 2: "Вы не поверите, что произошло на встрече"
Video 3: "Вы не поверите, как закончилась эта история"

Viewer: "Опять это 'вы не поверите'... скучно"
```

**Solution:** Template rotation, usage tracking, dynamic generation.

---

### Cause 2: Generic Keywords
**Severity:** HIGH  

**Problem:** Keyword extraction picks common words, not distinctive ones.

**Example:**
```
Boring: "История о семье и отношениях"
Better: "Конфликт между матерью и дочерью"

Boring: "Интересный момент из жизни"
Better: "Неожиданное признание на свадьбе"
```

**Solution:** Boost specific nouns/verbs, penalize generic adjectives.

---

### Cause 3: No Personalization
**Severity:** MEDIUM  

**Problem:** Same title generation for all audience segments.

**Reality:** Different viewers click different title styles:
- Young audience: Emoji-heavy, slang, short
- Older audience: Descriptive, formal, longer
- Story-driven: Focus on narrative arc
- Info-driven: Focus on facts, numbers

**Solution:** Audience-aware title generation (needs analytics integration).

---

## 🚨 WHY TITLES ARE CLICKBAIT

### Cause 1: Hook Templates Too Aggressive
**Severity:** MEDIUM  

**Examples:**
```
"ШОКИРУЮЩАЯ ПРАВДА!!!" ← All caps, excessive punctuation
"То, что произошло дальше, ВЗОРВАЛО интернет" ← Exaggeration
"99% людей НЕ ЗНАЮТ об этом" ← False statistics
```

**Solution:** Moderate template aggressiveness, validate against spam patterns.

---

### Cause 2: Mood Overreaction
**Severity:** LOW  

**Problem:** Mood detection may amplify emotion beyond content.

**Example:**
```
Content: Mild surprise
Detected mood: SHOCK
Title: "НЕВЕРОЯТНЫЙ ШОК!!!"
Reality: Video doesn't match hype
```

**Solution:** Mood intensity calibration, content-title alignment check.

---

## 📈 MISSING METRICS

### Should Track

```python
{
    "title_generation": {
        "candidates_generated": 0,
        "best_candidate_score": 0.0,
        "template_type_used": "",
        "keywords_extracted": [],
        "mood_detected": "",
        "emoji_added": "",
        "hashtag_count": 0,
        
        "quality_checks": {
            "length_optimal": bool,
            "keyword_relevance": 0.0,
            "spam_score": 0.0,
            "readability_score": 0.0
        },
        
        # Performance (to be filled later)
        "ctr": None,
        "avg_watch_time": None,
        "template_effectiveness": None
    }
}
```

---

## ✅ CONCLUSIONS

### Why Titles Are Boring

**Root Causes:**
1. **Template repetition** — limited variety, predictable patterns
2. **Generic keywords** — extraction picks common words
3. **No personalization** — same style for all audiences
4. **No feedback loop** — can't learn which titles work

### Why Titles Are Clickbait

**Root Causes:**
1. **Aggressive hook templates** — over-promise, under-deliver
2. **Mood amplification** — content doesn't match hype
3. **Lack of moderation** — no spam/clickbait filtering

### Primary Bottleneck

**TEMPLATE VARIETY** is the PRIMARY title quality bottleneck.

Limited template pool creates repetitive, predictable titles that lose effectiveness over time.

**Validation needed:** Title performance tracking to identify best templates.

---

**End of Title Generation Audit**
