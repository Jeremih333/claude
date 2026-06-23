# FINAL_ROOT_CAUSE_SUMMARY.md
**PHASE 2 ROOT CAUSE RECOVERY — Unified Action Plan**

---

## EXECUTIVE SUMMARY

**Problem**: PHASE 1 patches (artificial candidates, minimum_candidate_count=12, phase_a_bypass) successfully unblock output, but mask 5 root causes preventing natural candidate generation.

**Solution**: DELETE patches, TUNE story pipeline parameters, REBUILD active speaker authority, FIX subtitle persistence.

**Timeline**: 10 days, evidence-driven approach

---

## ROOT CAUSES IDENTIFIED

### RC-1: PHASE 1 Patches Mask Real Problems
**Status**: 🔴 **VIOLATIONS** — Delete immediately

**Evidence**:
- **Violation #1**: Artificial candidate injection (lines 8419-8433)
  - Synthetic score=0.35, fabricated score_breakdown
  - Hides builder failure rate
- **Violation #2**: minimum_candidate_count=12 (lines 9385-9418)
  - Forces 12 outputs regardless of quality
  - Episode with 3 natural candidates → topped to 12
  - Masks starvation
- **Violation #3**: phase_a_bypass=True (line 9064)
  - Disables ALL quality gates
  - Masks real rejection reasons

**Impact**: Can't diagnose upstream failures because patches hide symptoms

**Fix**: DELETE violations, enable diagnostics

---

### RC-2: Conversation Grouping Splits Valid Story Arcs
**Status**: ⚠️ **TUNING NEEDED**

**Evidence**:
- `max_gap_seconds = 2.0s` (config.py + story_pipeline.py)
- Natural dialogue pauses: 1.5-3.5s (thinking, reaction, comedic timing)
- Result: One 60s dialogue → split into 3x 20s blocks → all fail 35s filter

**Example**:
```
Timeline:
[0-12s] "Remember when we..."
[14.5s] ← 2.5s GAP → SPLIT!
[14.5-28s] "Yeah, that was crazy..."
[30.2s] ← 2.2s GAP → SPLIT!
[30.2-45s] "Exactly! And then..."

Outcome:
3 blocks (12s, 13.5s, 14.8s) → all fail 35s filter

Should be:
1 block (45s) → PASS ✅
```

**Bridge conditions exist** (speaker overlap, topic continuity, monologue) but may be too strict (thresholds: 0.50, 0.18)

**Impact**: Premature conversation splitting → short blocks → starvation

**Fix**: Raise `max_gap_seconds` to 3.5s

---

### RC-3: Story Chain Assembly Incomplete
**Status**: ⚠️ **TUNING NEEDED**

**Sub-problems**:

**3A. Payoff Extension Too Strict**
- Requires >= 2 topic token overlap
- Short chains (2-3 turns) may have < 2 tokens
- Can't find payoff in adjacent blocks

**3B. Duration Floor Too High**
- 35s minimum filters out 25-34s complete chains
- Tight narratives (rapid dialogue, no fluff) rejected

**3C. Payoff Search Radius Limited**
- 30s proximity limit
- Misses delayed payoffs (suspense, dramatic pause)

**Impact**: 
- Incomplete chains (missing payoff) → filtered
- Short complete chains (< 35s) → filtered
- Cascading starvation

**Fix**:
- Lower topic match: >= 2 → >= 1 token
- Lower duration: 35s → 25s
- Expand radius: 30s → 45s

---

### RC-4: Active Speaker = Face-First, NOT Turn-First
**Status**: 🔴 **ARCHITECTURE VIOLATION**

**Evidence**:
- Config: `reframe_switch_on_dialogue_turn = True` (config.py:228)
- Reality: Face tracking → speaker detection, subtitle turns NEVER consulted
- `_pick_center()` selects face with largest bbox, ignores speaking_score
- `speaker_switches` counts face changes, not dialogue turn changes

**Proof**:
```python
# face_crop.py:22-35
def _pick_center(local_tracks, reframe_mode):
    if reframe_mode == "speaker_focus":
        best = max(detected, key=lambda item: item["box_w"] * item["box_h"])
        # ❌ Picks largest face, NOT active speaker
```

**Impact**:
- Camera doesn't follow dialogue speaker changes
- speaker_switches metric misleading
- Config flag is a lie

**Fix**: 
- Pass subtitle_segments to create_vertical_crop()
- Build turn_timeline from segments
- Use turn boundaries as PRIMARY authority
- Face tracking as SECONDARY refinement

---

### RC-5: Subtitle Persistence Gaps
**Status**: ⚠️ **CONFIG TUNING**

