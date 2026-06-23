# STORY AUTHORITY AUDIT
## PHASE 3F FORENSIC VALIDATION

**Date:** 2026-06-20  
**Validator:** Forensic code audit (not report-based)  
**Scope:** Story pipeline vs legacy pipeline authority  

---

## EXECUTIVE SUMMARY

**Conclusion: ⚠️ PARTIAL — Legacy is DEFAULT**

Story-centric pipeline EXISTS and is FUNCTIONAL, but **legacy pipeline is the default execution path**. The feature flag `use_story_centric_pipeline` defaults to `False`, making legacy the primary authority.

**Critical Finding:** Story pipeline is ready but NOT ENABLED by default.

---

## VALIDATION CHECKLIST

### A. Story Pipeline Primary Authority
**Status:** ⚠️ EXISTS BUT NOT DEFAULT

**Routing Decision:** `pipeline/highlight.py` line 5394

```python
use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))
```

**Default Value:** `False`

**Execution Path:**

```python
def _candidate_windows(self, ...):
    use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))
    
    if use_story_pipeline:
        return self._candidate_windows_story_centric(...)  # NEW
    else:
        return self._candidate_windows_legacy(...)  # DEFAULT
```

**Authority Map:**

| Config Setting | Primary Path | Secondary Path |
|----------------|--------------|----------------|
| `use_story_centric_pipeline=True` | Story-centric | Legacy (fallback) |
| `use_story_centric_pipeline=False` (default) | Legacy | None |
| Not configured (default) | Legacy | None |

**Validation:** ⚠️ Story pipeline is NOT primary by default

---

### B. Legacy Candidate Builders Still Reachable
**Status:** ⚠️ FULLY REACHABLE — DEFAULT PATH

**Legacy Functions:**

1. **`_candidate_windows_legacy()`** — Line 5462
   - **Called by:** `_candidate_windows()` when flag=False (default)
   - **Status:** PRIMARY EXECUTION PATH
   - **Reachability:** ALWAYS (default config)

2. **`_build_story_candidates_from_turns_linear()`** — Line 5824
   - **Called by:** `_candidate_windows_legacy()` line 5488
   - **Status:** ACTIVE in legacy path
   - **Reachability:** ALWAYS (default config)

3. **`_build_story_candidates_from_window()`** — Line 6095
   - **Called by:** Various legacy methods
   - **Status:** ACTIVE in legacy path
   - **Reachability:** ALWAYS (default config)

4. **`_fallback_window_candidate()`** — Line 6005
   - **Called by:** Legacy path and story-centric fallback
   - **Status:** ACTIVE in both paths
   - **Reachability:** ALWAYS

**Call Graph:**

```
DEFAULT PATH (use_story_centric_pipeline=False):
    _candidate_windows()
        → _candidate_windows_legacy()
            → _build_story_candidates_from_turns_linear()
            → _build_story_candidates_from_window()
            → _fallback_window_candidate()

STORY-CENTRIC PATH (use_story_centric_pipeline=True):
    _candidate_windows()
        → _candidate_windows_story_centric()
            → build_story_chains_for_episode()
            → story_chain_to_candidate()
            → FALLBACK: _candidate_windows_legacy() (3 scenarios)
            → FALLBACK: _fallback_window_candidate()
```

**Validation:** ⚠️ Legacy builders are FULLY ACTIVE in default config

---

### C. Fallback Paths from Story-Centric
**Status:** ✅ VALIDATED — 3 FALLBACK SCENARIOS

**Location:** `_candidate_windows_story_centric()` lines 5397-5459

**Fallback Scenario 1: No Subtitle Data**
```python
# Line 5414
if not self.subtitle_info or not self.subtitle_info.get("segments"):
    # No subtitle data available, fall back to legacy
    return self._candidate_windows_legacy(...)
```

**Fallback Scenario 2: No Story Chains**
```python
# Line 5425
if not story_chains:
    # Story pipeline returned no chains, fall back to legacy
    return self._candidate_windows_legacy(...)
```

**Fallback Scenario 3: No Valid Windows**
```python
# Line 5458
if not windows:
    # No valid windows from story chains, fall back to legacy
    return self._candidate_windows_legacy(...)
```

**Fallback Authority:**

When story-centric is enabled but fails:
```
Story-centric ENABLED
    ↓
Try build_story_chains_for_episode()
    ↓
IF no subtitles → FALLBACK to legacy
IF no chains → FALLBACK to legacy
IF no windows → FALLBACK to legacy
    ↓
Legacy pipeline executes
```

**Validation:** ✅ Fallback mechanism is sound, legacy is safety net

---

### D. Synthetic Candidate Injection Check
**Status:** ✅ NO SYNTHETIC INJECTION

**Search Results:**

