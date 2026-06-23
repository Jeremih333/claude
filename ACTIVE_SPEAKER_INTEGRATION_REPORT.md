# ACTIVE SPEAKER INTEGRATION AUDIT — SECTION 2

**Date:** 2026-06-20  
**Status:** ⚠️ **PARTIALLY INTEGRATED**

---

## EXECUTIVE SUMMARY

Turn-first speaker switching logic is **partially integrated**:
- ✅ `_build_turn_timeline()` is called and active
- ✅ `subtitle_segments` parameter exists in signature
- ✅ Turn-first metrics (`compute_turn_first_metrics`) integrated in PHASE 3C
- ❌ `_find_best_face_for_speaker()` is orphaned (never called)
- ⚠️ `subtitle_segments` propagation needs verification

---

## INTEGRATION STATUS

### ✅ SUCCESSFULLY INTEGRATED

#### 1. `_build_turn_timeline()` — ACTIVE

**Location:** `pipeline/face_crop.py` lines 113-149

**Called From:** `create_vertical_crop()` at **line 1856**

```python
# PHASE 3C: Build turn timeline for turn-first speaker switching
turn_timeline = _build_turn_timeline(subtitle_segments, start_t, end_t) if subtitle_segments else []
```

**Function Purpose:**
- Parses subtitle segments to extract speaker turns
- Builds timeline of (start, end, speaker_id) tuples
- Used for turn-based speaker switching instead of face-detection-first

**Status:** ✅ Fully operational when `subtitle_segments` is provided.

---

#### 2. `subtitle_segments` Parameter — PRESENT

**Function Signature:** `pipeline/face_crop.py` line 1537

```python
def create_vertical_crop(
    video_path,
    start,
    end,
    ...
    subtitle_segments=None,  # PHASE 3C: Turn-first speaker switching
    target_w=720,
    ...
):
```

**Status:** ✅ Parameter exists and is documented.

---

#### 3. Turn-First Metrics — INTEGRATED (PHASE 3C)

**Import:** `pipeline/face_crop.py` line 12

```python
from .benchmarking import compute_turn_first_metrics
```

**Usage:** `pipeline/face_crop.py` line 2407

```python
if subtitle_segments and windows:
    try:
        metrics = compute_turn_first_metrics(windows, subtitle_segments, start_t, end_t)
        if isinstance(debug_info, dict) and metrics:
            debug_info["turn_first_metrics"] = metrics
    except Exception:
        pass  # Metrics are optional, don't fail the crop
```

**Status:** ✅ Metrics computation fully integrated. PHASE 3C task completed successfully.

---

### ❌ ORPHANED CODE

#### `_find_best_face_for_speaker()` — NEVER CALLED

**Location:** `pipeline/face_crop.py` lines 152-173

**Function Definition:**
```python
def _find_best_face_for_speaker(windows, speaker_id, target_time):
    """
    Find the best face detection window for a specific speaker around target_time.
    Used for turn-first speaker switching.
    """
    # ... implementation ...
```

**Search Results:** No callers found in entire codebase.

**Analysis:**
- Likely written for future use but never integrated
- Turn timeline approach works without it
- Face selection happens through existing `_pick_best_window()` logic

**Recommendation:** ❌ **DELETE** — Safe to remove (orphaned helper).

---

### ⚠️ INTEGRATION GAP

#### `subtitle_segments` Propagation — NEEDS VERIFICATION

**Question:** Is `subtitle_segments` always passed when calling `create_vertical_crop()`?

**Known Call Sites:** (From subagent analysis)

Search in `pipeline/highlight.py` for `create_vertical_crop()` calls shows:
- Multiple call sites exist
- Need to verify `subtitle_segments` is passed in all paths

**Risk:**
- If `subtitle_segments=None` in some paths → turn-first disabled
- Fall back to face-detection-first behavior
- Turn timeline not built → metrics not computed

**Action Required:**
1. Audit all `create_vertical_crop()` call sites
2. Trace `subtitle_segments` back to transcription source
3. Add logging to detect when subtitle_segments is None
4. Ensure subtitle data flows from transcription → highlight → face_crop

