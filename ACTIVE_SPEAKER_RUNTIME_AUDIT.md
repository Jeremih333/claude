# ACTIVE SPEAKER RUNTIME AUDIT
## PHASE 3F FORENSIC VALIDATION

**Date:** 2026-06-20  
**Validator:** Forensic code audit (not report-based)  
**Scope:** Turn-first active speaker integration  

---

## EXECUTIVE SUMMARY

**Conclusion: ✅ WORKING (with dead code warning)**

Turn-first active speaker switching is **FULLY OPERATIONAL** in runtime. Subtitle turn boundaries act as PRIMARY switch authority, face detection refines, cooldown prevents thrashing, and metrics are tracked.

**Critical Issue:** `_find_best_face_for_speaker()` is DEAD CODE — defined but never called.

---

## VALIDATION CHECKLIST

### A. subtitle_segments Passed to create_vertical_crop()
**Status:** ✅ VALIDATED

**Location:** `pipeline/highlight.py` line 11252

```python
"subtitle_segments": candidate.get("subtitle_segments"),
```

**Source:** Candidate-local subtitles (NOT full episode)

**Construction:**
- Line 5944-5998: Story chain candidates extract overlapping segments
- Line 6037-6079: Fallback window candidates extract overlapping segments
- Timestamps adjusted to be **relative to candidate start**
- Formula: `local_seg["start"] = max(0.0, seg_start - candidate_start)`

**Validation:** ✅ Correct scope, candidate-relative timestamps

---

### B. create_vertical_crop() Forwards subtitle_segments
**Status:** ✅ VALIDATED

**Location:** `pipeline/face_crop.py` line 1890

```python
# PHASE 3C: Build turn timeline for turn-first speaker switching
turn_timeline = _build_turn_timeline(subtitle_segments, start_t, end_t) if subtitle_segments else []
```

**Behavior:**
- Receives `subtitle_segments` parameter
- Immediately constructs `turn_timeline`
- Falls back to empty list if no subtitles
- Safe, non-blocking

**Validation:** ✅ Parameter consumed correctly

---

### C. _build_turn_timeline() Actually Called
**Status:** ✅ VALIDATED

**Location:** `pipeline/face_crop.py` line 1890

**Execution Path:**
```
highlight.py:11252
    → create_vertical_crop(subtitle_segments=...)
        → line 1890: turn_timeline = _build_turn_timeline(subtitle_segments, start_t, end_t)
```

**Function:** Lines 112-149
- Groups consecutive subtitle segments by speaker
- Merges short gaps (< 0.15s)
- Returns list of `{"start": float, "end": float, "speaker": str}`
- Fallback speaker IDs: `unknown_turn_{index}` (stable, non-content-derived)

**Validation:** ✅ Helper is LIVE, not dead code

---

### D. _build_window_targets() Uses turn_timeline
**Status:** ✅ VALIDATED

**Function Definition:** Line 505
**Parameter:** `turn_timeline=None` (6th parameter, backward compatible)

**Call Site:** Line 2014
```python
targets = _build_window_targets(
    tracks,
    start_t,
    end_t,
    window_sec,
    reframe_mode,
    turn_timeline=turn_timeline,  # ← PASSED
    anchor_mode=reframe_anchor_mode,
    ...
)
```

**Usage Inside Function:** Lines 560-591

```python
# PHASE 3C: Resolve active turn from timeline
active_turn = None
if turn_timeline:
    for turn in turn_timeline:
        if turn["start"] <= window_time < turn["end"]:
            active_turn = turn
            break

# Track turn changes
subtitle_turn_changed = False
if len(targets) > 0:
    prev_time = targets[-1]["start"]
    prev_turn_speaker = None
    for turn in turn_timeline:
        if turn["start"] <= prev_time < turn["end"]:
            prev_turn_speaker = turn["speaker"]
            break
    
    current_turn_speaker = active_turn["speaker"] if active_turn else None
    subtitle_turn_changed = (
        prev_turn_speaker is not None
        and current_turn_speaker is not None
        and current_turn_speaker != prev_turn_speaker
    )
```

