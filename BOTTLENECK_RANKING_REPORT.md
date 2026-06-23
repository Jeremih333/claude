# BOTTLENECK RANKING REPORT
## Priority Matrix & Action Plan

**Date:** 2026-06-22  
**Purpose:** Rank all identified bottlenecks by impact × feasibility, provide action priorities  
**Status:** SYNTHESIS COMPLETE

---

## 🎯 EXECUTIVE SUMMARY

### Top 3 Critical Bottlenecks (Fix These First)

1. **Face Detection Performance** — 96% of processing time, 40% rejection rate
2. **Late Quality Gates** — Expensive processing before rejection checks
3. **Confidence Gate Overrides Turns** — Turn-first authority compromised

**Combined Impact:** 80% time reduction + 40% quality improvement possible.

---

## 📊 BOTTLENECK PRIORITY MATRIX

### Scoring Methodology

**Impact Score (0-10):**
- Quality improvement potential
- Time/resource savings
- User experience enhancement

**Feasibility Score (0-10):**
- Implementation complexity
- Risk level
- Required resources

**Priority = Impact × Feasibility**

---

## 🔴 TIER 1: CRITICAL PRIORITY (Impact ≥8, Feasibility ≥7)

### 1. Face Detection Cache Implementation
**Source:** CANDIDATE_ECONOMY_AUDIT.md, FACE_PIPELINE_AUDIT.md  
**Impact:** 10/10 — 40% time savings, affects 96% of processing  
**Feasibility:** 9/10 — Simple dict cache, low risk  
**Priority:** 90

**Problem:**
- Same timestamps detected repeatedly across overlapping candidates
- 1400+ detection calls per episode
- No result reuse

**Solution:**
```python
face_cache = {}
def detect_faces_cached(video_path, timestamp):
    cache_key = f"{video_path}_{int(timestamp)}"
    if cache_key in face_cache:
        return face_cache[cache_key]
    faces = detect_faces(video_path, timestamp)
    face_cache[cache_key] = faces
    return faces
```

**Estimated savings:** 40% face detection time = 38% total time

---

### 2. Early Audio Filtering
**Source:** CANDIDATE_ECONOMY_AUDIT.md  
**Impact:** 9/10 — 30% candidates rejected early, massive time save  
**Feasibility:** 10/10 — Trivial audio checks, no risk  
**Priority:** 90

**Problem:**
- Silent/noisy candidates undergo full processing
- No early rejection based on cheap audio signals
- 30-40% candidates fail quality later

**Solution:**
```python
def quick_audio_filter(video_path, start, end):
    rms = sample_audio_rms(video_path, start, end, count=3)
    if max(rms) < SILENCE_THRESHOLD:
        return False, "silent"
    voice_ratio = quick_vad_check(video_path, start, end)
    if voice_ratio < 0.3:
        return False, "no_speech"
    return True, None
```

**Estimated savings:** 30% time reduction by early rejection

---

### 3. MediaPipe Confidence Thresholds Reduction
**Source:** FACE_PIPELINE_AUDIT.md  
**Impact:** 8/10 — Reduces center crop fallback rate  
**Feasibility:** 10/10 — Simple constant change  
**Priority:** 80

**Problem:**
- Light mode: 0.35/0.28 confidence too high
- Side profiles, poor lighting missed
- Center crop fallback kills turn-first switching

**Solution:**
```python
# Line 14-15 in active_speaker.py
model0_conf = 0.12 if strong else 0.22  # Was 0.18/0.35
model1_conf = 0.10 if strong else 0.20  # Was 0.14/0.28
```

**Estimated impact:** 20-30% reduction in face loss events

---

### 4. Candidate Deduplication
**Source:** CANDIDATE_ECONOMY_AUDIT.md  
**Impact:** 8/10 — 30-40% fewer candidates to process  
**Feasibility:** 9/10 — Simple overlap detection  
**Priority:** 72

**Problem:**
- Overlapping candidates compete (30-38s, 32-40s, 35-45s)
- 75% processing waste
- Only 1 selected

**Solution:**
```python
def deduplicate_candidates(candidates, max_overlap=0.5):
    unique = []
    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        if all(overlap_ratio(c, u) < max_overlap for u in unique):
            unique.append(c)
    return unique
```

**Estimated savings:** 30% processing reduction

---

## 🟠 TIER 2: HIGH PRIORITY (Impact ≥6, Feasibility ≥6)

### 5. Turn-First Authority Enforcement
**Source:** ACTIVE_SPEAKER_AUDIT.md  
**Impact:** 9/10 — Core quality issue, affects story coherence  
**Feasibility:** 6/10 — Requires careful logic changes  
**Priority:** 54

**Problem:**
- `score_margin_ok` gate can block turn switches (Line 1250)
- Low confidence new speaker can't take over
- Turn-first principle compromised

**Solution:**
```python
# Line 1261-1263 in face_crop.py
if subtitle_turn_changed and candidate_role == "speaker":
    should_switch = True
    score_margin_ok = True  # BYPASS confidence gate on turn
    required_hold = 0  # INSTANT switch
```

**Estimated impact:** 30-40% improvement in speaker tracking accuracy

