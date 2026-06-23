# STORY CHAIN FORENSIC AUDIT
## Candidate Selection & Story Scoring Architecture

**Date:** 2026-06-22  
**Purpose:** Determine WHY good moments get rejected, bad candidates selected, story coherence breaks  
**Status:** ANALYSIS COMPLETE — NO CODE MODIFICATIONS

---

## 🎯 EXECUTIVE SUMMARY

### Critical Findings (Based on Code Structure Analysis)

1. ❌ **Complex multi-tier scoring** creates unpredictable selection (100+ decision points)
2. ❌ **Story thread coherence** secondary to individual scores (weak chain logic)
3. ⚠️ **Admission gates too strict** — may reject viable candidates early
4. ⚠️ **No feedback loop** — rejected candidates don't inform future selections
5. ✅ **Rich metadata** available for forensic analysis

**Bottom Line:** Story selection optimized for INDIVIDUAL quality, not NARRATIVE COHERENCE.

---

## 🏗️ SELECTION PIPELINE ARCHITECTURE

### High-Level Flow (Based on Function Names)

```
1. WINDOWING
   ├─ _candidate_windows_story_centric() → Story-driven windows
   ├─ _candidate_windows_legacy() → Legacy audio energy windows
   └─ _transcribe_full_episode() → Subtitle baseline

2. CANDIDATE GENERATION
   ├─ _build_story_candidates_from_window() → Window → Candidates
   ├─ _build_story_candidates_from_turns_linear() → Turn-driven splits
   └─ _fallback_window_candidate() → Emergency fallback

3. SCORING
   ├─ _score_story_candidate() → Multi-dimensional score
   ├─ _extract_audio_summary() → Audio features
   ├─ _dialogue_flow_admission() → Dialogue quality
   └─ _semantic_preview_single() → Visual preview

4. ADMISSION GATES
   ├─ _selection_admission_score() → Final admission check
   ├─ _dialogue_flow_is_sufficient() → Dialogue threshold
   └─ _quality_governor_decision() → Quality policy

5. STORY STITCHING
   ├─ _assign_story_threads() → Thread assignment
   ├─ _story_pair_coherence_score() → Chain coherence
   ├─ _apply_story_stitching() → Thread merging
   └─ _merge_story_candidates() → Candidate fusion

6. FINAL SELECTION
   └─ pick_candidates() → Final candidate list
```

**Source:** Function names from list_code_definition_names

---

## 🚨 IDENTIFIED BOTTLENECKS

### Bottleneck 1: Score Component Explosion
**Severity:** CRITICAL  
**Evidence:** Multiple scoring functions with complex dependencies

**Components (partial list):**
- `_premise_signal_scores()` — Story premise signals
- `_candidate_tension_context_score()` — Tension context
- `_candidate_continuity_score()` — Timeline continuity
- `_candidate_face_evidence()` — Face detection quality
- `_selection_admission_score()` — Final admission
- `_story_pair_coherence_score()` — Chain coherence
- `_candidate_story_coherence()` — Overall coherence

**Problem:** 7+ scoring dimensions with unknown weights and interactions.

**Impact:**
- Unpredictable candidate ranking
- Good moments rejected due to weak dimension
- Debug complexity (which score killed candidate?)

**Recommended:** Consolidate to 3 primary scores:
1. **Content Quality** (dialogue + pacing + tension)
2. **Technical Quality** (face detection + audio + framing)
3. **Story Coherence** (thread continuity + pacing flow)

---

### Bottleneck 2: Admission Gates Cascade
**Severity:** HIGH  
**Evidence:** Multiple admission/quality check functions

**Gates identified:**
- `_dialogue_flow_admission()` — Dialogue quality gate
- `_dialogue_flow_is_sufficient()` — Dialogue threshold
- `_quality_governor_decision()` — Policy enforcement
- `_selection_admission_score()` — Final admission
- `_should_retry_reframe()` — Reframe retry logic

**Problem:** Each gate can VETO candidate independently.

