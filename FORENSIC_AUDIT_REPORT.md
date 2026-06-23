# FORENSIC PIPELINE AUDIT REPORT
**Date**: 2026-06-18 02:06 AM MSK  
**Auditor**: Staff-Level Software Architect  
**Scope**: Multimodal Pipeline Signal Integrity (audio + video + subtitle + speaker + story)

---

## 🔴 EXECUTIVE SUMMARY

**CRITICAL VIOLATIONS FOUND: 5**

Pipeline suffering from **ARTIFICIAL DATA INJECTION** and **SIGNAL CORRUPTION**.  
Root causes identified. Symptomatic hacks must be **IMMEDIATELY DELETED**.

---

## 📐 ARCHITECTURE MAP (ACTUAL STATE)

### LAYER 1: TRANSCRIPT (SOURCE OF TRUTH)
**Authority**: `transcribe_segment()` (subtitle.py, line 60)  
**Called at**: lines 2872, 2899, 2948, 5333, 5361  
**Output**: `subtitle_info` dict with segments `[{start, end, text, speaker, words}]`  
**Status**: ✅ **CLEAN** — timing originates here correctly

---

### LAYER 2: SPEAKER TURN (DIALOGUE AUTHORITY)
**Authority**: `extract_dialogue_turns()` (montage/dialogue_parser.py:30-101)  
**Input**: subtitle_segments from Layer 1  
**Output**: turns `[{turn_id, start, end, speaker, text, turn_type}]`  
**Status**: ✅ **CLEAN** — turn-first logic, speaker from segments NOT face

**Face Detection Role**: `sample_face_focus_stats()` at lines 2079, 2254, 6075  
→ **SECONDARY SCORING ONLY** (not primary signal) ✅ **CORRECT**

---

### LAYER 3: STORY SEGMENTATION
**Functions**:
- `_build_story_candidates_from_turns_linear()` (line 5824)
- `_build_story_candidates_from_window()` (line 5669)
- `build_story_plan()` from montage/story_builder (line 48)

**Status**: ⚠️ **FALLBACK CONTAMINATION DETECTED**

---

### LAYER 4: HIGHLIGHT SELECTION (18 GATES)
**Function**: `pick_candidates()` (line 8350)  
**Admission**: `_dialogue_flow_admission()` (line 3845)  
**Status**: 🔴 **CORRUPTED BY ARTIFICIAL INJECTION**

---

### LAYER 5: SCORING SYSTEM
**Primary**: `_score_story_candidate()` (line 6046)  
**Fallback**: `_score_story_candidate_timeout_fallback()` (line 3353)  
**Visual Precheck**: `_ranking_visual_precheck()` (line 2061)  
**Status**: ⚠️ **TIMEOUT FALLBACKS EVERYWHERE** (lines 8667-8866)

---

### LAYER 6: SUBTITLE SYNC
**Remap**: `remap_subtitle_info_after_cuts()` (line 10410)  
**Status**: 🔴 **DESYNC ROOT CAUSE** — called AFTER timeline mutation

---

## 🚨 TOP 5 ROOT CAUSES (CRITICAL)

### #1 ARTIFICIAL CANDIDATE GENERATION (RULE A VIOLATION)
**Location**: `highlight.py:8419-8433`  
**Code**:
```python
fallback = {
    "start": window_start,
    "end": window_end,
    "duration": window_end - window_start,
    "source": source,
    "fallback_reason": "insufficient_context_minimal_candidate",
    "score": 0.35,  # ← FAKE SCORE
    "score_breakdown": {
        "story_clarity_score": 0.30,
        "story_completeness_score": 0.25,
        "speech_density": 0.40,
    }
}
```
**Impact**: Creates candidates from NOTHING when `_fallback_window_candidate()` returns None  
**Verdict**: ❌ **DELETE IMMEDIATELY** (RULE A: NO ARTIFICIAL DATA)

---

### #2 MINIMUM CANDIDATE GUARANTEE (OUTPUT INFLATION HACK)
**Location**: `highlight.py` (found via search)  
**Code**:
```python
# PHASE 1 FIX 1.5: Guarantee minimum candidate count per episode
minimum_candidate_count = 12
if len(picked) < minimum_candidate_count and ranked:
    needed = minimum_candidate_count - len(picked)
    # ... force-accept rejected candidates
```
**Impact**: Forces acceptance of rejected candidates to hit quota  
**Verdict**: ❌ **DELETE IMMEDIATELY** (RULE A: NO FORCED TOP-UP)

---

### #3 SUBTITLE DESYNC (TIMELINE MUTATION)
**Location**: `highlight.py:10410`  
**Root Cause**: `remap_subtitle_info_after_cuts()` called AFTER `pause_removed_segments`  
**Flow**:
1. transcribe_segment → creates subtitle_info with original timestamps
2. trim_silence_in_candidate_ms → mutates timeline (removes pauses)
3. remap_subtitle → tries to sync subtitles to mutated timeline
4. **DESYNC**: downstream subtitle renderer uses remapped timestamps that don't match final video

**Verdict**: 🔴 **ARCHITECTURAL FAILURE** — downstream layer mutating upstream signal  
**Fix Required**: REWRITE timeline surgery OR hard-lock subtitle timestamps to original transcript

---

### #4 FALLBACK CASCADE CONTAMINATION
**Locations**:
- `_fallback_window_candidate()` (line 5987)
- `_score_story_candidate_timeout_fallback()` (line 3353)
- Timeout fallbacks at lines 8667-8866 (deferred candidates, watchdog bypass)

**Impact**: Multiple fallback layers creating synthetic recovery paths  
**Verdict**: ⚠️ **AUDIT REQUIRED** — some may be legitimate timeouts, others are artificial generation