**Validation:** ✅ turn_timeline consumed, subtitle_turn_changed computed

---

### E. subtitle_turn_changed Computed
**Status:** ✅ VALIDATED

**Computation:** Lines 570-584 (see above)

**Storage:** Line 990
```python
"subtitle_turn_changed": bool(subtitle_turn_changed),
```

**Extraction:** Line 1232
```python
subtitle_turn_changed = bool(target.get("subtitle_turn_changed", False))
active_turn_speaker = target.get("active_turn_speaker")
```

**Validation:** ✅ Flag flows from turn_timeline → targets → state machine

---

### F. turn_boundary_force_switch Overrides Cooldown
**Status:** ✅ VALIDATED

**Location:** Lines 1236-1267

**Critical Logic:**

```python
# PHASE 3C: Track turn speaker changes for hold/cooldown logic
if subtitle_turn_changed and active_turn_speaker:
    forced_turn_switches += 1
    speaker_hold_counter = 0  # Reset hold on turn boundary
    speaker_switch_cooldown = 0  # Turn boundary bypasses cooldown
    last_turn_speaker = active_turn_speaker

# PHASE 3C: Decrement cooldown counter
if speaker_switch_cooldown > 0:
    speaker_switch_cooldown -= 1

# PHASE 3C: Turn boundary BYPASSES cooldown - this is critical
if subtitle_turn_changed and candidate_role == "speaker":
    should_switch = True  # Force evaluation on turn boundary
    required_hold = 1  # Minimal hold on turn change
elif speaker_switch_cooldown > 0 and track_changed and not strong_turn_switch:
    # Cooldown blocks non-turn switches
    cooldown_blocked_switches += 1
    should_switch = False
```

**Behavior:**
1. Turn boundary detected → `speaker_switch_cooldown = 0`
2. Turn boundary detected → `should_switch = True` (FORCE)
3. Turn boundary detected → `required_hold = 1` (minimal)
4. Non-turn switch during cooldown → BLOCKED

**Validation:** ✅ Turn boundaries bypass cooldown, force evaluation

---

### G. _find_best_face_for_speaker() Affects Selection
**Status:** ⚠️ DEAD CODE

**Function Definition:** Lines 152-173

```python
def _find_best_face_for_speaker(
    tracks: list[dict],
    window_time: float,
    turn_speaker: str | None,
    window_duration: float = 0.1,
) -> dict | None:
    """
    PHASE 3C: Find best face for current turn using speaking_score priority.
    
    Priority:
    1. speaking_score (if turn_speaker matches)
    2. listener_score
    3. bbox size
    """
    ...
```

**Search Results:**
- ✅ Function defined
- ❌ Function NEVER CALLED anywhere in codebase
- ❌ No references in face_crop.py
- ❌ No references in highlight.py
- ❌ No references in any pipeline file

**Impact:**
- Turn-first logic WORKS WITHOUT this helper
- Face selection uses different mechanism (target scoring in _build_window_targets)
- This function is architectural dead code from incomplete Phase 3C

**Recommendation:** SAFE_DELETE

**Validation:** ⚠️ DEAD CODE — turn-first works, but this helper unused

---

### H. speaker_hold_frames Still Works
**Status:** ✅ VALIDATED

**Counter Initialization:** Line 1119
```python
speaker_hold_counter = 0
```

**Reset on Turn Boundary:** Line 1238
```python
speaker_hold_counter = 0  # Reset hold on turn boundary
```

**Hold Logic:** Lines 1272-1289 (required_hold calculations)