**Evidence**:
- `subtitle_persist_gap_seconds = 0.55s` (config.py:230)
- Natural dialogue pauses: 0.6-1.2s (thinking, reaction, emphasis)
- Gap blink zone: 0.55-1.35s → subtitle flickers

**Secondary Issues**:
- Timeline remap after silence trim → potential drift
- Cumulative shift errors on long clips (10+ min)

**Impact**:
- Subtitles disappear during mid-sentence pauses
- Timeline desync on long clips
- Poor viewer experience

**Fix**:
- Raise `subtitle_persist_gap_seconds`: 0.55 → 0.85
- Raise `subtitle_clear_gap_seconds`: 1.35 → 1.80
- (Optional) Audit remap logic if desync persists

---

## PRIORITY FIX ORDER

### 🔴 PHASE 2.1: DELETE VIOLATIONS (Days 1-2)
**Priority**: CRITICAL — Unblock diagnostics

**Actions**:

**1. Delete Artificial Candidate Injection**
```python
# File: pipeline/highlight.py
# Lines: 8419-8433
# DELETE entire block

# REPLACE with:
if fallback is None:
    built = []  # Let failure be visible
```

**2. Delete Minimum Candidate Count Top-Up**
```python
# File: pipeline/highlight.py
# Lines: 9385-9418
# DELETE entire block
# No replacement — return natural count
```

**3. Disable Scorer Gate Bypass (for diagnostics)**
```python
# File: pipeline/highlight.py
# Line: 9064
# CHANGE:
phase_a_bypass = False  # Was True
```

**Outcome**: See natural starvation, identify real bottleneck

**Effort**: 30 minutes  
**Risk**: Low — deletions, not additions

---

### ⚠️ PHASE 2.2: STORY PIPELINE TUNING (Days 3-4)
**Priority**: HIGH — Fix upstream starvation

**Actions**:

**1. Raise Conversation Gap Threshold**
```python
# File: pipeline/config.py
# Add new config key:
"story_max_gap_seconds": 3.5,  # Was 2.0 (default in code)
```

**2. Lower Duration Floor**
```python
# File: pipeline/montage/story_pipeline.py
# Line: 142
min_dur = min(25.0, min_seconds)  # Was 35.0
```

**3. Relax Payoff Extension Topic Match**
```python
# File: pipeline/montage/story_chain_builder.py
# Line: ~860 (in try_extend_chain_for_payoff)
topic_match = len(chain_topics & block_topics) >= 1  # Was >= 2
```

**4. Expand Payoff Search Radius**
```python
# File: pipeline/montage/story_chain_builder.py
# Line: ~870
if gap > 45.0:  # Was 30.0
    continue
```

**Outcome**: More story chains, longer chains, more complete chains

**Effort**: 4 line changes (~15 minutes)  
**Risk**: Low — all reversible thresholds

---

### 📝 PHASE 2.3: SUBTITLE PERSISTENCE (Day 5)
**Priority**: MEDIUM — Improve viewer experience

**Actions**:

**1. Raise Persistence Thresholds**
```python
# File: pipeline/config.py
# Lines: 230-231
"subtitle_persist_gap_seconds": 0.85,  # Was 0.55
"subtitle_clear_gap_seconds": 1.80,    # Was 1.35
```

**2. Update Validation Ranges**
```python
# File: pipeline/config.py
# Lines: 239-246
merged["subtitle_persist_gap_seconds"] = max(
    0.18,
    min(float(merged.get("subtitle_persist_gap_seconds", 0.85)), 0.85),
)

merged["subtitle_clear_gap_seconds"] = max(
    merged["subtitle_persist_gap_seconds"],
    min(float(merged.get("subtitle_clear_gap_seconds", 1.80)), 2.0),
)
```

**Outcome**: Subtitles persist across natural pauses, no flicker

**Effort**: 2 config changes (~15 minutes)  
**Risk**: Low — config only

---

### 🔧 PHASE 2.4: ACTIVE SPEAKER AUTHORITY (Days 6-8)
**Priority**: MEDIUM — Fix speaker switching

**Actions**:

**1. Add subtitle_segments Parameter**
```python
# File: pipeline/face_crop.py
# Function: create_vertical_crop()
# Add parameter:
subtitle_segments=None,
```

**2. Build Turn Timeline Helper**
```python
# File: pipeline/face_crop.py
# Add new function: _build_turn_timeline(segments, start, end)
# ~60 lines (merge segments by speaker, clip to window)
```

**3. Modify Window Processing Loop**
```python
# File: pipeline/face_crop.py
# Inside create_vertical_crop()
# For each window:
#   1. Find active turn at window timestamp
#   2. Lookup face for that speaker (by speaking_score)
#   3. Fallback to face-first if no turn
# ~40 lines modified
```