---

### 6. Track Persistence Increase
**Source:** FACE_PIPELINE_AUDIT.md  
**Impact:** 7/10 — Reduces track ID flicker, improves continuity  
**Feasibility:** 10/10 — Single constant change  
**Priority:** 70

**Problem:**
- 5 frames max persistence (1.67s at 3fps)
- Temporary occlusions delete tracks
- Track ID resets lose history

**Solution:**
```python
# Line 340 in active_speaker.py
if track["missed"] > 12:  # Was 5
    active_tracks.pop(track_id, None)
```

**Estimated impact:** 20% reduction in track ID changes

---

### 7. Story Thread Priority Inversion
**Source:** STORY_CHAIN_AUDIT.md  
**Impact:** 8/10 — Fixes story coherence breaks  
**Feasibility:** 5/10 — Requires architectural change  
**Priority:** 40

**Problem:**
- Candidates scored individually
- Thread coherence evaluated POST-HOC
- High individual scores from wrong thread selected

**Solution:**
```python
# Assign threads BEFORE scoring
threads = _assign_story_threads(candidates)

# Score candidates WITHIN threads
for thread in threads:
    thread_candidates = [c for c in candidates if c["thread_id"] == thread["id"]]
    score_candidates(thread_candidates, thread_context=thread)

# Select best THREAD
best_thread = max(threads, key=lambda t: t["coherence_score"])
selected = best_thread["top_candidates"]
```

**Estimated impact:** 40% improvement in story coherence

---

### 8. Admission Gate Relaxation for Exceptional Content
**Source:** STORY_CHAIN_AUDIT.md  
**Impact:** 7/10 — Prevents rejection of great content  
**Feasibility:** 8/10 — Add OR logic to gates  
**Priority:** 56

**Problem:**
- Multiple AND gates reject candidates with ANY weak dimension
- Exceptional dialogue rejected due to weak face_evidence
- 50-60% rejection at admission

**Solution:**
```python
# _selection_admission_score() in highlight.py
if face_evidence < THRESHOLD:
    # Check if content exceptional enough to bypass
    if dialogue_score > 0.85 or tension_score > 0.80:
        bypass_gate("exceptional_content")
        face_evidence = THRESHOLD  # Allow through
```

**Estimated impact:** 15-20% more high-quality candidates admitted

---

## 🟡 TIER 3: MEDIUM PRIORITY (Impact ≥4, Feasibility ≥5)

### 9. Whisper Confidence Threshold Enforcement
**Source:** SUBTITLE_QUALITY_AUDIT.md  
**Impact:** 6/10 — Prevents meaningless subtitles  
**Feasibility:** 9/10 — Simple threshold check  
**Priority:** 54

**Problem:**
- Low confidence transcriptions kept
- No rejection based on avg_logprob
- Meaningless subtitles ("uh... mm... yeah...")

**Solution:**
```python
MIN_SUBTITLE_CONFIDENCE = 0.45
if _subtitle_confidence_from_logprob(avg_logprob) < MIN_SUBTITLE_CONFIDENCE:
    reject_segment("low_confidence")
```

**Estimated impact:** 10-15% subtitle quality improvement

---

### 10. Title Template Pool Expansion
**Source:** TITLE_GENERATION_AUDIT.md  
**Impact:** 6/10 — Reduces title repetition fatigue  
**Feasibility:** 8/10 — Add more template variants  
**Priority:** 48

**Problem:**
- Limited template pool (10-20 variants)
- "Вы не поверите..." repeated every 3rd video
- Viewer fatigue

**Solution:**
```python
# Expand from 20 to 50+ templates per category
HOOK_TEMPLATES = {
    "shock": [
        "Вы не поверите...",
        "Шокирующая правда о...",
        "Невероятное открытие...",
        # Add 47 more variants
    ],
    # Add more hook types
}
```

**Estimated impact:** 30% engagement improvement (needs A/B testing)

---

### 11. Processing Budget Enforcement
**Source:** CANDIDATE_ECONOMY_AUDIT.md  
**Impact:** 5/10 — Predictable processing time  
**Feasibility:** 7/10 — Timeout implementation  
**Priority:** 35

**Problem:**
- No per-candidate time limit
- Episodes can take 40+ minutes
- No budget control

**Solution:**
```python
CANDIDATE_BUDGET = 30  # seconds
with timeout(CANDIDATE_BUDGET):
    process_candidate(candidate)
```

**Estimated impact:** Caps max processing time, no runaway cases

---

### 12. Continuity Bonus Weight Reduction
**Source:** FACE_PIPELINE_AUDIT.md  
**Impact:** 5/10 — Reduces speaker stickiness  
**Feasibility:** 10/10 — Constant change  
**Priority:** 50

**Problem:**
- Current speaker gets +0.55 bonus
- Overly sticky, hard to switch
- Turn boundaries should reduce bonus

**Solution:**
```python
# Line 137 in active_speaker.py
previous_anchor_continuity_bonus = 0.08 if subtitle_turn_changed else 0.12
speaking_score += previous_anchor_continuity_bonus * 0.25  # Was 0.55
```