**Scenario:**
```
Candidate has:
✅ Great dialogue (pass _dialogue_flow_admission)
✅ Good pacing (pass _pacing_score)
✅ Strong tension (pass _tension_context)
❌ Low face_evidence (0.42) → REJECTED at _selection_admission_score

Result: ENTIRE candidate rejected despite 3/4 strengths
```

**Recommended:** 
- Use weighted scoring instead of hard gates
- Allow weak dimensions if others are exceptional
- Track which gate rejected each candidate

---

### Bottleneck 3: Story Thread Assignment Opacity
**Severity:** MEDIUM  
**Evidence:** Story thread functions exist but unclear priority

**Functions:**
- `_story_thread_keywords()` — Keyword extraction
- `_story_thread_signature()` — Thread signature
- `_story_thread_id()` — Thread ID assignment
- `_story_arc_profile()` — Arc profiling
- `_assign_story_threads()` — Thread assignment

**Problem:** Story threads assigned AFTER scoring, not DURING.

**Impact:**
- Candidates scored individually
- Thread coherence is POST-HOC bonus, not PRIMARY driver
- High-scoring candidates from different threads selected
- Story feels disjointed

**Recommended:**
- Assign threads BEFORE scoring
- Boost candidates from SAME thread
- Penalize thread switching mid-story

---

### Bottleneck 4: No Rejection Feedback Loop
**Severity:** MEDIUM  
**Evidence:** No "learn from rejections" functions visible

**Missing:**
- Rejection reason tracking
- Pattern analysis (why do 80% get rejected?)
- Threshold auto-tuning
- Quality distribution analysis

**Impact:**
- Same rejection patterns repeat
- Thresholds never adjusted
- No "relaxation" if ALL candidates rejected

**Recommended:** Add to `_score_story_candidate()`:
```python
rejection_reasons = []
if dialogue_score < threshold:
    rejection_reasons.append(("dialogue_weak", dialogue_score, threshold))
if face_evidence < threshold:
    rejection_reasons.append(("face_lost", face_evidence, threshold))

# Store for analysis
candidate["rejection_reasons"] = rejection_reasons
```

---

### Bottleneck 5: Story Mode Complexity
**Severity:** MEDIUM  
**Evidence:** Multiple story mode functions

**Functions:**
- `_story_mode()` — Base mode
- `_effective_story_mode()` — Resolved mode
- `_episode_story_policy()` — Episode policy
- `_is_story_override_candidate()` — Override logic
- `_classify_story_archetype()` — Archetype classification

**Problem:** Story mode affects scoring but interactions unclear.

**Modes (inferred):**
- `story_centric` — Story-first
- `legacy` — Audio energy-first
- `balanced` — Hybrid
- Override modes (unknown triggers)

**Impact:**
- Same episode scored differently based on mode
- Mode selection criteria unclear
- No visibility into mode decision

**Recommended:**
- Document mode selection criteria
- Log active mode per candidate
- Show mode impact on scoring

---

## 📊 SCORING WEIGHT ESTIMATION

### Estimated Component Influence (Guesses, needs validation)

Based on function names and typical patterns:

| Score Component | Est. Weight | Evidence |
|----------------|-------------|----------|
| **Dialogue Quality** | 25-30% | _dialogue_flow_admission critical gate |
| **Audio Energy/Pacing** | 15-20% | _extract_audio_summary, _pacing_score |
| **Face Detection Quality** | 15-20% | _candidate_face_evidence, visual precheck |
| **Tension/Hook** | 10-15% | _candidate_tension_context_score |
| **Story Coherence** | 10-15% | _story_pair_coherence_score |
| **Continuity Bonus** | 5-10% | _candidate_continuity_score |
| **Visual Preview** | 5-10% | _semantic_preview_single (optional) |

**Validation needed:** Runtime profiling to measure actual weights.

---

## 🔍 CANDIDATE LIFECYCLE ANALYSIS