---

### #5 HIDDEN SCORING BYPASS LOGIC
**Locations**:
- Lines 8691-8713: `fast_fallback_first` mode bypasses normal scoring
- Lines 8743-8769: timeout fallback accepts candidates without full scoring
- Lines 8826-8866: deferred candidates retried with fallback scoring

**Impact**: Candidates bypass gates through timeout/fallback paths  
**Verdict**: ⚠️ **REVIEW & RECALIBRATE** — legitimate performance optimization or hidden bypass?

---

## 🗑️ DELETE LIST (IMMEDIATE)

### PRIORITY 1 (RULE A VIOLATIONS)
1. **Lines 8419-8433**: Artificial candidate generation from insufficient_context
2. **Minimum candidate guarantee logic** (search results show presence, exact line TBD)
3. **All "needed = minimum_candidate_count" forced top-up logic**

### PRIORITY 2 (HIDDEN BYPASSES)
4. Any logic converting `reject → accept` without re-scoring
5. Any `fallback_candidate` creation without real transcript support

---

## ✏️ REWRITE LIST (MINIMAL SURGICAL)

### REWRITE #1: SUBTITLE SYNC INTEGRITY
**File**: `highlight.py:10410` context  
**Current**: remap_subtitle AFTER silence cuts  
**Fix Option A**: Hard-lock subtitles to original transcript timestamps (ignore cuts)  
**Fix Option B**: Rewrite timeline surgery to preserve subtitle integrity  
**Recommendation**: **Option A** (simpler, preserves source of truth)

### REWRITE #2: FALLBACK_WINDOW_CANDIDATE
**File**: `highlight.py:5987`  
**Current**: Returns candidate or None  
**Issue**: Callers inject artificial data when None  
**Fix**: Ensure returns None → STOP (no artificial generation downstream)

### REWRITE #3: STORY CANDIDATE BUILDERS
**Files**: Lines 5669, 5824  
**Audit**: Do these create segments without transcript support?  
**Fix**: Ensure ALL segments trace back to real dialogue turns

---

## 🔒 DO NOT TOUCH (CRITICAL STABILITY)

1. **transcribe_segment()** — SOURCE OF TRUTH ✅
2. **extract_dialogue_turns()** — SPEAKER AUTHORITY ✅
3. **sample_face_focus_stats()** — correctly secondary ✅
4. **Core scoring logic** (line 6046) — until recalibration complete
5. **Subtitle rendering** (subtitle.py) — input signal should be fixed upstream

---

## 📊 OUTPUT RATE ANALYSIS

**Question**: Why is output rate low (or inflated)?  
**Answer**: **D + E** — Pipeline starvation + Signal corruption

**Evidence**:
- Artificial candidate generation (lines 8419-8433) → signal corruption
- Minimum guarantee hack → inflation to mask starvation
- Real issue: insufficient real candidates passing gates
- Forcing acceptance masks root problem: **gates may be too strict OR segmentation broken**

**Recommendation**:
1. DELETE artificial injection
2. Let pipeline produce natural output (may be low initially)
3. THEN analyze: are gates over-filtering OR is segmentation starved?
4. Fix root cause, NOT symptoms

---

## 🎯 SAFE NEXT STEP PLAN (3-7 DAYS)

### DAY 1-2: SURGICAL DELETION
- [ ] Remove lines 8419-8433 (artificial candidate)
- [ ] Remove minimum_candidate_count guarantee
- [ ] Remove all forced top-up logic
- [ ] Run test: measure natural output rate

### DAY 3-4: SUBTITLE INTEGRITY FIX
- [ ] Option A: Hard-lock subtitles to transcript timestamps
- [ ] Test: verify subtitle sync after silence cuts
- [ ] Validate: no desync in final renders

### DAY 5-6: FALLBACK AUDIT
- [ ] Classify each fallback: legitimate timeout vs artificial generation
- [ ] Keep: performance timeouts with real candidates
- [ ] Delete: any fallback creating synthetic data
- [ ] Recalibrate: scoring thresholds if needed

### DAY 7: VALIDATION
- [ ] Run full episode test
- [ ] Measure: candidate quality (not quantity)
- [ ] Verify: no artificial inflation
- [ ] Confirm: stable speaker tracking
- [ ] Check: subtitle temporal consistency

---

## ✅ SUCCESS CRITERIA

After fixes:
1. ✅ Pipeline produces **fewer but higher quality** candidates
2. ✅ **No artificial inflation**
3. ✅ **Stable speaker tracking** (turn-first maintained)
4. ✅ **Subtitles remain temporally consistent** (no desync)
5. ✅ **Story chains fully trace real transcript structure**
6. ✅ **No hidden bypass logic**

---

## 🔬 SIGNAL HIERARCHY (CONFIRMED)

```
TRANSCRIPT (transcribe_segment)
    ↓
SPEAKER TURNS (extract_dialogue_turns) ← SOURCE: subtitles NOT face
    ↓
STORY SEGMENTS (story_builder)
    ↓
HIGHLIGHT CANDIDATES (pick_candidates)
    ↓
SCORING (with face as SECONDARY boost)
    ↓
SUBTITLE REMAP ← ⚠️ BREAKS HERE (timeline mutation)
    ↓
FINAL SHORT
```

**Face Detection**: Secondary scoring boost ONLY ✅  
**Primary Signal**: Dialogue turns from transcript ✅

---

## 🚫 FINAL WARNING

Any proposal containing:
- "add minimum guarantee"
- "add fallback candidate"
- "soft bypass rejection"
- "increase candidate count"

→ **AUTOMATIC ARCHITECTURAL FAILURE**

Fix root causes, NOT symptoms.

---

**END OF REPORT**