1. **pipeline/highlight.py line 8454-8455**
   ```python
   # PHASE 3: Remove artificial candidate injection
   # If story_pipeline returns no candidates, respect that decision
   ```

2. **pipeline/highlight.py line 9401-9402**
   ```python
   # PHASE 3: Remove minimum candidate top-up
   # Respect quality gates; do not force quantity
   ```

3. **No active synthetic injection code found**

**Fallback is NOT synthetic:**

`_fallback_window_candidate()` (line 6005) creates a minimal candidate from EXISTING window data, not synthetic injection. It requires:
- `speech_density >= 0.18`
- `duration >= 35s`
- Respects quality gates

**Validation:** ✅ No synthetic candidate injection exists

---

## AUTHORITY HIERARCHY

### Default Configuration (use_story_centric_pipeline=False)

```
PRIMARY: Legacy Pipeline
    ↓
_candidate_windows_legacy()
    ↓
├─ _build_story_candidates_from_turns_linear()
├─ _build_story_candidates_from_window()
└─ _fallback_window_candidate()
    ↓
Returns legacy candidates
```

**Authority:** LEGACY = 100%

---

### Story-Centric Configuration (use_story_centric_pipeline=True)

```
PRIMARY: Story Pipeline
    ↓
_candidate_windows_story_centric()
    ↓
build_story_chains_for_episode()
    ↓
story_chain_to_candidate()
    ↓
IF SUCCESS:
    Returns story-centric candidates
    
IF FAILURE (no subtitles/chains/windows):
    FALLBACK → _candidate_windows_legacy()
        ↓
    Returns legacy candidates
```

**Authority:**
- Story-centric = PRIMARY (if data available)
- Legacy = FALLBACK (safety net)

---

## CONFIGURATION ANALYSIS

### Feature Flag: use_story_centric_pipeline

**Location:** Configuration file (settings.yaml, etc.)

**Default:** `False` (not set = False)

**Effect:**

| Value | Primary Path | Candidates Source |
|-------|--------------|-------------------|
| `False` (default) | Legacy | Turn-based linear extraction |
| `True` | Story-centric | Story chains with payoff detection |

**To Enable Story-Centric:**

Add to `settings.yaml`:
```yaml
use_story_centric_pipeline: true
```

Or in episode config:
```python
cfg = {
    "use_story_centric_pipeline": True,
    ...
}
```

---

## LEGACY VS STORY-CENTRIC COMPARISON

### Legacy Pipeline (_candidate_windows_legacy)

**Approach:** Turn-based linear extraction

**Algorithm:**
1. Extract subtitle turns
2. Group into conversation blocks
3. Build candidates from turn sequences
4. Score by dialogue density + duration
5. Filter by minimum thresholds

**Pros:**
- Simple, predictable
- Low computational cost
- No dependencies on complex logic

**Cons:**
- No payoff detection
- No story arc tracking
- No setup/punchline awareness
- Linear only (no cross-scene arcs)

---

### Story-Centric Pipeline (_candidate_windows_story_centric)

**Approach:** Story chain construction with payoff detection

**Algorithm:**
1. Extract subtitle turns
2. Detect story fragments (setup, continuation, payoff)
3. Build story chains across fragments
4. Score by narrative coherence + payoff strength
5. Filter by story quality metrics

**Pros:**
- Payoff-aware (jokes, reveals)
- Multi-block story arcs
- Cross-scene continuation
- Narrative coherence scoring

**Cons:**
- Higher computational cost
- Requires good subtitle quality
- More complex failure modes
- May reject valid non-story content

---

## EXECUTION FLOW MAP

### Default Flow (Legacy)

```
process_episode()
    ↓
pick_candidates()
    ↓
_candidate_windows()
    ↓
use_story_pipeline = False (default)
    ↓
_candidate_windows_legacy()
    ↓
_build_story_candidates_from_turns_linear()
    ↓
Group turns → conversation blocks
    ↓
Build candidates from blocks
    ↓
Score by dialogue density
    ↓
Filter by duration >= 35s
    ↓
Return legacy candidates
```

---

### Story-Centric Flow (Enabled)

```
process_episode()
    ↓
pick_candidates()
    ↓
_candidate_windows()
    ↓
use_story_pipeline = True
    ↓
_candidate_windows_story_centric()
    ↓
Check subtitle data
    ↓
IF no subtitles:
    → FALLBACK to legacy
    ↓
build_story_chains_for_episode()
    ↓
Detect story fragments
    ↓
Build story chains
    ↓
IF no chains:
    → FALLBACK to legacy
    ↓
story_chain_to_candidate()
    ↓
Convert chains → candidates
    ↓
IF no valid windows:
    → FALLBACK to legacy
    ↓
Return story-centric candidates
```

---

## DUAL AUTHORITY CONFLICT

### Current State: CONFLICT

**Problem:**

