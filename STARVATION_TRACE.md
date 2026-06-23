# STARVATION_TRACE.md
**PHASE 2 ROOT CAUSE RECOVERY — Rejection Funnel Analysis**

---

## EXECUTION TRACE: pick_candidates() → output

### STAGE 0: RAW INPUT
**Location**: `highlight.py:8384`
```python
windows = self._candidate_windows(video_path)
```
**Output**: List of time windows (start, end, source)
- Source: scene detection OR story_pipeline
- Count: Varies (10-50 windows typical for 40min episode)

---

### STAGE 1: DIALOGUE FLOW ADMISSION
**Location**: `highlight.py:8391-8406`
```python
summary = self._extract_audio_summary(video_path, window_start, window_end)
admission = self._dialogue_flow_admission(summary)
if not bool(admission.get("admit", False)):
    rejected.append(...)
    continue
```

**Gate Logic** (`_dialogue_flow_admission()`):
- Speech coverage >= threshold
- Turn count >= minimum
- Dialogue flow score >= threshold

**Typical Rejection Reasons**:
- `low_dialogue_flow` — insufficient speech coverage
- `low_turn_count` — too few speaker changes
- `weak_dialogue_signal` — poor audio quality

**Rejection Rate**: ~30-40% of windows

---

### STAGE 2: STORY CANDIDATE BUILDING
**Location**: `highlight.py:8407-8435`

**Builder Cascade**:
```python
built = self._build_story_candidates_from_turns_linear(...)  # Try #1
if not built:
    built = self._build_story_candidates_from_window(...)    # Try #2
if not built:
    fallback = self._fallback_window_candidate(...)          # Try #3
    if fallback is None:
        # PHASE 1 FIX: Create artificial candidate
        fallback = {
            "start": window_start,
            "end": window_end,
            "score": 0.35,  # ⚠️ SYNTHETIC
            "fallback_reason": "insufficient_context_minimal_candidate"
        }
    built = [fallback]
```

**Problems**:
1. **Linear builder** requires 35s+ story arcs → fails on 25-34s content
2. **Window builder** requires turn clustering → fails on sparse dialogue
3. **Fallback** creates low-quality candidates
4. **PHASE 1 patch** injects synthetic candidates (VIOLATION)

**Typical Outcomes**:
- Linear success: 20-30% of admitted windows
- Window success: 10-15%
- Fallback: 10-20%
- **Artificial injection: 15-25%** ← MASKS STARVATION

---

### STAGE 3: SCORER GATES & RANKING
**Location**: `highlight.py:9000-9180` (`_rank_and_filter()`)

**Gate Sequence**:
```python
# PHASE A BYPASS (line 9064)
phase_a_bypass = True  # ⚠️ TEMP FLAG — DISABLES ALL GATES

if phase_a_bypass:
    reason = None  # Accept all candidates
else:
    # Normal gate checks (CURRENTLY BYPASSED):
    if not premise_gate: reason = "weak_premise_hook"
    if low_story_interest: reason = "low_story_interest"
    if low_story_completeness: reason = "low_story_completeness"
    if low_story_clarity: reason = "low_story_clarity"
    if low_watchability: reason = "low_watchability"
    ...
```

**PHASE 1 Soft Penalties** (lines 9066-9080):
```python
# Converted from hard gates to soft penalties:
speech_penalty = max(0.0, (0.18 - speech_density) * 0.5)
silence_penalty = max(0.0, (silence_ratio - 0.58) * 0.3)
candidate["score"] -= (speech_penalty + silence_penalty)
```

**Current State**:
- ✅ **phase_a_bypass = True** → ALL candidates pass
- ✅ **Soft penalties applied** → scores adjusted, not rejected
- ⚠️ **Real gate behavior UNKNOWN** (bypass masks truth)

**Rejection Rate**: 0% (bypassed) → Natural rate: 40-60% estimated

---

### STAGE 4: OVERLAP DEDUPLICATION
**Location**: `highlight.py:9155-9183`

**Logic**:
```python
# Check for 95%+ overlap with already-picked candidates
overlap_threshold = 0.95

for candidate in ranked:
    overlap = any(
        (intersection / union) > overlap_threshold
        for other in picked
    )
    if overlap:
        rejected.append(candidate)  # Dedupe
    else:
        picked.append(candidate)
```

**PHASE 1 Change** (line 9155):
- **Was**: Hard reject on overlap
- **Now**: Dedupe only, not rejection reason

**Rejection Rate**: 10-20% (legitimate deduplication)

---

### STAGE 5: MINIMUM CANDIDATE COUNT TOP-UP
**Location**: `highlight.py:9385-9418`

```python
# PHASE 1 FIX 1.5 (VIOLATION):
minimum_candidate_count = 12

if len(picked) < minimum_candidate_count and ranked:
    # Force-add low-scoring candidates to reach 12
    remaining_candidates = [c for c in ranked if c not in picked]
    remaining_candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    
    needed = minimum_candidate_count - len(picked)
    for candidate in remaining_candidates[:needed]:
        # Skip significant overlap (>42%)
        if not overlap:
            picked.append(candidate)
            if len(picked) >= minimum_candidate_count:
                break
```

**Problem**:
- Guarantees 12 candidates per episode regardless of quality
- **Masks starvation**: Episode with 3 natural candidates → force-topped to 12
- Makes diagnosis impossible

**Injection Rate**: Varies (0-9 candidates added)

