# PHASE 4A — AUTHORITY COLLAPSE PLAN
## MIGRATION FROM HYBRID TO STORY-AUTHORITATIVE RUNTIME

**Date:** 2026-06-20  
**Scope:** Collapse dual authority into single story-first pipeline  
**Status:** PLAN ONLY (Do NOT implement yet)  

---

## EXECUTIVE SUMMARY

**Current State:** HYBRID AUTHORITY — two parallel candidate builders compete for control

**Target State:** STORY-AUTHORITATIVE — story chains as primary, legacy as fallback only

**Critical Findings:**
1. ✅ Story-centric pipeline EXISTS and WORKS
2. ⚠️ Legacy pipeline is DEFAULT (use_story_centric_pipeline=False)
3. ⚠️ Config enforces quality-first (selection_policy: quality_first)
4. ⚠️ 4 legacy builders active in default path
5. ❌ 1 dead function confirmed (_find_best_face_for_speaker)

**Migration Strategy:** 5-phase gradual collapse over 9 weeks

---

## COMPLETE RUNTIME GRAPH

```
main.py (line 34)
    ↓
Pipeline.process_episode() [highlight.py:9975]
    ↓
pick_candidates() [highlight.py:8386]
    ↓
_candidate_windows() [highlight.py:5387]
    ↓
    ┌────── CONFIG FORK (line 5394) ──────┐
    │                                      │
    │ use_story_centric_pipeline?          │
    │                                      │
    ▼ TRUE (CONDITIONAL)        ▼ FALSE (DEFAULT)
    │                                      │
┌───────────────────┐          ┌─────────────────────┐
│  STORY PATH       │          │  LEGACY PATH        │
│  (Optional)       │          │  (Default)          │
└───────────────────┘          └─────────────────────┘
    │                                      │
    ├─ _candidate_windows_                ├─ _candidate_windows_legacy
    │  story_centric (5397)                │  (5399, 5414, 5425, 5458)
    │      ↓                                │      ↓
    │  Transcribe episode                   │  Scene detection
    │      ↓                                │      ↓
    │  build_story_chains_                  │  Turn extraction
    │  for_episode()                        │      ↓
    │      ↓                                │  Builder cascade:
    │  story_chain_to_                      │  ├─ _build_story_candidates_
    │  candidate()                          │  │  from_turns_linear (8443)
    │      ↓                                │  │      ↓
    │  Candidate list                       │  ├─ _build_story_candidates_
    │                                       │  │  from_window (8447)
    │  FALLBACK if:                         │  │      ↓
    │  • No subtitles → 5414                │  └─ _fallback_window_
    │  • No chains → 5425                   │     candidate (8451)
    │  • No windows → 5458                  │
    │      ↓                                │
    └──────┬───────────────────────────────┘
           │
           ▼
    CONVERGENCE: Candidate list assembled
           ↓
    _score_story_candidate() [6174]
           ↓
    ┌─── QUALITY GATES (OVERRIDE RISK) ───┐
    │                                       │
    ├─ _dialogue_flow_admission (3845)     │
    │  └─ Rejects BEFORE story analysis    │
    │                                       │
    ├─ _selection_admission_score (8218)   │
    │  └─ Quality score > story continuity │
    │                                       │
    └───────────────────────────────────────┘
           ↓
    rank_story_candidates() [8541]
           ↓
    select_publishable_candidates() [8690]
           ↓
    analyze_active_speaker() [10141]
           ↓
    create_vertical_crop() [face_crop.py:1833]
           ↓
    _build_turn_timeline() [face_crop.py:112]
           ↓
    _build_window_targets() [face_crop.py:505]
           ↓
    _turn_based_targets() / state machine
           ↓
    _stabilize_subtitle_timeline() [subtitle.py:510]
           ↓
    Export video + metadata
```

---

## AUTHORITY CLASSIFICATION

### ✅ AUTHORITATIVE (Story-First)
**Functions that enforce story continuity:**

1. **_candidate_windows_story_centric** (highlight.py:5397)
   - Status: CONDITIONAL (requires flag)
   - Authority: PRIMARY (when enabled)
   - Removable: NO (target architecture)

2. **build_story_chains_for_episode** (story_pipeline.py)
   - Status: ACTIVE (when story-centric enabled)
   - Authority: PRIMARY
   - Removable: NO

3. **_build_turn_timeline** (face_crop.py:112)
   - Status: ACTIVE
   - Authority: PRIMARY (turn-first speaker)
   - Removable: NO

4. **_stabilize_subtitle_timeline** (subtitle.py:510)
   - Status: ACTIVE
   - Authority: PRIORITY 1 (persistence)
   - Removable: NO