**Estimated impact:** 15% improvement in speaker switching responsiveness

---

## 🟢 TIER 4: LOW PRIORITY (Nice to Have)

### 13. FFmpeg Call Batching
**Source:** CANDIDATE_ECONOMY_AUDIT.md  
**Impact:** 4/10 — 20-30% encoding time save  
**Feasibility:** 5/10 — Moderate complexity  
**Priority:** 20

**Problem:** 3-4 FFmpeg calls per candidate (crop, subtitle, audio, encode)

**Solution:** Combine operations into single pass

---

### 14. Subtitle Timing Metrics
**Source:** SUBTITLE_QUALITY_AUDIT.md  
**Impact:** 3/10 — Visibility into timing issues  
**Feasibility:** 8/10 — Add metric tracking  
**Priority:** 24

**Problem:** No metrics for subtitle-speech alignment

**Solution:** Track avg offset, max offset, gap fill count

---

### 15. Title Performance Feedback Loop
**Source:** TITLE_GENERATION_AUDIT.md  
**Impact:** 7/10 — Learn which titles work  
**Feasibility:** 3/10 — Needs analytics integration  
**Priority:** 21

**Problem:** No way to learn from title performance

**Solution:** Track CTR per template type (requires external analytics)

---

## 📋 RECOMMENDED ACTION PLAN

### Phase 1: Quick Wins (Week 1) — 60% Time Reduction

**Priority order:**
1. ✅ **Early Audio Filtering** (2 hours) — 30% time save
2. ✅ **Face Detection Cache** (4 hours) — 40% time save  
3. ✅ **MediaPipe Thresholds** (30 min) — Quality improvement
4. ✅ **Candidate Deduplication** (2 hours) — 30% time save

**Combined impact:** 75-80% time reduction, episode processing under 10 minutes

---

### Phase 2: Quality Fixes (Week 2) — 40% Quality Improvement

**Priority order:**
1. ✅ **Turn-First Authority** (6 hours) — Core quality fix
2. ✅ **Track Persistence** (30 min) — Stability improvement
3. ✅ **Admission Gate Relaxation** (4 hours) — Content preservation
4. ✅ **Whisper Confidence** (2 hours) — Subtitle quality

**Combined impact:** 35-40% quality improvement across board

---

### Phase 3: Strategic Improvements (Week 3-4)

**Priority order:**
1. ✅ **Story Thread Priority** (2 days) — Story coherence
2. ✅ **Title Template Expansion** (1 day) — Engagement
3. ✅ **Continuity Bonus** (1 hour) — Speaker tracking
4. ✅ **Processing Budget** (4 hours) — Predictability

**Combined impact:** Long-term quality and performance gains

---

## 📊 EXPECTED RESULTS AFTER ALL FIXES

### Before (Current State):
```
Processing time: 3-48 minutes per episode
Face detection: 96% of time
Quality issues:
  - Faces disappear: FREQUENT
  - Wrong speaker tracked: COMMON
  - Story coherence breaks: COMMON
  - Titles repetitive: HIGH
  - Subtitle errors: MODERATE
```

### After (All Fixes Applied):
```
Processing time: 0.7-10 minutes per episode (75-80% reduction)
Face detection: 40% of time (optimized)
Quality issues:
  - Faces disappear: RARE
  - Wrong speaker tracked: UNCOMMON
  - Story coherence breaks: RARE
  - Titles repetitive: LOW
  - Subtitle errors: LOW
```

**Overall improvement:** 80% faster, 40% better quality

---

## ✅ CRITICAL SUCCESS METRICS

### Track These to Validate Improvements:

```python
{
    "performance_metrics": {
        "avg_episode_processing_time_sec": 0.0,
        "face_detection_time_percent": 0.0,
        "candidate_rejection_rate": 0.0,
        "early_rejection_rate": 0.0,
        "cache_hit_rate": 0.0
    },
    
    "quality_metrics": {
        "face_disappearance_rate": 0.0,
        "track_id_change_rate": 0.0,
        "turn_switch_accuracy": 0.0,
        "story_coherence_score": 0.0,
        "subtitle_confidence_avg": 0.0,
        "center_crop_fallback_rate": 0.0
    },
    
    "engagement_metrics": {
        "title_ctr": 0.0,
        "avg_view_duration_sec": 0.0,
        "completion_rate": 0.0
    }
}
```

---

## 🎯 FINAL RECOMMENDATIONS

### Top 3 Actions (Start Today):

1. **Implement face detection cache** — Biggest time win, trivial implementation
2. **Add early audio filtering** — Massive waste elimination, zero risk
3. **Fix turn-first authority** — Core quality issue, medium complexity

### Validation Strategy:

1. Apply fixes incrementally
2. Measure before/after for each fix
3. Track metrics dashboard
4. Adjust thresholds based on data
5. Iterate on story thread logic

### Success Criteria:

- ✅ Processing time < 10 minutes per episode
- ✅ Face disappearance rate < 5%
- ✅ Turn switch accuracy > 85%
- ✅ Story coherence score > 0.75
- ✅ Subtitle confidence > 0.65

---

**End of Bottleneck Ranking Report**