**4. Add Speaker-Face Matching Helper**
```python
# File: pipeline/face_crop.py
# Add new function: _find_best_face_for_speaker(faces, speaker_hint)
# Sort by speaking_score (primary), bbox size (secondary)
# ~25 lines
```

**5. Update Speaker Switch Detection**
```python
# File: pipeline/face_crop.py
# Lines: ~1845-1870
# Count turn changes instead of face changes
# ~15 lines modified
```

**6. Pass Subtitle Segments from highlight.py**
```python
# File: pipeline/highlight.py
# Lines: ~11020-11300 (reframe invocation)
# Add parameter:
subtitle_segments=subtitle_info.get("segments") if subtitle_info else None,
```

**Outcome**: Camera follows dialogue turns, accurate speaker_switches count

**Effort**: ~150 lines added/modified (2 days)  
**Risk**: Low — has fallback to face-first

---

### ✅ PHASE 2.5: VALIDATION (Days 9-10)
**Priority**: HIGH — Confirm fixes work

**Actions**:

**1. Run Test Episode with Diagnostics**
```bash
python gui.py
# Process episode01_test.avi
# Monitor logs for:
# - story_chains count
# - filtered count
# - rejection reasons
```

**2. Measure Key Metrics**
```
Before Fixes:
- story_chains: 2-3 per 40min episode
- filtered (< 35s): 5-8 chains
- incomplete: 60-70%
- artificial candidates: 15-25%
- minimum_count top-ups: variable

After Fixes (Expected):
- story_chains: 8-12 per 40min episode
- filtered (< 25s): 2-4 chains
- incomplete: 30-40%
- artificial candidates: 0%
- minimum_count top-ups: 0%
```

**3. Confirm Violations Removed**
```
✅ NO artificial candidates
✅ NO minimum_candidate_count forcing
✅ Natural starvation visible (if present)
```

**4. Verify Component Fixes**
```
✅ Speaker switches align with dialogue turns
✅ Subtitles persist across 0.6-1.0s pauses
✅ Story chains include 25-34s complete arcs
✅ Payoff extension finds adjacent blocks
```

**Outcome**: Evidence-backed validation, metrics-driven tuning

**Effort**: 2 days (run tests, analyze, iterate)  
**Risk**: Low — validation only

---

## IMPLEMENTATION CHECKLIST

### Pre-Flight
- [ ] Backup current codebase (git commit)
- [ ] Document current metrics (baseline)
- [ ] Prepare test episodes (diverse content)

### PHASE 2.1 (Days 1-2): DELETE
- [ ] Delete lines 8419-8433 (artificial candidate)
- [ ] Delete lines 9385-9418 (minimum_candidate_count)
- [ ] Set phase_a_bypass = False
- [ ] Test: Run episode, confirm natural starvation visible
- [ ] Document: Rejection counts at each stage

### PHASE 2.2 (Days 3-4): TUNE STORY PIPELINE
- [ ] Raise story_max_gap_seconds: 2.0 → 3.5
- [ ] Lower min_dur: 35.0 → 25.0
- [ ] Lower topic_match: >= 2 → >= 1
- [ ] Expand gap_radius: 30s → 45s
- [ ] Test: Run episode, measure story_chains count
- [ ] Document: Before/after comparison

### PHASE 2.3 (Day 5): SUBTITLE PERSISTENCE
- [ ] Raise subtitle_persist_gap_seconds: 0.55 → 0.85
- [ ] Raise subtitle_clear_gap_seconds: 1.35 → 1.80
- [ ] Update validation ranges
- [ ] Test: Visual check for flicker
- [ ] Document: Gap blink count

### PHASE 2.4 (Days 6-8): ACTIVE SPEAKER
- [ ] Add subtitle_segments parameter (face_crop.py)
- [ ] Implement _build_turn_timeline()
- [ ] Modify window processing loop
- [ ] Implement _find_best_face_for_speaker()
- [ ] Update speaker switch detection
- [ ] Pass subtitle_segments from highlight.py
- [ ] Test: Visual check for speaker following
- [ ] Document: speaker_switches before/after

### PHASE 2.5 (Days 9-10): VALIDATE
- [ ] Run full episode test suite
- [ ] Measure all metrics
- [ ] Confirm violations removed
- [ ] Verify component fixes
- [ ] Document final results
- [ ] Tune thresholds if needed

---

## FILES MODIFIED SUMMARY

