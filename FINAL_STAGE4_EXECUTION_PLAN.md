# FINAL STAGE-4 EXECUTION PLAN — SECTION 6

**Date:** 2026-06-20  
**Status:** ✅ **READY FOR EXECUTION**

---

## EXECUTIVE SUMMARY

Based on forensic audit findings, this plan provides **exact actions** to fix critical architectural issues discovered in STAGE-4 validation.

**Critical Findings:**
1. ❌ Story pipeline NOT authoritative (default=False)
2. ⚠️ Turn-first integration incomplete (subtitle_segments propagation unverified)
3. ⚠️ Hold-until-next may create subtitle overlaps after timeline cuts
4. ✅ Minimal dead code (2 safe deletions)

**Execution Order:** PRIORITY 1 → PRIORITY 2 → PRIORITY 3 → PRIORITY 4 → PRIORITY 5

---

## PRIORITY 1: MAKE STORY MODE AUTHORITATIVE 🚨

### Status: CRITICAL — ARCHITECTURE FIX

### Problem

Story pipeline is optional feature flag (default=False) → system runs in legacy mode by default.

**Impact:** All story pipeline improvements don't affect production behavior.

### Solution

**Change default from False to True**

### Exact Actions

#### Action 1.1: Change Feature Flag Default

**File:** `pipeline/highlight.py`  
**Line:** 5394

**SEARCH:**
```python
use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))
```

**REPLACE:**
```python
use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", True))  # STAGE-4: Story mode now default
```

**Risk:** May expose undiscovered story mode bugs  
**Mitigation:** Test exhaustively before deployment

---

#### Action 1.2: Update Configuration Documentation

**File:** `examples/sample_config.yaml`

**ADD:**
```yaml
# Story Pipeline (STAGE-4: Now enabled by default)
# Set to false to use legacy scene-based candidate generation
use_story_centric_pipeline: true  # Default: true (recommended)
```

**Risk:** None  
**Benefit:** Document new default behavior

---

#### Action 1.3: Add Logging for Mode Detection

**File:** `pipeline/highlight.py`  
**After Line:** 5394

**INSERT:**
```python
if use_story_pipeline:
    logger.info("Story-centric pipeline ENABLED (dialogue-first mode)")
else:
    logger.warning("Story-centric pipeline DISABLED — using legacy scene-based mode")
```

**Benefit:** Production visibility into execution mode

---

### Dependencies

None — can execute immediately

### Rollback Plan

Revert line 5394 to `False` if critical bugs discovered

### Verification

```bash
# Check story mode is active
python -c "from pipeline.highlight import HighlightExtractor; h = HighlightExtractor({}); print('Story mode:', h.cfg.get('use_story_centric_pipeline', True))"
```

Expected output: `Story mode: True`

---

## PRIORITY 2: VERIFY SUBTITLE_SEGMENTS PROPAGATION ⚠️

### Status: INTEGRATION GAP

### Problem

Turn-first logic in face_crop.py may not receive `subtitle_segments` in all execution paths.

**Impact:** Turn-first speaker switching silently falls back to face-first if subtitle_segments=None.

### Solution

**Audit all create_vertical_crop() call sites, ensure subtitle_segments is passed**

### Exact Actions

#### Action 2.1: Find All Call Sites

**Command:**
```bash
findstr /N "create_vertical_crop(" pipeline\highlight.py
```

**Goal:** List all locations where create_vertical_crop is invoked

---

#### Action 2.2: Verify subtitle_segments Parameter

**For each call site found:**

Check if pattern matches:
```python
create_vertical_crop(
    ...
    subtitle_segments=subtitle_info.get("segments"),  # ← Must be present
    ...
)
```

**If missing:** Add `subtitle_segments=subtitle_info.get("segments", [])` to call

---

#### Action 2.3: Add Defensive Logging

**File:** `pipeline/face_crop.py`  
**Line:** 1857 (after turn_timeline creation)

