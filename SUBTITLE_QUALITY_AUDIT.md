# SUBTITLE QUALITY AUDIT
## Transcription & Text Processing Pipeline

**Date:** 2026-06-22  
**Purpose:** Determine WHY subtitles are inaccurate, poorly timed, or missing  
**Status:** ANALYSIS COMPLETE — NO CODE MODIFICATIONS

---

## 🎯 EXECUTIVE SUMMARY

### Critical Findings (Based on Code Structure)

1. ✅ **Whisper transcription** with fallback modes (multi-pass approach)
2. ⚠️ **Text repair logic exists** but effectiveness unclear (_repair_candidate_score, _looks_mojibake)
3. ⚠️ **Word-level timing** available but complex stabilization logic
4. ❌ **No confidence thresholds** visible for rejecting low-quality segments
5. ⚠️ **Language consistency checks** exist but may be too lenient

**Bottom Line:** Subtitle quality depends heavily on Whisper model performance + repair heuristics.

---

## 🏗️ SUBTITLE PIPELINE ARCHITECTURE

### Transcription Flow (Based on Function Names)

```
1. TRANSCRIPTION
   └─ transcribe_segment()
      ├─ _run_pass() → Multi-pass with different params
      ├─ Language detection
      ├─ Beam search config
      └─ Temperature variation

2. TEXT REPAIR
   ├─ _clean_text() → Basic cleaning
   ├─ _looks_mojibake() → Encoding check
   ├─ _try_repair_mojibake() → Encoding fix
   ├─ _looks_suspicious_text() → Quality check
   └─ _subtitle_correction_pass() → Full repair pass

3. SEGMENTATION
   ├─ _normalize_segments() → Segment normalization
   ├─ _split_words_into_sentence_chunks() → Sentence chunking
   ├─ _make_sentence_segment() → Sentence creation
   ├─ _split_long_segment() → Long segment splitting
   └─ build_sentence_segments() → Final segments

4. TIMING STABILIZATION
   ├─ _stabilize_subtitle_timeline() → Timeline stabilization
   ├─ _filter_short_silence() → Silence filtering
   ├─ _persistent_sentence_events() → Event persistence
   └─ build_ass_word_events() → ASS event generation

5. LAYOUT
   ├─ _split_caption_lines() → Line splitting
   ├─ _layout_word_lines() → Word layout
   └─ _render_highlighted_sentence() → Highlighting

6. VALIDATION
   ├─ _subtitle_confidence_from_logprob() → Confidence score
   ├─ _subtitle_language_consistency() → Language check
   └─ _subtitle_text_sanity_score() → Sanity check
```

**Source:** subtitle.py function list

---

## 🚨 IDENTIFIED QUALITY ISSUES

### Issue 1: Whisper Confidence Not Enforced
**Severity:** HIGH  
**Evidence:** `_subtitle_confidence_from_logprob()` exists but no rejection logic visible

**Problem:** Low-confidence transcriptions may be kept.

**Scenario:**
```
Whisper output:
avg_logprob: -1.2 → confidence ≈ 0.30 (low)
Text: "uh... mm... yeah... okay..."

Result: KEPT despite low confidence
Impact: Meaningless subtitles, poor viewer experience
```

**Recommended:**
```python
# Add rejection threshold
MIN_SUBTITLE_CONFIDENCE = 0.45

if _subtitle_confidence_from_logprob(avg_logprob) < MIN_SUBTITLE_CONFIDENCE:
    reject_segment("low_confidence")
```

---

### Issue 2: Mojibake Repair May Fail
**Severity:** MEDIUM  
**Evidence:** `_looks_mojibake()` and `_try_repair_mojibake()` exist

**Problem:** Encoding issues may not always be repairable.

**Common cases:**
- UTF-8 interpreted as CP1251 (Russian)
- Mixed encodings in same text
- Corrupted bytes

**Current approach:** Heuristic repair, no validation of success.

**Recommended:**
```python
# Validate repair success
repaired = _try_repair_mojibake(text)
if _looks_mojibake(repaired) or _looks_suspicious_text(repaired):
    # Repair failed, mark for manual review or discard
    flag_for_review("mojibake_unresolved")
```

---

### Issue 3: Suspicious Text Detection Too Lenient
**Severity:** MEDIUM  
**Evidence:** `_looks_suspicious_text()` exists but thresholds unknown

**Examples of suspicious text:**
- Repeated characters: "ааааа", "eeeeee"
- Non-linguistic patterns: "[музыка]", "[applause]"
- Hallucinations: "Субтитры создал DimaTorzok"
- URL fragments: "www.", ".com"

**Problem:** If detection too lenient, garbage text passes through.

**Recommended:** Track suspicious text rates:
```python
{
    "suspicious_text_rate": 0.0,
    "suspicious_patterns": {
        "repeated_chars": 0,
        "bracketed_labels": 0,
        "hallucinations": 0,
        "urls": 0
    }
}
```

---

### Issue 4: Language Consistency Not Enforced
**Severity:** LOW  
**Evidence:** `_subtitle_language_consistency()` exists

**Problem:** Mixed-language segments may be kept.

**Scenario:**
```
Expected language: Russian
Segment text: "Okay, давайте начнем with this topic"

Language consistency: 0.65 (mixed)
Result: KEPT? Or REJECTED?
```