---

### ⚠️ LEGACY (Quality-First)
**Functions in DEFAULT path, must migrate or remove:**

1. **_candidate_windows_legacy** (highlight.py:5462)
   - Status: **DEFAULT PATH**
   - Called from:
     - Line 5399 (when flag=False — DEFAULT)
     - Line 5414 (fallback: no subtitles)
     - Line 5425 (fallback: no chains)
     - Line 5458 (fallback: no windows)
   - Authority: PRIMARY (in default config)
   - **Removable: CONDITIONAL** (keep as fallback)
   - **Action: DEMOTE to fallback-only**

2. **_build_story_candidates_from_turns_linear** (highlight.py:5824)
   - Status: ACTIVE (first in cascade)
   - Called from: Line 8443 (always)
   - Authority: PRIMARY (turn-based extraction)
   - **Removable: YES** (after story chains proven)
   - **Action: DEPRECATE → REMOVE**

3. **_build_story_candidates_from_window** (highlight.py:5669)
   - Status: ACTIVE (second in cascade)
   - Called from: Line 8447 (if linear fails)
   - Authority: SECONDARY
   - **Removable: YES**
   - **Action: DEPRECATE → REMOVE**

---

### 🔄 FALLBACK (Safety Net)
**Keep as ultimate safety mechanism:**

1. **_fallback_window_candidate** (highlight.py:6005)
   - Status: ACTIVE (third in cascade)
   - Called from: Line 8451 (if all fail)
   - Authority: ULTIMATE FALLBACK
   - **Removable: NO** (critical safety)
   - **Action: KEEP permanently**

---

### ❌ DEAD CODE
**Remove immediately (zero risk):**

1. **_find_best_face_for_speaker** (face_crop.py:152-173)
   - Status: DEFINED but NEVER CALLED
   - Integration point: Should be in _build_window_targets() ~line 602
   - **Removable: YES** (dead code)
   - **Action: DELETE lines 152-173**

---

## LEGACY PATH REMOVAL ANALYSIS

### Function 1: _build_story_candidates_from_turns_linear
**Location:** highlight.py:5824  
**Call Site:** Line 8443  
**Frequency:** Called for EVERY window in legacy path

**When Called:**
```python
# Line 8443 in pick_candidates()
candidate = self._build_story_candidates_from_turns_linear(
    video_path, window, idx, out_dir
)
```

**Conditions:** No conditions — always first attempt

**Removable:** YES, after story chains replace it

**Dependencies:**
- Called by: pick_candidates() cascade
- Calls: subtitle turn extraction, dialogue grouping
- Output: candidate dict or None

**Removal Impact:**
- BREAKS legacy path if removed without replacement
- Story-centric must be DEFAULT first
- Requires 90%+ success rate validation

---

### Function 2: _build_story_candidates_from_window
**Location:** highlight.py:5669  
**Call Site:** Line 8447  
**Frequency:** Called if turns_linear returns None

**When Called:**
```python
# Line 8447 in pick_candidates()
if not candidate:
    candidate = self._build_story_candidates_from_window(
        video_path, window, idx, out_dir
    )
```

**Conditions:** Only if previous builder failed

**Removable:** YES

**Dependencies:**
- Backup for turns_linear
- Less sophisticated than story chains
- Rarely used in practice

**Removal Impact:**
- LOW — already secondary path
- Story chains cover this case better

---

### Function 3: _candidate_windows_legacy
**Location:** highlight.py:5462  
**Call Sites:** Lines 5399, 5414, 5425, 5458  
**Frequency:** DEFAULT path + 3 fallback scenarios

**When Called:**
```python
# Line 5399 (DEFAULT)
return self._candidate_windows_legacy(video_path)

# Line 5414 (Fallback: no subtitles)
return self._candidate_windows_legacy(video_path)

# Line 5425 (Fallback: no chains)
return self._candidate_windows_legacy(video_path)

# Line 5458 (Fallback: no windows)
return self._candidate_windows_legacy(video_path)
```

**Removable:** CONDITIONAL — keep fallback calls, remove default

**Dependencies:**
- Scene detection
- Turn extraction
- Calls other legacy builders

**Removal Strategy:**
1. Remove line 5399 (default call) → story-centric becomes default
2. KEEP lines 5414, 5425, 5458 (fallback safety)

---

### Function 4: _fallback_window_candidate
**Location:** highlight.py:6005  
**Call Site:** Line 8451  
**Frequency:** Last resort (rare)

**Removable:** NO — critical safety net

**Reason:** Ultimate fallback when all else fails

---

## SCORING CONFLICT MAP

