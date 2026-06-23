# SAFE FIX PLAN
**Date**: 2026-06-19 01:28 AM MSK  
**Purpose**: Evidence-backed surgical fixes for upstream starvation

---

## 🎯 GUIDING PRINCIPLES

1. **DELETE FIRST**: Remove violations before any fixes
2. **DIAGNOSE SECOND**: Run test with diagnostics to identify root cause
3. **FIX THIRD**: Apply ONLY evidence-backed surgical changes
4. **VALIDATE FOURTH**: Confirm fix resolves root cause without side effects

**NO SPECULATIVE FIXES**.  
**NO MASS RELAXATION OF GATES**.  
**ONLY MINIMAL SURGICAL CHANGES**.

---

## 📋 PHASE 1: ROLLBACK VIOLATIONS (DAYS 1-2)

### TASK 1.1: Delete Artificial Candidate Injection

**Location**: `pipeline/highlight.py:8419-8433`

**Current Code**:
```python
if fallback is None:
    # PHASE 1 FIX: Create minimal candidate instead of rejecting
    # insufficient_context should not hard-block candidate recovery
    fallback = {
        "start": window_start,
        "end": window_end,
        "duration": window_end - window_start,
        "source": source,
        "fallback_reason": "insufficient_context_minimal_candidate",
        "score": 0.35,  # Low but acceptable baseline
        "score_breakdown": {
            "story_clarity_score": 0.30,
            "story_completeness_score": 0.25,
            "speech_density": 0.40,
        }
    }
    built = [fallback]
```

**REPLACE WITH**:
```python
if fallback is None:
    # No valid candidate - reject cleanly
    built = []
```

**Justification**: RULE A violation — creating synthetic candidates from nothing

---

### TASK 1.2: Delete Forced Minimum Count Top-Up

**Location**: TBD (search shows exists, need exact lines)

**Search Pattern**:
```python
minimum_candidate_count = 12
```

**Current Code** (approximate):
```python
# PHASE 1 FIX 1.5: Guarantee minimum candidate count per episode
minimum_candidate_count = 12
if len(picked) < minimum_candidate_count and ranked:
    needed = minimum_candidate_count - len(picked)
    remaining_candidates = [c for c in ranked if c not in picked]
    remaining_candidates.sort(key=lambda x: float(x.get("score", 0.0) or 0.0), reverse=True)
    
    for candidate in remaining_candidates[:needed]:
        overlap = any(...)  # overlap check
        if not overlap:
            picked.append(candidate)
            if len(picked) >= minimum_candidate_count:
                break
```

**REPLACE WITH**:
```python
# Return natural output without forced top-up
return picked, rejected
```

**Justification**: RULE A violation — forcing acceptance of rejected candidates

---

### TASK 1.3: Verify Deletions

**Run**:
```bash
# Check for remaining violations
grep -n "insufficient_context_minimal_candidate" pipeline/highlight.py
grep -n "minimum_candidate_count" pipeline/highlight.py
grep -n "PHASE 1 FIX" pipeline/highlight.py
```

**Expected**: No matches for artificial injection or forced top-up

---

## 📋 PHASE 2: ADD DIAGNOSTIC LOGGING (DAY 3)

### TASK 2.1: Story Pipeline Duration Filter Counter

**Location**: `pipeline/montage/story_pipeline.py:143`

**INSERT AFTER line 143**:
```python
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]

# DIAGNOSTIC LOGGING
print(f"[STARVATION #1] story_pipeline duration filter:")
print(f"  total_chains: {len(extended_chains)}")
print(f"  filtered_chains: {len(filtered)}")
print(f"  min_duration_required: {min_dur}s")
if extended_chains:
    durations = [_chain_duration(c) for c in extended_chains]
    print(f"  chain_durations: {durations}")
print()
```

---

### TASK 2.2: Legacy Linear Builder Counter

**Location**: `pipeline/highlight.py:5828`

**REPLACE**:
```python
if len(turns) < 1 and float(summary.get("speech_density", 0.0) or 0.0) < 0.18:
    return []
```