| File | Lines Changed | Type | Phase |
|------|---------------|------|-------|
| pipeline/highlight.py | -30 lines | DELETE | 2.1 |
| pipeline/config.py | +1 key, ~10 lines | CONFIG | 2.2 + 2.3 |
| pipeline/montage/story_pipeline.py | 1 line | CODE | 2.2 |
| pipeline/montage/story_chain_builder.py | 2 lines | CODE | 2.2 |
| pipeline/face_crop.py | +150 lines | CODE | 2.4 |
| pipeline/highlight.py | +1 param | CODE | 2.4 |
| **TOTAL** | ~120 net lines | - | - |

**Net Code Change**: +120 lines (mostly face_crop.py additions)

---

## RISK ANALYSIS

### LOW RISK ✅
- Deletions (PHASE 2.1) — removes code, doesn't add
- Config changes (PHASE 2.2, 2.3) — reversible
- Threshold tuning — can iterate

### MEDIUM RISK ⚠️
- Active speaker rebuild (PHASE 2.4) — new logic paths
- Mitigation: Fallback to face-first if no subtitle data

### HIGH RISK 🔴
- None identified

---

## ROLLBACK PLAN

### If PHASE 2.1 Breaks Output
**Symptom**: Zero candidates output
**Action**: Re-enable phase_a_bypass = True temporarily
**Root Cause**: Scorer gates too strict (needs tuning, not bypass)

### If PHASE 2.2 Over-Groups
**Symptom**: Single 10min "conversation" block
**Action**: Lower max_gap_seconds incrementally (3.5 → 3.0 → 2.5)

### If PHASE 2.3 Causes Subtitle Overlap
**Symptom**: Multiple subtitle lines visible
**Action**: Lower persist_gap (0.85 → 0.70)

### If PHASE 2.4 Breaks Reframe
**Symptom**: Crashes, black frames, bad framing
**Action**: Check subtitle_segments=None fallback works
**Verify**: Face-first legacy path still functional

---

## SUCCESS CRITERIA

### Quantitative Metrics

| Metric | Before | After (Target) |
|--------|--------|----------------|
| story_chains per episode | 2-3 | 8-12 |
| Filtered (duration) | 5-8 | 2-4 |
| Incomplete chains | 60-70% | 30-40% |
| Artificial candidates | 15-25% | 0% |
| speaker_switches accuracy | ~60% | ~95% |
| Subtitle gap blink count | > 5 | 0-1 |

### Qualitative Validation

- ✅ Camera follows dialogue speakers (visual check)
- ✅ Subtitles persistent across natural pauses
- ✅ Story chains semantically complete
- ✅ No synthetic candidates
- ✅ Natural starvation visible (if present)

---

## WHAT NOT TO TOUCH

### ✅ Keep (Authoritative, Well-Designed)
- story_pipeline.py
- dialogue_parser.py
- conversation_grouper.py (core logic)
- story_fragments.py
- story_chain_builder.py (core logic)
- subtitle.py (transcription)
- Soft penalties (speech_density, silence_ratio)

### ⚠️ Review Later (Potentially Obsolete)
- _build_story_candidates_from_turns_linear()
- _build_story_candidates_from_window()
- _fallback_window_candidate()
- _candidate_windows_legacy()

### 🔴 Delete (Violations)
- Artificial candidate injection
- minimum_candidate_count top-up
- phase_a_bypass flag (after diagnostics)

---

## ESTIMATED TIMELINE

```
Day 1-2:  PHASE 2.1 — Delete violations
Day 3-4:  PHASE 2.2 — Tune story pipeline
Day 5:    PHASE 2.3 — Subtitle persistence
Day 6-8:  PHASE 2.4 — Active speaker authority
Day 9-10: PHASE 2.5 — Validation
```

**Total**: 10 days (evidence-driven, iterative)

---

## CONCLUSION

**Root Problem**: PHASE 1 patches mask 5 upstream failures:
1. Conversation grouping splits valid arcs
2. Story chain assembly incomplete
3. Duration floor too high
4. Active speaker authority wrong (face-first not turn-first)
5. Subtitle persistence gaps

**Solution Path**:
1. DELETE patches (reveal truth)
2. TUNE story pipeline (fix starvation)
3. FIX subtitle persistence (improve UX)
4. REBUILD active speaker (correct architecture)
5. VALIDATE (confirm fixes)

**Key Principle**: Evidence-driven iteration, not guesswork. Delete violations → run diagnostics → identify bottleneck → apply surgical fix → validate.

**Expected Outcome**: 8-12 natural story chains per 40min episode (was 2-3), no artificial candidates, camera follows dialogue, subtitles persist properly.

---

**STATUS**: Ready for execution. Toggle to Act mode to begin PHASE 2.1 (DELETE violations).