### Conflict 1: _dialogue_flow_admission (lines 3845-3901)
**Problem:** Rejects windows BEFORE story analysis

**Location:** highlight.py:3845  
**Called:** Early in candidate evaluation

**Rejection Logic:**
```python
# Line 3853-3861: Audio starvation
if speech_density < 0.12 and audio_energy < 0.08 and turn_count == 0:
    return {"admit": False, "reason": "audio_starvation"}

# Line 3898-3901: Low dialogue flow
if not sufficient:
    return {"admit": False, "reason": "low_dialogue_flow"}
```

**Impact:** Story candidates with good continuity but low audio metrics get rejected before scoring

**Solution:**
```python
# ADD story override
if story_continuity_score > 0.70:
    # Allow story candidates through even with lower audio
    return {"admit": True, "reason": "story_override"}
```

---

### Conflict 2: _selection_admission_score (lines 8218-8384)
**Problem:** Quality score can deprioritize strong story arcs

**Location:** highlight.py:8218  
**Called:** In candidate ranking

**Current Weights:**
- story_clarity: 23%
- story_completion: 19%
- context_completeness: 15%
- hook_score: 15%
- speech_coverage: 11%
- Other quality metrics: 17%

**Impact:** Story components only 57% of total score

**Solution:**
```python
# NEW WEIGHTS (story-first)
- story_clarity: 35%
- story_completion: 30%
- payoff_strength: 20%
- hook_score: 15%
```

**Story components → 100% of score**

---

### Conflict 3: selection_policy: quality_first
**Problem:** Config explicitly prioritizes quality

**Location:** settings.yaml:17

**Impact:** Entire selection strategy is quality-first

**Solution:**
```yaml
# CHANGE
selection_policy: quality_first
# TO
selection_policy: story_first
```

---

### Conflict 4: selection_admission_fraction: 0.2
**Problem:** Only top 20% admitted by quality

**Location:** settings.yaml:18

**Impact:** Mid-tier story arcs rejected

**Solution:**
```yaml
# INCREASE
selection_admission_fraction: 0.35
# OR make story-conditional
```

---

## CONFIG CONFLICT MAP

### Critical Parameters (settings.yaml)

#### 1. selection_policy (line 17)
**Current:** `quality_first`  
**Required:** `story_first`  
**Impact:** HIGH — changes entire selection strategy

#### 2. use_story_centric_pipeline (line 329)
**Current:** `true` ✅  
**Required:** `true`  
**Impact:** Already correct

#### 3. selection_admission_fraction (line 18)
**Current:** `0.2` (20%)  
**Required:** `0.35` (35%)  
**Impact:** MEDIUM — affects candidate pool size

#### 4. Quality Thresholds
Multiple thresholds that override story continuity:
- `interestingness_threshold: 0.52` (line 70)
- `story_clarity_threshold: 0.56` (line 66)
- `story_coherence_threshold: 0.62` (line 62)
- `hook_score_threshold: 0.34` (line 267)
- `payoff_threshold: 0.48` (line 286)

**Solution:** Make story-conditional:
```yaml
# IF story_continuity_score > 0.70:
#   Apply -20% relaxation to all thresholds
# IF payoff_strength > 0.60:
#   Apply -15% relaxation
```

---

## SAFE DELETION ORDER

### Phase 1: Dead Code Cleanup ✅ ZERO RISK
**Timeline:** Immediate

**Actions:**
1. Delete `_find_best_face_for_speaker` (face_crop.py:152-173)
2. Archive `highlight.py.backup_phase_a`

**Commands:**
```python
# face_crop.py: DELETE lines 152-173
# OR mark deprecated:
# DEPRECATED: Dead code from Phase 3C, never integrated
```

**Risk:** ZERO  
**Rollback:** N/A (dead code removal)

---

### Phase 2: Config Authority Flip ✅ LOW RISK
**Timeline:** Day 1-2

**Actions:**
1. Change `selection_policy: story_first`
2. Set `selection_admission_fraction: 0.35`
3. Verify `use_story_centric_pipeline: true`

**Config Changes:**
```yaml
# settings.yaml
selection_policy: story_first  # WAS: quality_first
selection_admission_fraction: 0.35  # WAS: 0.2
use_story_centric_pipeline: true  # KEEP
```

**Risk:** LOW (legacy fallback available)  
**Rollback:** Instant (revert config)

**Validation:**
- Run 5 test episodes
- Verify story candidates selected
- Check fallback rate < 20%

---

### Phase 3: Legacy Builder Deprecation ⚠️ MEDIUM RISK
**Timeline:** Week 1-2