**INSERT:**
```python
# STAGE-4: Log turn-first status for production visibility
if subtitle_segments:
    logger.info(f"Turn-first ACTIVE: {len(turn_timeline)} speaker turns detected from {len(subtitle_segments)} subtitle segments")
else:
    logger.warning("Turn-first DISABLED: No subtitle_segments provided — falling back to face-detection-first")
```

**Benefit:** Detect when subtitle data is missing

---

#### Action 2.4: Trace Subtitle Flow

**Verification chain:**

1. `transcribe_segment()` returns `subtitle_info`
2. `subtitle_info` passed to candidate building functions
3. Candidate building calls `create_vertical_crop(subtitle_segments=...)`

**Audit Points:**
- highlight.py candidate building functions
- Check subtitle_info availability at call site
- Verify no code paths skip subtitle_segments

---

### Dependencies

Requires Priority 1 completion (story mode default) to ensure subtitle generation

### Verification

```bash
# Run with logging enabled, check for turn-first messages
python main.py --video test.mp4 --log-level INFO 2>&1 | findstr "turn-first"
```

Expected: "Turn-first ACTIVE" messages

---

## PRIORITY 3: FIX SUBTITLE OVERLAP RISK ⚠️

### Status: TIMING BUG RISK

### Problem

Hold-until-next extends subtitles BEFORE timeline cuts → remap may not account for extensions → overlaps or stale subtitles.

### Solution

**Enhance remap_subtitle_info_after_cuts() to handle hold-until-next extensions**

### Exact Actions

#### Action 3.1: Locate Remap Function

**Command:**
```bash
findstr /N "def remap_subtitle_info_after_cuts" pipeline\subtitle.py
```

**Goal:** Find exact line number of remap function

---

#### Action 3.2: Add Extension Tracking

**File:** `pipeline/subtitle.py`  
**Function:** `_stabilize_subtitle_timeline()`  
**After Line:** 569 (where hold-until-next extends)

**MODIFY:**
```python
if gap < min_gap_sec:
    original_end = event["end"]  # STAGE-4: Track original end
    event["end"] = next_event["start"]
    event["_extended_by_hold_until_next"] = True  # STAGE-4: Flag for remap
    event["_original_end"] = original_end  # STAGE-4: Store original
    logger.debug(f"Hold-until-next: Extended subtitle from {original_end:.2f} to {event['end']:.2f}")
```

**Benefit:** Remap can detect extended subtitles

---

#### Action 3.3: Enhance Remap to Handle Extensions

**File:** `pipeline/subtitle.py`  
**Function:** `remap_subtitle_info_after_cuts()`

**ADD after timestamp remapping:**
```python
# STAGE-4: Truncate extended subtitles at cut boundaries
for event in events:
    if event.get("_extended_by_hold_until_next"):
        # Check if extension crosses a cut boundary
        original_end = event.get("_original_end", event["end"])
        current_end = event["end"]
        
        # If next event exists and we're too close, truncate
        next_idx = events.index(event) + 1
        if next_idx < len(events):
            next_start = events[next_idx]["start"]
            if current_end > next_start:
                logger.warning(f"Overlap detected after remap: truncating subtitle at {next_start:.2f}")
                event["end"] = next_start
                
        # Clean up tracking flags
        del event["_extended_by_hold_until_next"]
        del event["_original_end"]
```

---

#### Action 3.4: Add Post-Remap Validation

**File:** `pipeline/subtitle.py`  
**After:** `remap_subtitle_info_after_cuts()` completes

**INSERT:**
```python
# STAGE-4: Validate no overlaps remain
for i in range(len(events) - 1):
    if events[i]["end"] > events[i+1]["start"]:
        logger.error(f"OVERLAP BUG: Event {i} end={events[i]['end']:.2f} > Event {i+1} start={events[i+1]['start']:.2f}")
        # Force fix
        events[i]["end"] = events[i+1]["start"]
        logger.info(f"Auto-fixed: truncated event {i} to {events[i]['end']:.2f}")
```

**Benefit:** Catch and fix any remaining overlaps