Two parallel candidate construction systems exist:
1. **Legacy pipeline** — turn-based, linear
2. **Story-centric pipeline** — story chains, payoff-aware

**Conflict:**

- Legacy = DEFAULT (primary authority)
- Story-centric = OPTIONAL (secondary authority)

**Result:**

Most production runs use legacy pipeline, even though story-centric is superior for narrative content.

**Recommendation:**

1. **Short-term:** Enable `use_story_centric_pipeline=True` in production config
2. **Mid-term:** Make story-centric the default, legacy the fallback
3. **Long-term:** Deprecate legacy pipeline entirely

---

## INTEGRATION POINTS

### 1. Episode Processing
- **Entry:** `process_episode()` in highlight.py
- **Decision:** `_candidate_windows()` line 5394
- **Config:** `use_story_centric_pipeline` flag

### 2. Story Chain Building
- **Module:** `pipeline/montage/story_pipeline.py`
- **Function:** `build_story_chains_for_episode()`
- **Input:** subtitle_info, scenes, cfg
- **Output:** List of story chains

### 3. Candidate Conversion
- **Module:** `pipeline/montage/story_pipeline.py`
- **Function:** `story_chain_to_candidate()`
- **Input:** Story chain
- **Output:** Candidate dict with subtitle_segments

### 4. Legacy Fallback
- **Function:** `_candidate_windows_legacy()` line 5462
- **Triggered:** When story-centric fails or disabled
- **Output:** Legacy candidates

---

## REGRESSION RISKS

### LOW RISK ✅
- **Legacy disabled:** Story-centric has fallback to legacy
- **No subtitles:** Falls back to legacy (safe)
- **Story chain failure:** Falls back to legacy (safe)

### MEDIUM RISK ⚠️
- **Story-centric as default:** May reject valid non-story content
  - Mitigation: Fallback to legacy still available
  - Mitigation: Quality thresholds tunable

### MONITORED 👁️
- **Computational cost:** Story-centric is more expensive
  - Mitigation: Caching, optimization passes
- **False rejections:** Story quality gates may be too strict
  - Mitigation: Tune thresholds in Phase 4

---

## FINAL VERDICT

### Story Pipeline Authority
**STATUS: ⚠️ PARTIAL — Exists but not default**

**Working:**
- ✅ Story-centric pipeline exists and is functional
- ✅ Legacy pipeline exists and is functional
- ✅ Fallback mechanism works correctly
- ✅ No synthetic candidate injection
- ✅ Quality gates respected

**Issues:**
- ⚠️ Legacy is DEFAULT (use_story_centric_pipeline=False)
- ⚠️ Story-centric is OPTIONAL (requires flag)
- ⚠️ Dual authority creates confusion

**Authority:**
- DEFAULT: Legacy pipeline (turn-based)
- OPTIONAL: Story-centric pipeline (story chains)
- FALLBACK: Legacy (safety net for story-centric)

**Ready for Phase 4:** ⚠️ CONDITIONAL

Story chain tuning is BLOCKED until story-centric is enabled as default. Otherwise, tuning parameters will have no effect on production runs.

---

## RECOMMENDATIONS

### CRITICAL — Before Phase 4:

1. **Enable story-centric by default:**
   ```yaml
   # settings.yaml
   use_story_centric_pipeline: true
   ```

2. **Make legacy the fallback only:**
   ```python
   # Flip default in code
   use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", True))  # Default True
   ```

3. **Monitor fallback rate:**
   - Track how often story-centric falls back to legacy
   - If fallback rate > 30%, investigate subtitle quality or chain logic

### For Phase 4:

**Once story-centric is default:**
- Tune `story_max_gap_seconds` (allow larger gaps)
- Relax payoff extension matching (reduce false rejections)
- Lower 35s hard floor (accept shorter valid chains)
- Improve multi-block chain continuation

**If legacy remains default:**
- Phase 4 tuning will be INEFFECTIVE
- Story chain parameters will not affect output
- Production will continue using turn-based extraction

---

## EXECUTION PLAN FOR PHASE 4

### Step 1: Enable Story-Centric (REQUIRED)
```yaml
use_story_centric_pipeline: true
```

### Step 2: Validate Story-Centric Activation
Run test episode and verify:
- Story chains built
- Candidates sourced from chains
- Legacy fallback rate < 30%

### Step 3: Tune Story Chain Parameters
- `story_max_gap_seconds`: 8.0 → 12.0
- `story_payoff_extension_topic_threshold`: 0.60 → 0.45
- `minimum_candidate_duration_seconds`: 35.0 → 28.0
- `story_chain_min_payoff_strength`: 0.50 → 0.40

### Step 4: Monitor Metrics
- Candidate count
- Story chain count
- Fallback rate
- Rejection reasons

---

*Audit completed: 2026-06-20 21:28 UTC+3*
