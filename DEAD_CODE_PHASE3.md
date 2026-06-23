# DEAD_CODE_PHASE3.md
**PHASE 3 STABILIZATION — Legacy Execution Path Cleanup**

---

## OBJECTIVE

Identify and remove **dead legacy execution paths** that are:
1. No longer authoritative
2. Bypassed by story_pipeline.py
3. PHASE 1 violations (artificial candidate injection)
4. Unreferenced or unreachable

**Principle**: Clean authoritative pipeline by removing hybrid/legacy contamination

---

## DEAD CODE INVENTORY

### CATEGORY 1: Legacy Story Builders (SUPERSEDED by story_pipeline.py)

#### 1.1 `_candidate_windows_legacy()`
**Location**: `pipeline/highlight.py:5462-5519`

**Status**: ⚠️ **ANALYSIS REQUIRED**

**Function**:
- Scene-based temporal window generation
- Original implementation before story-centric migration
- Comment says "Will be deprecated after story-centric migration is complete"

**Evidence**:
```python
def _candidate_windows_legacy(self, video_path: str):
    """LEGACY: Temporal window generation based on scene detection.
    
    This is the original implementation. Will be deprecated after
    story-centric migration is complete.
    """
```

**Analysis needed**:
- Check if ANY code path still calls `_candidate_windows_legacy()`
- Verify story_pipeline.py fully replaces this
- Search for: `self._candidate_windows_legacy(`

**If unreferenced**: ✅ **SAFE_DELETE** (58 lines)

---

#### 1.2 `_build_story_candidates_from_turns_linear()`
**Location**: `pipeline/highlight.py:5824-5966`

**Status**: ⚠️ **ANALYSIS REQUIRED**

**Function**:
- Linear turn-based candidate building
- Turn clustering with gap merging
- Predates story_chain_builder.py

**Evidence**:
```python
def _build_story_candidates_from_turns_linear(
    self, window_start: float, window_end: float, source: str, summary: dict
):
    turns = summary.get("turns", [])
    # ... 142 lines of turn clustering logic
```

**Overlap with story_pipeline.py**:
- story_pipeline.py: `build_story_chains_for_episode()` does turn grouping
- story_chain_builder.py: Authoritative turn clustering
- This function: Duplicate logic

**Analysis needed**:
- Check if called from `_generate_candidates_story_centric()`
- Verify story_pipeline.py covers all use cases
- Search for: `self._build_story_candidates_from_turns_linear(`

**If unreferenced**: ✅ **SAFE_DELETE** (142 lines)

---

#### 1.3 `_build_story_candidates_from_window()`
**Location**: `pipeline/highlight.py:5669-5822`

**Status**: ⚠️ **ANALYSIS REQUIRED**

**Function**:
- Window-based story candidate builder
- Fragment assembly from dialogue turns
- Alternative to linear builder

**Evidence**:
```python
def _build_story_candidates_from_window(
    self, window_start: float, window_end: float, source: str, summary: dict
):
    # ... 153 lines of fragment-based building
```

**Overlap with story_pipeline.py**:
- story_fragments.py: Authoritative fragment building
- story_chain_builder.py: Authoritative chain assembly
- This function: Duplicate implementation

**Analysis needed**:
- Check if fallback in `_generate_candidates_story_centric()`
- Lines 8411-8413: `if not built: built = self._build_story_candidates_from_window(...)`
- Determine if story_pipeline.py eliminates need for this

**Risk**: Medium (appears to be fallback path)

**If unreferenced OR story_pipeline always succeeds**: ⚠️ **CONSIDER_DELETE** (153 lines)

---

#### 1.4 `_fallback_window_candidate()`
**Location**: `pipeline/highlight.py:5987-6044`

**Status**: ⚠️ **ANALYSIS REQUIRED**

**Function**:
- Minimal fallback candidate when all builders fail
- Emergency candidate generator

**Evidence**:
```python
def _fallback_window_candidate(
    self, window_start: float, window_end: float, source: str, summary: dict
):
    # ... 57 lines of minimal candidate building
```

**Used in**: Lines 8415-8434 (inside artificial injection block)

**Analysis needed**:
- Check if PHASE 1 artificial injection makes this reachable
- If artificial injection removed → this becomes unreachable
- Verify story_pipeline.py never needs "emergency" candidates