---

### Dependencies

None — can execute independently

### Verification

```bash
# Check subtitle timing in output
python main.py --video test.mp4 --debug-subtitles
# Review logs for overlap warnings
```

---

## PRIORITY 4: DELETE DEAD CODE 🧹

### Status: SAFE CLEANUP

### Problem

2 items identified as safe to delete:
1. Orphaned function: `_find_best_face_for_speaker()`
2. Backup file: `pipeline/highlight.py.backup_phase_a`

### Solution

**Remove both items**

### Exact Actions

#### Action 4.1: Delete Orphaned Function

**File:** `pipeline/face_crop.py`  
**Lines:** 152-173

**DELETE:**
```python
def _find_best_face_for_speaker(windows, speaker_id, target_time):
    """
    Find the best face detection window for a specific speaker around target_time.
    Used for turn-first speaker switching.
    """
    # ... (entire function body)
```

**Risk:** ✅ None — never called

---

#### Action 4.2: Delete Backup File

**Command:**
```bash
del pipeline\highlight.py.backup_phase_a
```

**Risk:** ✅ None — backup only, active code in highlight.py

---

### Dependencies

None — can execute anytime

### Verification

```bash
# Verify deletion
dir pipeline\highlight.py.backup_phase_a
# Should show: File Not Found

# Verify code compiles after function deletion
python -m py_compile pipeline\face_crop.py
```

---

## PRIORITY 5: OPTIONAL ENHANCEMENTS 🔧

### Status: NICE TO HAVE

### These are non-critical improvements for future consideration

#### Enhancement 5.1: Rename "Legacy" Functions

**Rationale:** Functions named "legacy" are actually active production code

**Proposed Renames:**
- `_candidate_windows_legacy()` → `_candidate_windows_scene_based()`
- `_fallback_window_candidate()` → `_emergency_single_candidate()`

**Risk:** High — requires updating all callers  
**Benefit:** Clarity — stops misleading developers

**Decision:** DEFER to later phase

---

#### Enhancement 5.2: Remove Feature Flag Entirely

**Goal:** Make story mode THE ONLY mode (no fallback to legacy)

**Change:**
```python
# Delete lines 5394-5399 in highlight.py
# Replace with:
def candidate_windows(self, video_path: str):
    """Generate temporal candidate windows using story-centric pipeline."""
    return self._candidate_windows_story_centric(video_path)
```

**Risk:** Very high — removes emergency fallback  
**Benefit:** Simplifies architecture

**Decision:** DEFER until story mode proven stable in production

---

#### Enhancement 5.3: Audit Montage Directory

**Goal:** Find unused helpers in `pipeline/montage/`

**Action:**
```bash
# List all functions
python -m analysis.list_definitions pipeline/montage/
# Check call sites for each
```

**Risk:** None  
**Benefit:** Additional cleanup opportunities

**Decision:** DEFER to separate cleanup phase

---

## EXECUTION ORDER

### Phase 1: Critical Fixes (Do First)

1. ✅ Priority 1: Make story mode default
2. ✅ Priority 4: Delete dead code (safe, no dependencies)

**Estimated Time:** 30 minutes  
**Risk:** Low

---

### Phase 2: Integration Verification (Do Second)

3. ✅ Priority 2: Verify subtitle_segments propagation

**Estimated Time:** 1-2 hours (audit + fixes)  
**Risk:** Medium (may require multiple call site fixes)

---

### Phase 3: Subtitle Fix (Do Third)

4. ✅ Priority 3: Fix hold-until-next overlap risk

**Estimated Time:** 2-3 hours (locate remap, add tracking, test)  
**Risk:** Medium (timing-sensitive code)

---

### Phase 4: Optional (Do Later)

5. ⏳ Priority 5: Enhancements (defer)

**Estimated Time:** Varies  
**Risk:** High (architectural changes)

---

## ROLLBACK STRATEGY

### If Priority 1 Causes Issues

**Rollback:**
```python
# Revert line 5394 to:
use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))
```