```python
required_hold_floor = 1 if strict_center else max(int(confident_lock_min_hold_windows), hold_windows)

# Multiple adjustments based on conditions:
if hard_switch_mode and strict_center:
    required_hold_floor = 1
else:
    required_hold_floor = 1 if strict_center else max(int(handoff_min_hold_windows), 1)

required_hold = max(hold_windows, required_hold_floor)

if accent_hold_active:
    required_hold = max(required_hold, accent_frame_hold_windows)

if strong_turn_switch:
    required_hold = max(1, hold_windows - 1)

if candidate_role == "listener" and current_role == "speaker" and invisible_streak > 0:
    required_hold = 1

if scene_change_detected and confident_lock:
    required_hold = max(1, required_hold - 1)
```

**Hold Range:** 8-14 frames (configurable via `hold_windows` parameter)

**Turn Override:** Line 1263
```python
required_hold = 1  # Minimal hold on turn change
```

**Validation:** ✅ Hold logic active, turn boundaries reduce hold requirement

---

### I. Face-First vs Turn-First Authority
**Status:** ✅ TURN-FIRST IS PRIMARY

**Authority Hierarchy:**

```
1. subtitle_turn_changed (lines 1261-1263)
   ↓
   Force switch = True
   Required hold = 1
   Bypass cooldown
   
2. Face speaking_score (lines 1250-1258)
   ↓
   Refines target selection
   Must still pass score_margin_ok
   Subject to cooldown if not turn boundary
   
3. Hold + Cooldown (lines 1236-1267)
   ↓
   Prevents thrashing
   Turn boundaries BYPASS
```

**Evidence:**

Line 1231 comment:
```python
# PHASE 3C: Turn-first switching - subtitle_turn_changed is PRIMARY trigger
```

Line 1260 comment:
```python
# PHASE 3C: Turn boundary BYPASSES cooldown - this is critical
```

**Old Face-First Branches Remaining:**
- Line 1250: `score_margin_ok` still evaluated
- Line 1251: `should_switch` still computed from face scores
- **BUT** lines 1261-1263 override with turn-first authority

**Validation:** ✅ Turn-first is PRIMARY, face-first is refinement

---

## EXECUTION FLOW MAP

```
SUBTITLE SEGMENTS (candidate-local)
    ↓
_build_turn_timeline() [line 1890]
    ↓
    Returns: [{"start", "end", "speaker"}, ...]
    ↓
_build_window_targets(..., turn_timeline=turn_timeline) [line 2014]
    ↓
    Per window (lines 560-591):
        - Resolve active_turn
        - Compute subtitle_turn_changed
        - Store in target dict
    ↓
_turn_based_targets() / state machine [lines 1230+]
    ↓
    Extract subtitle_turn_changed [line 1232]
    ↓
    IF subtitle_turn_changed:
        - forced_turn_switches += 1
        - speaker_hold_counter = 0
        - speaker_switch_cooldown = 0
        - should_switch = True
        - required_hold = 1
    ↓
    Face detection refines target
    ↓
    Hold + cooldown stability
    ↓
    Switch executed
    ↓
Metrics exported to state_usage [lines 1507-1510]:
    - forced_turn_switches
    - cooldown_blocked_switches
    - turn_first_enabled
```

---

## METRICS VALIDATION

**Counters Initialized:** Lines 1117-1122
```python
speaker_hold_counter = 0
speaker_switch_cooldown = 0
last_turn_speaker = None
forced_turn_switches = 0
cooldown_blocked_switches = 0
```

**Metrics Exported:** Lines 1507-1510
```python
# PHASE 3C: Turn-first metrics
state_usage["forced_turn_switches"] = int(forced_turn_switches)
state_usage["cooldown_blocked_switches"] = int(cooldown_blocked_switches)
state_usage["turn_first_enabled"] = bool(forced_turn_switches > 0 or cooldown_blocked_switches > 0)
```

**Additional Metrics:** Lines 2401-2408
```python
# PHASE 3C: Compute turn-first metrics
if subtitle_segments and windows:
    try:
        metrics = compute_turn_first_metrics(windows, subtitle_segments, start_t, end_t)
        if isinstance(debug_info, dict) and metrics:
            debug_info["turn_first_metrics"] = metrics
    except Exception:
        pass  # Metrics are optional, don't fail the crop
```