**If unreferenced**: ✅ **SAFE_DELETE** (57 lines)

---

### CATEGORY 2: PHASE 1 Violations (Artificial Candidate Injection)

#### 2.1 Artificial Candidate Injection
**Location**: `pipeline/highlight.py:8419-8433`

**Status**: ❌ **MUST DELETE** — PHASE 1 violation

**Code**:
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
```

**Problem**: Injects artificial candidate with fake scores

**Violates**: Story-first authority — candidates MUST come from story_pipeline.py

**Action**: ✅ **DELETE IMMEDIATELY** (15 lines)

**Replacement**: If story_pipeline returns [] → NO candidates (correct behavior)

---

#### 2.2 Minimum Candidate Count Top-Up
**Location**: `pipeline/highlight.py:9385-9418`

**Status**: ❌ **MUST DELETE** — PHASE 1 violation

**Code**:
```python
# PHASE 1 FIX 1.5: Guarantee minimum candidate count per episode
# After all gates and filters, ensure we have at least 12 candidates
# to maximize selection opportunities and prevent starvation
minimum_candidate_count = 12
if len(picked) < minimum_candidate_count and ranked:
    # Sort ranked by score to get best remaining candidates
    remaining_candidates = [c for c in ranked if c not in picked]
    remaining_candidates.sort(key=lambda x: float(x.get("score", 0.0) or 0.0), reverse=True)
    
    needed = minimum_candidate_count - len(picked)
    for candidate in remaining_candidates[:needed]:
        # ... overlap check + append
```

**Problem**: Forces minimum 12 candidates even if quality gates reject them

**Violates**: Quality-first authority — gates must be respected

**Action**: ✅ **DELETE IMMEDIATELY** (33 lines)

**Replacement**: If gates filter to 5 candidates → OK, 5 is correct output

---

#### 2.3 `phase_a_bypass` Flag
**Location**: Search results show 2 occurrences in `pipeline/highlight.py`

**Status**: ❌ **MUST DELETE** — PHASE 1 violation

**Evidence**:
```python
phase_a_bypass = True  # TEMP production experiment

if phase_a_bypass:
    # BYPASS: All scorer gates disabled for hypothesis test