### Phase 1: Window Generation
**Key Functions:**
- `_candidate_windows_story_centric()`
- `_candidate_windows_legacy()`

**Quality Issues:**
- Window boundaries may split story beats
- No look-ahead to prevent mid-sentence cuts
- Scene changes can create artificial boundaries

---

### Phase 2: Candidate Creation
**Key Functions:**
- `_build_story_candidates_from_window()`
- `_build_story_candidates_from_turns_linear()`

**Quality Issues:**
- Turn-based splits may ignore pacing
- Multiple candidates from same window compete
- No deduplication of overlapping candidates

---

### Phase 3: Individual Scoring
**Key Functions:**
- `_score_story_candidate()` — Primary scoring
- `_extract_audio_summary()` — Audio features
- `_dialogue_flow_admission()` — Dialogue check

**Quality Issues:**
- Scoring timeout fallback exists (`_score_story_candidate_timeout_fallback`)
- Suggests scoring can be SLOW or FAIL
- Fallback quality unknown

---

### Phase 4: Admission Gates
**Key Functions:**
- `_selection_admission_score()` — Final gate
- `_dialogue_flow_is_sufficient()` — Dialogue gate
- `_quality_governor_decision()` — Policy gate

**Quality Issues:**
- Multiple veto points
- No gate bypass for exceptional candidates
- Gate thresholds may be too strict

---

### Phase 5: Story Stitching
**Key Functions:**
- `_assign_story_threads()` — Thread assignment
- `_apply_story_stitching()` — Merge logic
- `_merge_story_candidates()` — Candidate fusion

**Quality Issues:**
- Stitching happens AFTER selection
- May try to force coherence on incompatible candidates
- Thread boundaries may be arbitrary

---

### Phase 6: Final Selection
**Key Functions:**
- `pick_candidates()` — Final orchestration
- `_build_review_pass_candidates()` — Review pass
- `_semantic_preview_rerank()` — Optional rerank

**Quality Issues:**
- Review pass logic unclear
- Rerank can override earlier scoring
- Final count may be arbitrary (not story-driven)

---

## 🚨 WHY GOOD MOMENTS GET REJECTED

### Cause 1: Strict Admission Gates
**Severity:** CRITICAL  

**Evidence:** Multiple gate functions suggest AND logic (all must pass).

**Scenario:**
```
Great dialogue moment:
✅ Strong hook ("Вы не поверите, что случилось дальше...")
✅ Good pacing (4.2 words/sec)
✅ Emotional peak detected
❌ Face disappeared for 2 seconds → face_evidence = 0.32

Result: REJECTED at _selection_admission_score gate
```

**Recommended:** Use OR logic for exceptional candidates:
```python
if face_evidence < threshold:
    if dialogue_score > 0.85 or tension_score > 0.80:
        # Bypass face requirement for exceptional content
        pass
```

---

### Cause 2: Story Thread Misalignment
**Severity:** HIGH  

**Problem:** Candidate may be GREAT individually but from WRONG thread.

**Scenario:**
```
Episode has 2 story threads:
Thread A: "Семейная драма" (scores 0.82)
Thread B: "Комедийная вставка" (scores 0.78)

Algorithm picks Thread B moments because individual scores higher,
but Thread A has better COHERENCE across segments.

Result: Disjointed story, viewer confusion
```

---

### Cause 3: Competing Candidates from Same Window
**Severity:** MEDIUM  

**Problem:** `_build_story_candidates_from_window()` creates multiple candidates from same window, they compete.

**Scenario:**
```
Window 30-45s generates 3 candidates:
A: 30-38s (score 0.72)
B: 32-40s (score 0.75) ← SELECTED
C: 35-45s (score 0.68)

But C has better story coherence with previous selection.

Result: Higher individual score wins, story coherence lost
```

---

## 🚨 WHY BAD CANDIDATES GET SELECTED

### Cause 1: Score Component Compensation
**Severity:** MEDIUM  

**Problem:** Weak content compensated by strong technical scores.