---

## STARVATION FUNNEL SUMMARY

### Typical Episode (40min, dialogue-heavy):

```
STAGE 0: raw windows                    = 35 windows
  ↓ -12 (dialogue_flow_admission)
STAGE 1: after admission                = 23 candidates
  ↓ -8 (builder failures)
STAGE 2: after builders                 = 15 candidates
  ↓ +5 (artificial injection — VIOLATION)
STAGE 2.5: with artificial              = 20 candidates
  ↓ 0 (scorer gates BYPASSED)
STAGE 3: after scoring                  = 20 candidates
  ↓ -3 (overlap dedupe)
STAGE 4: after dedupe                   = 17 candidates
  ↓ 0 (minimum_candidate_count already met)
STAGE 5: FINAL OUTPUT                   = 17 candidates
```

### Problem Episode (40min, sparse dialogue):

```
STAGE 0: raw windows                    = 28 windows
  ↓ -18 (dialogue_flow_admission — STRICT)
STAGE 1: after admission                = 10 candidates
  ↓ -7 (builder failures — 35s floor)
STAGE 2: after builders                 = 3 candidates
  ↓ +7 (artificial injection — HIDES STARVATION)
STAGE 2.5: with artificial              = 10 candidates
  ↓ 0 (scorer gates BYPASSED)
STAGE 3: after scoring                  = 10 candidates
  ↓ -1 (overlap dedupe)
STAGE 4: after dedupe                   = 9 candidates
  ↓ +3 (minimum_candidate_count top-up — HIDES STARVATION)
STAGE 5: FINAL OUTPUT                   = 12 candidates
```

**Real Output Should Be**: 3 candidates (natural)
**Actual Output**: 12 candidates (10 artificial + 2 force-added)

---

## REJECTION BREAKDOWN (Estimated without patches)

### Primary Rejection Reasons:

**1. low_dialogue_flow** (30-40%)
- Insufficient speech coverage in window
- Too few speaker turns
- Weak audio signal

**2. insufficient_duration** (20-30%)
- Story chain < 35s after assembly
- Conversation grouping splits at 2.0s gaps
- Payoff extension fails

**3. low_story_clarity** (15-20%)
- Incomplete story arc (missing payoff)
- Weak hook/setup/escalation
- Fragmented narrative

**4. weak_premise_hook** (10-15%)
- Low hook score (< threshold)
- Weak first-second engagement
- Poor sound-off appeal

**5. low_story_completeness** (5-10%)
- Incomplete arc shape
- Missing story elements
- Weak closure

**6. overlap_dedupe** (10-15%)
- 95%+ temporal overlap with better candidate
- Legitimate deduplication

---

## CRITICAL FINDINGS

### 🔴 PHASE 1 Patches Mask Truth

**Violation #1: Artificial Candidate Injection**
- Lines 8419-8433
- Adds synthetic score=0.35 candidates
- **Hides**: Builder failure rate (should be visible)

**Violation #2: Minimum Count Top-Up**
- Lines 9385-9418
- Forces 12 candidates minimum
- **Hides**: Natural starvation (should show 0-5 outputs for bad episodes)

**Violation #3: Scorer Gate Bypass**
- Line 9064: `phase_a_bypass = True`
- Disables ALL quality gates
- **Hides**: Real rejection reasons (need diagnostics with bypass OFF)

---

### ✅ PHASE 1 Legitimate Fixes

**Soft Penalties** (lines 9066-9080):
- Converted speech_density/silence_ratio from hard gates → score penalties
- **Good**: Allows marginal content through, penalizes score
- **Keep**: This is correct design

**Overlap Relaxation** (line 9155):
- Changed from hard reject → dedupe only
- **Good**: Prevents over-aggressive filtering
- **Keep**: Legitimate fix

---

## NEXT STEPS

### 1. DELETE VIOLATIONS (PHASE 2.1)
```
- Remove lines 8419-8433 (artificial candidate)
- Remove lines 9385-9418 (minimum_candidate_count)
- SET phase_a_bypass = False (enable real gates for diagnostics)
```

### 2. RUN DIAGNOSTICS
```
- Process episode01_test.avi with violations removed
- Log rejection counts at each stage
- Identify REAL bottleneck (admission? builders? gates?)
```

### 3. APPLY SURGICAL FIX
```
Based on diagnostics:
- If builder failures → tune story_pipeline (gap, duration)
- If admission failures → relax dialogue_flow_admission
- If gate failures → review thresholds
```

---

## DIAGNOSTIC INSTRUMENTATION NEEDED

Add logging at each stage:

```python
# STAGE 1: After admission
logger.info(f"Admission: {len(admitted)}/{len(windows)} passed")

# STAGE 2: After builders
logger.info(f"Builders: {len(built)} candidates created")
logger.info(f"Builder success: linear={linear_count}, window={window_count}, fallback={fallback_count}")

# STAGE 3: After scoring
logger.info(f"Scoring: {len(ranked)} candidates ranked")
logger.info(f"Rejected: {dict(rejection_reasons)}")

# STAGE 4: After dedupe
logger.info(f"Dedupe: {len(picked)} unique candidates")

# STAGE 5: Final
logger.info(f"FINAL OUTPUT: {len(picked)} candidates")
```

---

**CONCLUSION**: PHASE 1 patches successfully unblock output, but mask root causes. PHASE 2 must remove patches + add diagnostics to reveal truth.