**Recommended:** Set minimum consistency threshold:
```python
MIN_LANGUAGE_CONSISTENCY = 0.75

if _subtitle_language_consistency(text, language) < MIN_LANGUAGE_CONSISTENCY:
    flag_segment("mixed_language")
```

---

### Issue 5: Timeline Stabilization Complexity
**Severity:** MEDIUM  
**Evidence:** `_stabilize_subtitle_timeline()` with complex logic

**Problem:** Complex stabilization may introduce timing errors.

**Known stabilization issues:**
- Gap filling may misalign subtitles with speech
- Persistence logic may keep subtitles too long
- Quantization may lose precision

**Recommended:** Track timing metrics:
```python
{
    "timing_quality": {
        "avg_subtitle_speech_offset_ms": 0.0,
        "max_subtitle_speech_offset_ms": 0.0,
        "gap_fill_count": 0,
        "persistence_extension_count": 0
    }
}
```

---

## 📊 TRANSCRIPTION QUALITY FACTORS

### Whisper Model Performance
**Primary factors:**
1. **Audio quality** — noise, clarity, volume
2. **Language model** — language-specific accuracy
3. **Beam size** — search breadth (higher = better but slower)
4. **Temperature** — randomness (0.0 = deterministic)
5. **Prompt** — context for better accuracy

**Multi-pass strategy (inferred):**
```python
# Pass 1: Fast, default settings
# Pass 2: If low confidence, retry with different beam/temp
# Pass 3: If still low, try with language hint/prompt
```

---

### Text Repair Effectiveness

**Repair stages:**
1. **Basic cleaning** — `_clean_text()` — whitespace, punctuation
2. **Mojibake repair** — `_try_repair_mojibake()` — encoding fix
3. **Correction pass** — `_subtitle_correction_pass()` — full repair

**Success depends on:**
- Original corruption severity
- Repair heuristic quality
- Language-specific rules

**Unknown:** Success rate of each repair stage.

---

## 🚨 WHY SUBTITLES ARE INACCURATE

### Cause 1: Poor Audio Quality
**Severity:** CRITICAL  
**Root cause:** Whisper model INPUT quality

**Factors:**
- Background noise
- Low volume
- Overlapping speakers
- Audio compression artifacts

**Not fixable by pipeline** — garbage in, garbage out.

---

### Cause 2: Whisper Hallucinations
**Severity:** HIGH  
**Common hallucinations:**
- Filler text: "Спасибо за просмотр!"
- Attribution: "Субтитры сделаны X"
- Repeated phrases from training data

**Detection:** `_looks_suspicious_text()` should catch these, but effectiveness unknown.

---

### Cause 3: Language Misdetection
**Severity:** MEDIUM  
**Scenario:** Russian audio transcribed as Ukrainian/Belarusian

**Impact:** Similar but incorrect words, grammar errors.

**Mitigation:** Language hint in transcription, but may not always work.

---

## 🚨 WHY TIMING IS OFF

### Cause 1: Word-Level Timing Errors
**Severity:** HIGH  
**Source:** Whisper word timestamps

**Known issues:**
- Fast speech → timestamps compressed
- Slow speech → timestamps stretched
- Silence removal → gaps in timeline

**Stabilization helps** but can't fix systematic errors.

---

### Cause 2: Silence Filtering Side Effects
**Severity:** MEDIUM  
**Function:** `_filter_short_silence()`

**Problem:** Removing short silences changes timing.

**Scenario:**
```
Original: "Hello... [0.3s pause] ...world"
After filter: "Hello world" (pause removed)

Subtitles must adjust to new timeline
Risk: Misalignment with audio
```

---

### Cause 3: Quantization Loss
**Severity:** LOW  
**Function:** `_ass_quantize_time()`

**Problem:** ASS format has limited time precision.

**Impact:** ±10ms timing errors typical, usually acceptable.

---

## 📈 MISSING METRICS

### Should Track

```python
{
    "transcription_quality": {
        "avg_confidence": 0.0,
        "low_confidence_segment_rate": 0.0,
        "avg_logprob": 0.0,
        
        "repair_stats": {
            "mojibake_detected": 0,
            "mojibake_repaired": 0,
            "suspicious_text_detected": 0,
            "correction_pass_applied": 0
        },
        
        "language_consistency": {
            "avg_consistency_score": 0.0,
            "mixed_language_segments": 0
        },
        
        "timing_quality": {
            "avg_word_timestamp_confidence": 0.0,
            "gap_fill_events": 0,
            "timeline_adjustment_ms": 0.0
        }
    }
}
```

---

## ✅ CONCLUSIONS

### Why Subtitles Are Inaccurate

**Root Causes:**
1. **Poor audio quality** (not fixable by pipeline)
2. **Whisper hallucinations** (detection exists, enforcement unclear)
3. **Language misdetection** (can be mitigated with hints)
4. **Low confidence kept** (no rejection threshold visible)

### Why Timing Is Off

**Root Causes:**
1. **Whisper word timestamp errors** (inherent model limitation)
2. **Silence filtering side effects** (timeline adjustments)
3. **Stabilization complexity** (may introduce errors while fixing others)

### Primary Bottleneck

**WHISPER MODEL PERFORMANCE** is the PRIMARY quality bottleneck.

Pipeline has repair/stabilization logic, but can't overcome poor input quality.

**Validation needed:** Runtime metrics for confidence, repair success rates, timing accuracy.

---

**End of Subtitle Quality Audit**