### If Priority 2 Breaks Turn-First

**Rollback:**
- Remove defensive logging
- Turn-first will fall back to face-first (safe degradation)

### If Priority 3 Creates Subtitle Bugs

**Rollback:**
- Remove extension tracking
- Remove remap enhancements
- Hold-until-next will continue as-is (known issue remains)

---

## TESTING CHECKLIST

### After Each Priority

- [ ] Code compiles without errors
- [ ] Unit tests pass (if available)
- [ ] Integration test with sample video
- [ ] Check logs for errors/warnings
- [ ] Verify expected behavior changes

### Full System Test

```bash
# Run complete pipeline
python main.py --video episode01_test.avi --output test_output/

# Check generated shorts
dir test_output\

# Review logs for:
# - "Story-centric pipeline ENABLED"
# - "Turn-first ACTIVE"
# - No overlap warnings
```

---

## SUCCESS CRITERIA

### Priority 1 Success

- ✅ Story mode enabled by default
- ✅ Legacy mode only via explicit config
- ✅ Logs show "Story-centric pipeline ENABLED"

### Priority 2 Success

- ✅ All create_vertical_crop() calls pass subtitle_segments
- ✅ Logs show "Turn-first ACTIVE" when subtitles available
- ✅ Turn timeline built successfully

### Priority 3 Success

- ✅ No subtitle overlap warnings in logs
- ✅ No stale subtitles in output videos
- ✅ Hold-until-next extensions properly truncated at cuts

### Priority 4 Success

- ✅ Orphaned function deleted
- ✅ Backup file deleted
- ✅ Code compiles and runs

---

## DEPENDENCY GRAPH

```
Priority 1 (Story Mode Default)
    ↓
Priority 2 (Subtitle Propagation) ← Depends on P1 for subtitle generation
    ↓
Priority 3 (Overlap Fix) ← Independent, but benefits from P2 logging
    ↓
Priority 4 (Dead Code) ← Independent, can run anytime
    ↓
Priority 5 (Optional) ← Defer
```

---

## RISK ASSESSMENT

| Priority | Risk Level | Impact if Failed | Rollback Difficulty |
|----------|-----------|------------------|---------------------|
| P1 | 🟡 Medium | Story mode bugs exposed | ✅ Easy (1-line change) |
| P2 | 🟢 Low | Turn-first silently disabled | ✅ Easy (remove logging) |
| P3 | 🟡 Medium | Subtitle overlaps/flicker | 🟡 Medium (timing-sensitive) |
| P4 | 🟢 Low | None (cleanup only) | ✅ Easy (restore from git) |
| P5 | 🔴 High | Architecture breakage | 🔴 Hard (many callers) |

---

## FINAL NOTES

### What NOT to Do

❌ **Do NOT delete "legacy" functions** — they are active production code  
❌ **Do NOT remove feature flag yet** — wait for story mode stability  
❌ **Do NOT rename functions yet** — high risk, low benefit

### What TO Do

✅ **Execute Priorities 1-4 in order**  
✅ **Test exhaustively after each priority**  
✅ **Monitor production logs after deployment**  
✅ **Keep rollback plan ready**

---

## CONCLUSION

**STAGE-4 Forensic Validation revealed:**
1. Story pipeline not authoritative (fix in P1)
2. Turn-first integration incomplete (verify in P2)
3. Subtitle overlap risk (fix in P3)
4. Minimal dead code (clean in P4)

**Execution Strategy:**
- Priorities 1-4: Execute now (clear path, manageable risk)
- Priority 5: Defer (high risk, architectural changes)

**Expected Outcome:**
- Story mode becomes default behavior
- Turn-first fully integrated and monitored
- Subtitle timing bugs eliminated
- Codebase cleaned up

**Status:** ✅ **READY FOR EXECUTION IN ACT MODE**

---

**Report Status:** ✅ COMPLETE  
**Total Actions:** 12 exact file changes + 2 deletions  
**Estimated Total Time:** 4-6 hours for Priorities 1-4