**Actions:**
1. Add deprecation warnings to legacy builders
2. Log when legacy path used
3. Monitor fallback frequency

**Code Changes:**
```python
# highlight.py:5824
def _build_story_candidates_from_turns_linear(self, ...):
    # DEPRECATED: Use story chains instead
    logger.warning("DEPRECATED: Using legacy turn-linear builder")
    ...

# highlight.py:5669
def _build_story_candidates_from_window(self, ...):
    # DEPRECATED: Use story chains instead
    logger.warning("DEPRECATED: Using legacy window builder")
    ...
```

**Risk:** MEDIUM (behavioral change)  
**Rollback:** Remove warnings

**Monitoring:**
- Track deprecation warning frequency
- Identify episodes triggering legacy path
- Analyze why story-centric failed

---

### Phase 4: Legacy Builder Removal ⚠️ HIGH RISK
**Timeline:** Week 9+ (after 90% success validation)

**Prerequisites:**
- Story-centric success rate > 90%
- Legacy fallback rate < 10%
- No critical bugs in story pipeline

**Actions:**
1. Remove `_build_story_candidates_from_turns_linear` (line 5824)
2. Remove `_build_story_candidates_from_window` (line 5669)
3. Remove call sites (lines 8443, 8447)
4. Keep `_fallback_window_candidate` as safety

**Code Changes:**
```python
# highlight.py: DELETE lines 5824-5920 (turns_linear)
# highlight.py: DELETE lines 5669-5800 (from_window)
# highlight.py: DELETE lines 8443-8449 (cascade calls)
```

**Risk:** HIGH (breaking change)  
**Rollback:** Git revert + config flip

---

### Phase 5: Scoring Authority Adjustment ⚠️ MEDIUM RISK
**Timeline:** After Phase 2 validation

**Actions:**
1. Refactor `_selection_admission_score` weights
2. Make quality thresholds story-conditional
3. Add story_continuity override logic

**Code Changes:**
```python
# highlight.py:8218 - _selection_admission_score()
# NEW WEIGHTS
story_weight = 0.35  # WAS: 0.23
completion_weight = 0.30  # WAS: 0.19
payoff_weight = 0.20  # NEW
hook_weight = 0.15  # WAS: 0.15
```

**Risk:** MEDIUM (changes output profile)  
**Rollback:** Revert weight changes

---

## MIGRATION SEQUENCE

### Week 1: Foundation
**Goals:**
- Dead code cleanup complete
- Config flipped to story-first
- Story-centric validated as primary

**Tasks:**
- [ ] Phase 1: Delete dead code
- [ ] Phase 2: Flip config authority
- [ ] Run 10 test episodes
- [ ] Measure story-centric success rate
- [ ] Document baseline metrics

**Success Criteria:**
- ✅ Zero dead code
- ✅ Story-centric runs by default
- ✅ Fallback rate < 30%

---

### Week 2: Observation
**Goals:**
- Monitor story-centric stability
- Identify failure patterns
- Collect rejection metrics

**Tasks:**
- [ ] Run 50+ episodes across corpus
- [ ] Log all legacy fallback triggers
- [ ] Analyze story chain failures
- [ ] Track quality vs story score conflicts

**Success Criteria:**
- ✅ Story-centric success > 70%
- ✅ Legacy fallback < 20%
- ✅ No critical bugs

---

### Week 3: Deprecation
**Goals:**
- Mark legacy builders deprecated
- Add migration warnings
- Update documentation

**Tasks:**
- [ ] Phase 3: Add deprecation warnings
- [ ] Update README with migration notes
- [ ] Log legacy path usage
- [ ] Notify users of deprecation

**Success Criteria:**
- ✅ Warnings active
- ✅ Usage tracked
- ✅ Documentation updated

---

### Week 4-8: Validation Period
**Goals:**
- Achieve 90% story-centric success
- Reduce legacy fallback to < 10%
- Fix story pipeline bugs

**Tasks:**
- [ ] Run 200+ episodes
- [ ] Fix story chain edge cases
- [ ] Tune story parameters
- [ ] Optimize story scoring

**Success Criteria:**
- ✅ 90% success rate
- ✅ < 10% fallback
- ✅ Zero regressions

---

### Week 9+: Removal
**Goals:**
- Remove deprecated legacy builders
- Adjust scoring authority
- Final validation

**Tasks:**
- [ ] Phase 4: Remove legacy builders
- [ ] Phase 5: Adjust scoring weights
- [ ] Run full regression suite
- [ ] Deploy to production

**Success Criteria:**
- ✅ Legacy code removed
- ✅ Story-first scoring active
- ✅ All tests pass

---

## ROLLBACK PLAN