**WITH**:
```python
if len(turns) < 1 and float(summary.get("speech_density", 0.0) or 0.0) < 0.18:
    print(f"[STARVATION #2] linear_builder rejected: turns={len(turns)}, speech_density={float(summary.get('speech_density', 0.0)):.2f}")
    return []
```

---

### TASK 2.3: Fallback Candidate Counter

**Location**: `pipeline/highlight.py:5993-5996`

**REPLACE**:
```python
if speech_density_value < 0.18 or duration < max(
    35.0, float(self.cfg.get("min_candidate_seconds", 35))
):
    return None
```

**WITH**:
```python
min_dur_fallback = max(35.0, float(self.cfg.get("min_candidate_seconds", 35)))
if speech_density_value < 0.18 or duration < min_dur_fallback:
    print(f"[STARVATION #4] fallback rejected: speech_density={speech_density_value:.2f}, duration={duration:.1f}s, required={min_dur_fallback}s")
    return None
```

---

## 📋 PHASE 3: RUN DIAGNOSTICS (DAY 4)

### TASK 3.1: Run Test Episode

**Command**:
```bash
python main.py --episode episode01_test.avi --config settings.yaml 2>&1 | tee diagnostic_run.log
```

**Monitor**: Look for `[STARVATION #N]` messages

---

### TASK 3.2: Analyze Output

**Count occurrences**:
```bash
grep -c "\[STARVATION #1\]" diagnostic_run.log  # story pipeline
grep -c "\[STARVATION #2\]" diagnostic_run.log  # linear builder
grep -c "\[STARVATION #4\]" diagnostic_run.log  # fallback
```

**Identify bottleneck**:
- Most frequent starvation point = root cause
- Review chain durations / speech density values
- Confirm natural output count (should be low initially)

---

### TASK 3.3: Extract Key Metrics

**From logs, determine**:
1. How many story chains created?
2. How many passed 35s filter?
3. What are actual chain durations?
4. What are actual speech density values?
5. Final candidate count (natural, without inflation)?

---

## 📋 PHASE 4: APPLY EVIDENCE-BACKED FIX (DAYS 5-6)

**CRITICAL**: Only proceed if diagnostics confirm root cause.

### FIX OPTION A: Lower Duration Floor

**IF**: Diagnostics show chains exist but all 25-34s

**Evidence Required**:
```
[STARVATION #1] story_pipeline duration filter:
  total_chains: 8
  filtered_chains: 0
  chain_durations: [28.3, 31.7, 29.1, 26.4, ...]
```

**Apply**:

**File**: `pipeline/montage/story_pipeline.py:142`

**CHANGE**:
```python
min_dur = min(25.0, min_seconds)  # was 35.0
```

**File**: `settings.yaml` OR user cfg

**ADD**:
```yaml
target_story_min_seconds: 25.0
min_candidate_seconds: 25.0
```

**Expected Result**: More chains pass filter, output increases

---

### FIX OPTION B: Relax Conversation Grouping

**IF**: Diagnostics show many turns but small conversation blocks

**Evidence Required**:
```
[STARVATION #1] story_pipeline duration filter:
  total_chains: 15
  filtered_chains: 0
  chain_durations: [18.2, 14.5, 19.8, 12.3, ...]
```
AND: Logs show many conversation splits due to gaps

**Apply**:

**File**: `pipeline/montage/story_pipeline.py:89-91`

**CHANGE**:
```python
max_gap = float(cfg.get("story_max_gap_seconds", 3.5))  # was 2.0
```

**File**: `settings.yaml`

**ADD**:
```yaml
story_max_gap_seconds: 3.5
```

**Expected Result**: Longer conversation blocks → longer chains

---

### FIX OPTION C: Improve Payoff Extension

**IF**: Diagnostics show incomplete chains (missing payoff)

**Evidence Required**:
- Many chains with `is_complete=False`
- Payoff fragments exist in adjacent blocks but not found

**Apply**:

**File**: `pipeline/montage/story_chain_builder.py` (try_extend_chain_for_payoff)

**ENHANCE**: Expand search radius or relax topic matching

**Details**: Requires deeper code analysis (not speculative)

---

### FIX OPTION D: Use Story-Centric Mode

**IF**: Legacy mode showing high starvation

**Evidence Required**:
```
cfg["use_story_centric_pipeline"] = False
[STARVATION #2] linear_builder rejected: (repeated)
[STARVATION #4] fallback rejected: (repeated)
```

**Apply**:

**File**: `settings.yaml`

**CHANGE**:
```yaml
use_story_centric_pipeline: true
```

**Expected Result**: Switch to semantic story chains instead of scene-based windows

---

## 📋 PHASE 5: VALIDATION (DAY 7)

### TASK 5.1: Run Test Again

**Command**:
```bash
python main.py --episode episode01_test.avi --config settings.yaml 2>&1 | tee validation_run.log
```

---

### TASK 5.2: Compare Metrics

**Before Fix** (from diagnostic_run.log):
```
story_chains: X
filtered_chains: Y
final_candidates: Z
```

**After Fix** (from validation_run.log):
```
story_chains: X'
filtered_chains: Y'
final_candidates: Z'
```

**Success**: `Y' > Y` and `Z' > Z` without artificial inflation

---

### TASK 5.3: Verify Quality

**Check**:
1. ✅ No artificial candidates (search logs for "insufficient_context_minimal_candidate")
2. ✅ No forced top-up (search for "minimum_candidate_count")
3. ✅ Natural output (may be low, that's OK)
4. ✅ Story chains complete or near-complete
5. ✅ Candidate quality scores reasonable (>0.5)

**If validation fails**: Revert fix, analyze why, try different approach

---

## 🚫 FORBIDDEN ACTIONS

DO NOT apply these without EXPLICIT evidence:

❌ Blindly lower all thresholds to 0
❌ Remove hard gates entirely  
❌ Disable admission gates  
❌ Add back artificial candidate generation  
❌ Add back forced minimum count  
❌ "Try and see" speculative fixes  
❌ Mass relaxation of multiple gates at once  

**Each fix must be**:
- Justified by diagnostic evidence
- Surgical (changes one thing)
- Reversible (can roll back easily)
- Validated (measurable improvement)

---

## ✅ SUCCESS CRITERIA

After all fixes:

1. ✅ **Zero artificial candidates**
2. ✅ **Zero forced top-up**
3. ✅ **Natural starvation point identified**
4. ✅ **Evidence-backed fix applied**
5. ✅ **Output quality > output quantity**
6. ✅ **Story chains semantically complete**
7. ✅ **No hidden bypasses**

---

## 📊 DECISION TREE

```
DELETE violations (Phase 1)
  ↓
RUN diagnostics (Phase 2-3)
  ↓
ANALYZE results
  ↓
  ├─ Chains exist but < 35s?
  │    → FIX A: Lower duration floor
  │
  ├─ Conversation blocks too small?
  │    → FIX B: Relax grouping
  │
  ├─ Chains incomplete (no payoff)?
  │    → FIX C: Improve extension
  │
  ├─ Legacy mode starvation?
  │    → FIX D: Use story-centric
  │
  └─ No chains at all?
       → INVESTIGATE: transcription quality
                      OR content lacks dialogue
                      OR conversation grouping too strict
```

---

## 🎯 RECOMMENDED SEQUENCE

**Day 1**: Delete lines 8419-8433 (artificial candidate)  
**Day 2**: Delete minimum_candidate_count block  
**Day 3**: Add diagnostic logging (3 points)  
**Day 4**: Run test + analyze results → identify root cause  
**Day 5**: Apply ONE fix (A, B, C, or D based on evidence)  
**Day 6**: Validate fix + measure improvement  
**Day 7**: Document results + plan next iteration if needed

**Total**: 7 days, evidence-driven, no speculation

---

**END OF SAFE FIX PLAN**