```

**Problem**: Disables ALL quality gates for "production experiment"

**Violates**: Gate authority — gates must always run

**Action**: ✅ **DELETE ALL REFERENCES** (~5-10 lines)

**Search pattern**: `phase_a_bypass`

---

### CATEGORY 3: Duplicate/Unused Helpers

#### 3.1 Duplicate Subtitle Builders
**Status**: ⚠️ **SEARCH REQUIRED**

**Analysis needed**:
- Search for: `def.*subtitle.*builder|def.*generate.*subtitle`
- Check for duplicate implementations in highlight.py vs subtitle.py
- Verify only authoritative subtitle.py used

#### 3.2 Old Title Generators
**Status**: ✅ **ALREADY CLEAN**

**Finding**: Search for viral/legacy title generators returned **0 results**

**Conclusion**: No cleanup needed in titling.py

#### 3.3 Unused Fallback Assemblers
**Status**: ⚠️ **SEARCH REQUIRED**

**Analysis needed**:
- Search for: `def.*assemble.*fallback|def.*emergency.*candidate`
- Check if any unreferenced after story_pipeline migration

---

## DELETION PRIORITY

### PRIORITY 1: MUST DELETE (PHASE 1 Violations) ❌
1. **Artificial candidate injection** (lines 8419-8433) — 15 lines
2. **Minimum candidate count top-up** (lines 9385-9418) — 33 lines
3. **phase_a_bypass flag** (search + delete all) — ~10 lines

**Total**: ~58 lines
**Risk**: LOW (removes violations)
**Benefit**: Enforces story-first + quality-first authority

---

### PRIORITY 2: LIKELY DELETE (Legacy Builders) ⚠️
4. **_candidate_windows_legacy()** (lines 5462-5519) — 58 lines
5. **_build_story_candidates_from_turns_linear()** (lines 5824-5966) — 142 lines
6. **_fallback_window_candidate()** (lines 5987-6044) — 57 lines

**Total**: ~257 lines
**Risk**: MEDIUM (need usage analysis first)
**Benefit**: Simplifies codebase, enforces story_pipeline authority

---

### PRIORITY 3: CONDITIONAL DELETE (Fallback Path) ⚠️
7. **_build_story_candidates_from_window()** (lines 5669-5822) — 153 lines

**Total**: ~153 lines
**Risk**: HIGH (appears to be used as fallback in line 8411)
**Benefit**: Removes duplicate logic if story_pipeline sufficient

---

## ANALYSIS CHECKLIST

### Step 1: Usage Analysis
- [ ] Search for `_candidate_windows_legacy(` calls
- [ ] Search for `_build_story_candidates_from_turns_linear(` calls
- [ ] Search for `_build_story_candidates_from_window(` calls
- [ ] Search for `_fallback_window_candidate(` calls
- [ ] Search for `phase_a_bypass` references
- [ ] Document: Which are UNREFERENCED vs REACHABLE

### Step 2: Story Pipeline Coverage
- [ ] Verify: story_pipeline.py handles all episode types
- [ ] Verify: story_pipeline.py never returns [] inappropriately
- [ ] Verify: No need for "emergency fallback" candidates
- [ ] Document: Coverage gaps (if any)

### Step 3: Impact Assessment
- [ ] Test: Delete PRIORITY 1 (violations)
- [ ] Run: Full pipeline on test episodes
- [ ] Verify: No candidate starvation
- [ ] Verify: Quality gates enforced
- [ ] Document: Impact of deletions

### Step 4: Phased Deletion
- [ ] Phase A: Delete PRIORITY 1 (violations) — SAFE
- [ ] Test: Validate story-first + quality-first authority
- [ ] Phase B: Delete PRIORITY 2 (legacy builders) — MEDIUM RISK
- [ ] Test: Validate story_pipeline coverage
- [ ] Phase C: Delete PRIORITY 3 (fallback) — HIGH RISK
- [ ] Test: Validate no starvation

---

## VALIDATION CRITERIA

### Success Metrics

**1. No Artificial Candidates**
- Before: Candidates with `fallback_reason: "insufficient_context_minimal_candidate"`
- After: Zero artificial candidates
- Verify: All candidates from story_pipeline.py

**2. Quality Gates Respected**
- Before: Minimum 12 candidates forced
- After: Gate output = final output (may be < 12)
- Verify: Low-quality episodes → fewer candidates (correct)

**3. No phase_a_bypass**
- Before: Gates bypassed
- After: All gates run
- Verify: Rejection logs show gate enforcement

**4. Story Pipeline Authority**
- Before: Multiple candidate builders (hybrid)
- After: Only story_pipeline.py (authoritative)
- Verify: `_generate_candidates_story_centric()` only calls story_pipeline

**5. Code Simplification**
- Before: ~468 lines of legacy/violation code
- After: Legacy removed, story_pipeline authoritative
- Verify: Codebase smaller, easier to maintain

---

## TESTING PLAN

### Test Case 1: High-Quality Episode
**Input**: Clear dialogue, complete story arcs
**Expected**: 
- story_pipeline returns 8-12 candidates
- No fallback paths triggered
- All candidates authentic (from story_pipeline)

### Test Case 2: Low-Quality Episode
**Input**: Sparse dialogue, fragmented
**Expected**:
- story_pipeline returns 2-4 candidates (or 0)
- Gates reject low-quality candidates
- NO artificial top-up to 12
- Correct behavior: fewer candidates

### Test Case 3: Edge Case (Monologue)
**Input**: Single speaker, 20min
**Expected**:
- story_pipeline handles gracefully
- No legacy fallback needed
- Candidates from story_pipeline only

### Test Case 4: No Dialogue
**Input**: Action sequence, no speech
**Expected**:
- story_pipeline returns []
- NO artificial injection
- Correct behavior: zero candidates

### Test Case 5: phase_a_bypass Removed
**Input**: Any episode
**Expected**:
- All quality gates run
- Rejection logs show gate decisions
- No bypass paths active

---

## ROLLBACK PLAN

### If Candidate Starvation Increases
**Symptom**: Episodes with 0 candidates that previously had some

**Action**:
1. Check story_pipeline.py coverage
2. Verify minimum_duration thresholds not too high
3. Check if artificial injection was masking story_pipeline bugs
4. FIX story_pipeline, DON'T restore artificial injection

### If Legacy Builder Actually Needed
**Symptom**: Crashes on specific episode types

**Action**:
1. Identify episode characteristics
2. Add coverage to story_pipeline.py
3. DON'T restore legacy builder
4. Fix authoritative path, not fallback

### If Quality Drops After Deletion
**Symptom**: Lower average candidate quality

**Action**:
1. Verify this isn't selection bias (artificial candidates were low quality)
2. Check if quality gates too strict
3. Tune gates, DON'T restore violations

---

## IMPLEMENTATION STEPS

### STEP 1: Delete PRIORITY 1 (PHASE 1 Violations)

**Files**: `pipeline/highlight.py`

**Deletions**:
1. Lines 8419-8433 (artificial injection)
2. Lines 9385-9418 (minimum count top-up)
3. Search + delete all `phase_a_bypass` references

**Test**: Run pipeline, verify no artificial candidates

---

### STEP 2: Usage Analysis (Legacy Builders)

**Search commands**:
```python
# In pipeline/highlight.py
grep -n "_candidate_windows_legacy(" pipeline/highlight.py
grep -n "_build_story_candidates_from_turns_linear(" pipeline/highlight.py
grep -n "_build_story_candidates_from_window(" pipeline/highlight.py
grep -n "_fallback_window_candidate(" pipeline/highlight.py
```

**Document**: Which functions are UNREFERENCED

---

### STEP 3: Delete PRIORITY 2 (Unreferenced Legacy Builders)

**If analysis shows unreferenced**:
- Delete `_candidate_windows_legacy()` (lines 5462-5519)
- Delete `_build_story_candidates_from_turns_linear()` (lines 5824-5966)
- Delete `_fallback_window_candidate()` (lines 5987-6044)

**Test**: Run pipeline, verify no crashes

---

### STEP 4: Evaluate PRIORITY 3 (Fallback Path)

**Analysis**:
- Check line 8411: Is `_build_story_candidates_from_window()` ever reached?
- Add logging: Count how often this fallback triggers
- If frequency > 5% → keep and refactor
- If frequency < 5% → story_pipeline sufficient → delete

---

## ESTIMATED EFFORT

**Total**: 1-2 days

**Breakdown**:
- Step 1 (Delete violations): 2 hours
- Step 2 (Usage analysis): 2 hours
- Step 3 (Delete unreferenced): 1 hour
- Step 4 (Evaluate fallback): 2 hours
- Testing: 3 hours

**Complexity**: LOW-MEDIUM
- PRIORITY 1 deletions: LOW risk, clear violations
- PRIORITY 2 deletions: MEDIUM risk, need analysis
- PRIORITY 3 deletion: HIGH risk, careful evaluation

---

## BENEFITS

1. ✅ **Story-first authority** — story_pipeline.py is sole source
2. ✅ **No artificial injection** — candidates authentic
3. ✅ **Quality gates enforced** — no bypasses
4. ✅ **Simpler codebase** — ~470 lines removed
5. ✅ **Easier maintenance** — single authoritative path
6. ✅ **Deterministic** — no hybrid execution paths

---

## SUMMARY TABLE

| Code Block | Lines | Priority | Status | Risk |
|------------|-------|----------|--------|------|
| Artificial injection | 15 | P1 | MUST DELETE | LOW |
| Minimum count top-up | 33 | P1 | MUST DELETE | LOW |
| phase_a_bypass flag | ~10 | P1 | MUST DELETE | LOW |
| _candidate_windows_legacy | 58 | P2 | LIKELY DELETE | MEDIUM |
| _build_story_candidates_from_turns_linear | 142 | P2 | LIKELY DELETE | MEDIUM |
| _fallback_window_candidate | 57 | P2 | LIKELY DELETE | MEDIUM |
| _build_story_candidates_from_window | 153 | P3 | CONDITIONAL | HIGH |
| **TOTAL** | **~468** | | | |

---

## CONCLUSION

Dead code cleanup focuses on **enforcing authoritative execution paths**:

1. **Remove PHASE 1 violations** — artificial injection, bypasses
2. **Remove legacy builders** — superseded by story_pipeline.py
3. **Simplify codebase** — single authoritative path

**Key principle**: story_pipeline.py = SOLE candidate source, no fallbacks, no artificial injection.

**Ready for execution**: Deletions identified, analysis steps clear, testing defined.