### Emergency Rollback Triggers
Execute rollback if ANY occurs:

1. **Story-centric failure rate > 30%**
2. **Zero candidates generated in 3+ consecutive runs**
3. **Critical bug in story pipeline**
4. **Fallback rate > 50%**
5. **User-reported regressions**

---

### Rollback Sequence (< 1 hour)

#### Step 1: Config Instant Revert
```yaml
# settings.yaml - REVERT
use_story_centric_pipeline: false
selection_policy: quality_first
selection_admission_fraction: 0.2
```

**Timeline:** < 5 minutes  
**Impact:** Story-centric disabled, legacy active

---

#### Step 2: Code Rollback (if needed)
```bash
git log --oneline -20  # Find last stable commit
git revert <commit-hash>  # Revert breaking changes
git push origin main
```

**Timeline:** < 30 minutes  
**Impact:** Code reverted to stable state

---

#### Step 3: Validation
```bash
# Run smoke tests
python main.py --input-file test_episode.mp4
# Verify legacy path works
# Check candidate generation
```

**Timeline:** < 30 minutes  
**Impact:** Confirm rollback success

---

### Rollback Safety Mechanisms

1. **Config Flag:** Instant disable via `use_story_centric_pipeline: false`
2. **Legacy Preserved:** Legacy pipeline code kept until Phase 4
3. **Git History:** All changes reversible via git
4. **Fallback Active:** Legacy automatically engages on story failure

---

## SUCCESS METRICS & KPIs

### Primary Metrics

#### 1. Story-Centric Success Rate
**Target:** > 90%  
**Measure:** Candidates generated via story path / total episodes  
**Tracking:** `story_chain_count > 0` per episode

#### 2. Legacy Fallback Rate
**Target:** < 10%  
**Measure:** Episodes using legacy path / total episodes  
**Tracking:** `legacy_fallback_reason` logs

#### 3. Candidate Generation Rate
**Target:** Maintained or improved  
**Measure:** Candidates per episode (before vs after)  
**Tracking:** Average candidate count

#### 4. Dead Code Count
**Target:** 0 functions  
**Measure:** Unused function count  
**Tracking:** Static analysis

---

### Secondary Metrics

#### 5. Turn-First Activation
**Target:** > 95% when subtitles present  
**Measure:** `forced_turn_switches` > 0  
**Tracking:** Crop metrics

#### 6. Story Continuity Score
**Target:** Median > 0.65  
**Measure:** Story arc coherence  
**Tracking:** `story_continuity_score` in metadata

#### 7. Selection Policy Compliance
**Target:** 100% story-first  
**Measure:** Config parameter  
**Tracking:** `selection_policy == "story_first"`

---

### Monitoring Dashboard

```python
# Metrics to track per episode
{
    "pipeline_mode": "story_centric" | "legacy",
    "story_chain_count": int,
    "candidate_count": int,
    "legacy_fallback_reason": str | None,
    "forced_turn_switches": int,
    "story_continuity_score": float,
    "selection_policy": str,
}
```

---

## RISK ASSESSMENT SUMMARY

| Phase | Risk Level | Mitigation | Rollback Time |
|-------|------------|------------|---------------|
| Phase 1: Dead Code | ✅ ZERO | N/A (safe delete) | N/A |
| Phase 2: Config Flip | ✅ LOW | Legacy fallback active | < 5 min |
| Phase 3: Deprecation | ⚠️ MEDIUM | Warnings only, code preserved | < 5 min |
| Phase 4: Removal | ⚠️ HIGH | 90% success validation required | < 1 hour |
| Phase 5: Scoring | ⚠️ MEDIUM | Gradual weight adjustment | < 30 min |

---

## FINAL RECOMMENDATIONS

### Before Starting Migration:

1. ✅ **Complete Phase 3F audits** (DONE)
2. ✅ **Verify story-centric works** (validated in Phase 3)
3. ⚠️ **Backup production config**
4. ⚠️ **Establish baseline metrics**
5. ⚠️ **Set up monitoring dashboard**

### During Migration:

1. ⚠️ **Follow phase order strictly**
2. ⚠️ **Validate each phase before next**
3. ⚠️ **Monitor rollback triggers**
4. ⚠️ **Document all changes**
5. ⚠️ **Keep stakeholders informed**

### After Migration:

1. ⚠️ **Run full regression suite**
2. ⚠️ **Monitor production for 2 weeks**
3. ⚠️ **Update documentation**
4. ⚠️ **Archive legacy code**
5. ⚠️ **Celebrate story-first victory!** 🎉

---

*Authority collapse plan completed: 2026-06-20 22:46 UTC+3*