---

## SPEAKER SWITCHING FLOW

### Current Architecture

```
Transcription (subtitle.py)
  → transcribe_segment()
    → Returns subtitle_info with segments
      → Passed to highlight.py
        → Candidate building
          → create_vertical_crop(subtitle_segments=...)
            → _build_turn_timeline(subtitle_segments)
              → Turn-based speaker tracking
```

**Critical Path:** Subtitle segments must flow through entire pipeline.

---

## FACE-FIRST vs TURN-FIRST

### Decision Logic (face_crop.py)

```python
# Line 1856
turn_timeline = _build_turn_timeline(subtitle_segments, start_t, end_t) if subtitle_segments else []

# If turn_timeline is non-empty → turn-first
# If turn_timeline is empty → face-first (existing behavior)
```

**Backward Compatibility:** ✅ Maintained
- No subtitle_segments → fall back to face detection
- System continues to work without subtitles

---

## HIDDEN CONFLICTS

### Search Results: No Active Conflicts Found

**Checked for:**
- Old face-first overrides
- Hidden branches bypassing turn logic
- Conflicting speaker selection

**Result:** Clean — no conflicts detected.

**Note:** The `_find_best_face_for_speaker()` orphan is the only dead code related to speaker logic.

---

## METRICS INTEGRATION (PHASE 3C)

### Benchmarking Integration Status

**compute_turn_first_metrics()** from `pipeline/benchmarking.py`:
- ✅ Imported successfully
- ✅ Called before return in create_vertical_crop
- ✅ Metrics added to debug_info
- ✅ Error handling: metrics failure doesn't break crop

**Metrics Output:**
```python
{
    "turn_first_metrics": {
        "total_turns": N,
        "turns_captured": M,
        "turn_coverage": 0.XX,
        "turn_alignment_score": 0.XX
    }
}
```

**Usage:** Can track turn-first effectiveness in production.

---

## INTEGRATION VERIFICATION CHECKLIST

### Completed ✅
- [x] _build_turn_timeline() is called
- [x] subtitle_segments parameter exists
- [x] Turn-first metrics integrated
- [x] No conflicting speaker logic found
- [x] Backward compatibility maintained

### Needs Verification ⚠️
- [ ] subtitle_segments propagation from highlight.py
- [ ] All create_vertical_crop() call sites pass subtitle_segments
- [ ] Subtitle data available in all execution modes (legacy vs story)

### Cleanup Required 🧹
- [ ] Delete _find_best_face_for_speaker() (orphaned)

---

## RECOMMENDATIONS

### Priority 1: Verify Propagation

**Action:**
1. Search all `create_vertical_crop(` calls in highlight.py
2. Check if `subtitle_segments=subtitle_info.get("segments")` is passed
3. Verify subtitle_info is available at call site

**Command:**
```bash
grep -n "create_vertical_crop(" pipeline/highlight.py
```

### Priority 2: Add Defensive Logging

**Location:** face_crop.py after turn_timeline building

```python
if subtitle_segments:
    logger.info(f"Turn-first active: {len(turn_timeline)} speaker turns detected")
else:
    logger.warning("Turn-first disabled: No subtitle_segments provided — using face-first fallback")
```

**Benefit:** Production visibility into turn-first usage.

### Priority 3: Delete Orphaned Code

**File:** pipeline/face_crop.py  
**Lines:** 152-173  
**Function:** `_find_best_face_for_speaker()`

**Safe to delete:** No callers, no dependencies.

---

## CONCLUSION

Turn-first speaker switching is **mostly integrated**:
- Core logic (`_build_turn_timeline`) is active
- Metrics collection works
- One orphaned helper needs cleanup
- **Critical gap:** subtitle_segments propagation needs verification

**Status:** ⚠️ **FUNCTIONAL BUT INCOMPLETE**

**Next Action:** Verify subtitle_segments flows from highlight.py to face_crop.py in all execution paths.

---

**Report Status:** ✅ COMPLETE  
**Dependencies:** Requires highlight.py audit for call site verification