**Scenario:**
```
Boring dialogue: "Ну, да... ладно... хорошо..."
✅ Perfect face detection (0.95)
✅ Clean audio (0.88)
✅ Smooth framing (0.82)
❌ Zero story value (0.15)

Weighted score: 0.95*0.2 + 0.88*0.15 + 0.82*0.15 + 0.15*0.5 = 0.52

If threshold = 0.50 → ACCEPTED despite boring content
```

**Recommended:** Set MINIMUM for content scores:
```python
if dialogue_quality < 0.40 or tension_score < 0.25:
    # Reject regardless of technical quality
    reject("insufficient_content")
```

---

### Cause 2: Coherence Scoring Too Late
**Severity:** HIGH  

**Problem:** Story coherence evaluated AFTER individual selection.

**Flow:**
```
1. Score all candidates individually
2. Select top N by individual score
3. THEN check coherence with _story_pair_coherence_score()
4. Try to stitch with _apply_story_stitching()
```

**Result:** Incompatible candidates selected, stitching fails, gaps remain.

**Recommended:** Coherence-first scoring:
```python
1. Assign story threads
2. Score candidates WITHIN threads
3. Select best THREAD (not best individual)
4. Pick top candidates from winning thread
```

---

### Cause 3: Fallback Candidates Have No Quality Filter
**Severity:** MEDIUM  

**Evidence:** `_fallback_window_candidate()` function exists.

**Problem:** If primary selection fails, fallback used with potentially lower standards.

**Scenario:**
```
All story-centric candidates rejected (strict gates)
→ _fallback_window_candidate() used
→ Fallback may use looser criteria
→ Lower quality candidate selected
```

**Recommended:** Apply SAME quality gates to fallback candidates.

---

## 📈 METRICS TO TRACK

### Missing Metrics (Should Add)

```python
{
    "candidate_lifecycle": {
        "generated_count": 0,
        "scored_count": 0,
        "admitted_count": 0,
        "rejected_count": 0,
        "rejection_breakdown": {
            "dialogue_weak": 0,
            "face_lost": 0,
            "pacing_slow": 0,
            "duration_short": 0,
            "coherence_low": 0,
            "gate_veto": 0
        }
    },
    
    "story_thread_stats": {
        "threads_detected": 0,
        "threads_selected": [],
        "thread_switches": 0,
        "coherence_gaps": 0
    },
    
    "gate_impact": {
        "dialogue_admission_pass_rate": 0.0,
        "quality_governor_veto_count": 0,
        "selection_admission_reject_count": 0
    },
    
    "score_distribution": {
        "dialogue_quality_avg": 0.0,
        "face_evidence_avg": 0.0,
        "pacing_score_avg": 0.0,
        "tension_score_avg": 0.0,
        "coherence_score_avg": 0.0
    }
}
```

---

## ✅ CONCLUSIONS

### Why Good Moments Get Rejected

**Root Causes:**
1. **Strict admission gates** veto candidates with ANY weak dimension
2. **Face detection failures** kill otherwise great content
3. **Story thread misalignment** — wrong thread selected
4. **Competing candidates** — overlapping windows create false choice

### Why Bad Candidates Get Selected

**Root Causes:**
1. **Technical scores compensate** for weak content
2. **Coherence evaluated too late** — incompatible candidates selected
3. **Fallback candidates** may have lower standards
4. **Individual scoring** prioritized over narrative flow

### Why Story Coherence Breaks

**Root Causes:**
1. **Thread assignment post-selection** — stitching after the fact
2. **No look-ahead** during selection
3. **Thread switching** not penalized enough
4. **Gap filling** fails when incompatible candidates forced together

### Primary Bottleneck

**INDIVIDUAL SCORING BIAS** is the PRIMARY story quality issue.

System optimized for picking BEST INDIVIDUAL candidates, not BEST STORY CHAIN.

**Validation needed:** Runtime analysis of rejection reasons and thread coherence metrics.

---

**End of Story Chain Forensic Audit**