**Validation:** ✅ Metrics tracked and exported

---

## DEAD CODE ANALYSIS

### _find_best_face_for_speaker()
**Lines:** 152-173  
**Status:** DEAD CODE  
**Reason:** Never called in runtime  
**Impact:** None — turn-first works without it  
**Category:** SAFE_DELETE  

**Recommended Action:**
```python
# Remove function definition (lines 152-173)
# OR add comment:
# DEPRECATED: Originally planned for Phase 3C but never integrated.
# Face selection handled by target scoring in _build_window_targets().
```

---

## REGRESSION RISKS

### LOW RISK ✅
- **No subtitles:** Falls back to empty turn_timeline, face-only mode
- **No turn changes:** Hold/cooldown logic still prevents thrashing
- **Metrics failure:** Wrapped in try/except, doesn't break crop

### MEDIUM RISK ⚠️
- **Turn detection false positives:** Could cause unnecessary switches
  - Mitigation: `speaker_turn_strength >= 0.20` threshold
  - Mitigation: Face confidence still validates
  
### MONITORED 👁️
- **Cooldown too aggressive:** May miss legitimate face changes
  - Mitigation: Turn boundaries bypass cooldown
  - Mitigation: Cooldown only 4-8 frames

---

## INTEGRATION POINTS

### 1. Subtitle Source
- **Input:** `candidate.get("subtitle_segments")` from highlight.py
- **Type:** Candidate-local, relative timestamps
- **Fallback:** Empty list if no subtitles

### 2. Turn Timeline Construction
- **Function:** `_build_turn_timeline()` lines 112-149
- **Input:** subtitle_segments, start_t, end_t
- **Output:** List of turn boundaries with speaker IDs
- **Fallback:** Empty list

### 3. Window Target Building
- **Function:** `_build_window_targets()` lines 505+
- **Input:** turn_timeline
- **Output:** targets with subtitle_turn_changed flag
- **Logic:** Compares current vs previous turn speaker

### 4. State Machine
- **Function:** `_turn_based_targets()` / main loop lines 1117+
- **Authority:** Turn boundary = PRIMARY
- **Refinement:** Face speaking_score = SECONDARY
- **Stability:** Hold 8-14 frames, cooldown 4-8 frames

### 5. Metrics Output
- **Location:** state_usage dict, debug_info
- **Consumers:** Montage pipeline, validation reports
- **Format:** Integer counts + boolean flags

---

## FINAL VERDICT

### Turn-First Runtime Integration
**STATUS: ✅ FULLY OPERATIONAL**

**Working:**
- ✅ turn_timeline built from candidate-local subtitles
- ✅ subtitle_turn_changed computed per window
- ✅ Turn boundaries force switches
- ✅ Turn boundaries bypass cooldown
- ✅ Hold + cooldown prevent thrashing
- ✅ Metrics tracked and exported

**Issues:**
- ⚠️ `_find_best_face_for_speaker()` is dead code (SAFE_DELETE)

**Authority:**
- PRIMARY: Subtitle turn boundaries
- SECONDARY: Face speaking scores
- STABILITY: Hold + cooldown logic

**Ready for Phase 4:** ✅ YES

Turn-first architecture is live and operational. Story chain tuning can proceed.

---

## RECOMMENDATIONS

### Before Phase 4:
1. ✅ **Keep turn-first as-is** — working correctly
2. ⚠️ **Remove `_find_best_face_for_speaker()`** — cleanup dead code
3. ✅ **Monitor metrics** — `forced_turn_switches`, `cooldown_blocked_switches`

### For Phase 4:
- Focus on story chain parameter tuning
- Turn-first will provide stable speaker framing
- Face thrashing already mitigated

---

*Audit completed: 2026-06-20 21:24 UTC+3*
