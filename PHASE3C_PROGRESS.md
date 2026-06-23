# PHASE 3C — TURN-FIRST INTEGRATION PROGRESS

**Date**: 2026-06-19 03:48 UTC+3  
**Status**: PARTIALLY COMPLETE (3/8 steps done)

---

## ✅ COMPLETED WORK

### 1. Parameter Addition (DONE)
**File**: `pipeline/face_crop.py:1478`  
**Change**: Added `subtitle_segments=None` parameter to `create_vertical_crop()`

```python
def create_vertical_crop(
    video_path,
    start,
    end,
    out_path,
    subtitle_segments=None,  # PHASE 3C: Turn-first speaker switching
    target_w=720,
    ...
```

### 2. Helper Functions Created (DONE)
**File**: `pipeline/face_crop.py:113-171`

#### `_build_turn_timeline()` — Lines 113-149
Builds merged speaker turn timeline from subtitle segments:
- Filters segments by time range
- Merges consecutive same-speaker segments (gap < 0.3s)
- Returns `[{start, end, speaker, text}, ...]`

#### `_find_best_face_for_speaker()` — Lines 152-171
Finds best face for current turn using speaker priority:
- Filters faces active during turn window
- Uses `_speaker_priority()` ranking
- Returns `(center_x, center_y)` tuple

### 3. Timeline Built (DONE)
**File**: `pipeline/face_crop.py:1811`

```python
# PHASE 3C: Build turn timeline for turn-first speaker switching
turn_timeline = _build_turn_timeline(subtitle_segments, start_t, end_t) if subtitle_segments else []
```

**Current State**: `turn_timeline` variable exists but **NOT USED** yet (dead code).

---

## ⚠️ REMAINING WORK (5/8 steps)

### 4. Pass Timeline to Window Builder (NOT DONE)
**Location**: `pipeline/face_crop.py:1935`  
**Required**: Add `turn_timeline` parameter to `_build_window_targets()` call

```python
targets = _build_window_targets(
    tracks,
    start_t,
    end_t,
    window_sec,
    reframe_mode,
    turn_timeline=turn_timeline,  # ← ADD THIS
    anchor_mode=reframe_anchor_mode,
    ...
```

**Also Required**: Update function signature on line ~440 to accept `turn_timeline=None`

### 5. Window Loop Integration (NOT DONE)
**Location**: `pipeline/face_crop.py:551-956` (window loop body)  
**Required Changes**:

#### A. Determine Active Turn (after line 553)
```python
# Determine active subtitle turn for this window
active_turn = None
previous_turn_speaker = None
current_turn_speaker = None

if turn_timeline:
    for turn in turn_timeline:
        if turn["start"] <= cursor < turn["end"]:
            active_turn = turn
            current_turn_speaker = turn["speaker"]
            break
    
    # Track turn changes
    if len(resolved) > 0:
        prev_window_time = resolved[-1]["start"]
        for turn in turn_timeline:
            if turn["start"] <= prev_window_time < turn["end"]:
                previous_turn_speaker = turn["speaker"]
                break
    
    subtitle_turn_changed = (
        previous_turn_speaker is not None 
        and current_turn_speaker is not None 
        and current_turn_speaker != previous_turn_speaker
    )
```

#### B. Force Switch on Turn Change (before line 560)
```python
# PRIORITY 1: Subtitle turn change overrides face continuity
if subtitle_turn_changed and active_turn:
    # Force re-evaluation — find face for new speaker
    turn_faces = []
    for item in local:
        for face in item.get("faces", []) or []:
            if face.get("detected"):
                turn_faces.append({**face, "t": item["t"]})
    
    if turn_faces:
        turn_center = _find_best_face_for_speaker(
            turn_faces, 
            active_turn["start"], 
            active_turn["end"]
        )
        # Override face resolution
        center = turn_center
        anchor_track_id = "turn_switch"
        target_role = "speaker"
        strength = max(strength, 0.85)  # High confidence for turn switches
```

### 6. Speaker Hold Logic (NOT DONE)
**Location**: Inside `_turn_based_targets()` (~line 1100+)  
**Required**: Add hold/cooldown counters

```python
# PHASE 3C: Speaker hold logic
speaker_hold_frames = 8
speaker_switch_cooldown = 4
current_hold_count = 0
cooldown_count = 0

# During switch detection:
if should_switch:
    if cooldown_count > 0 and not subtitle_turn_changed:
        # Block switch during cooldown (unless subtitle turn changed)
        should_switch = False
    else:
        # Allow switch, reset counters
        current_hold_count = speaker_hold_frames
        cooldown_count = speaker_switch_cooldown
        
# Decrement counters each frame
if current_hold_count > 0:
    current_hold_count -= 1
if cooldown_count > 0:
    cooldown_count -= 1
```

### 7. Metrics Tracking (NOT DONE)
**Location**: After line 956 in `_build_window_targets()`  
**Required**: Add to return value

```python
metrics = {
    "speaker_switch_count": 0,
    "forced_turn_switches": 0,
    "face_refinement_switches": 0,
    "turn_hold_extensions": 0,
    "cooldown_blocks": 0,
    "turn_timeline_length": len(turn_timeline) if turn_timeline else 0,
}

# Track during loop...
# Return: return targets, metrics
```

### 8. Highlight Pipeline Integration (NOT DONE)
**File**: `pipeline/highlight.py`  
**Location**: Find `create_vertical_crop()` call (~line 400-500)

**Required**:
```python
create_vertical_crop(
    video_path=...,
    start=...,
    end=...,
    out_path=...,
    subtitle_segments=candidate.get("subtitle_segments"),  # ← ADD THIS
    ...
```

**Critical**: Pass **candidate-local** subtitle segments, NOT full episode subtitles.

---

## VALIDATION REQUIRED

After integration complete, run validation:

```python
python -m pipeline.story_pipeline --input <test_video> --output <output_dir>
```

**Expected Metrics** (in debug output):
- `forced_turn_switches > 0`
- `speaker_switch_count` aligned with subtitle turn count
- `turn_hold_extensions > 0`
- `cooldown_blocks > 0` (if multi-turn scene)
- `avg_turn_alignment_ms < 300` (switches within 300ms of turn boundary)

---

## RISK ASSESSMENT

**Current State**: Code compiles, backward-compatible (new params optional).

**Risks if deployed incomplete**:
1. ❌ Turn timeline built but **ignored** (dead code)
2. ❌ Speaker switching still face-first (OLD behavior)
3. ❌ Subtitle turn boundaries **NOT authoritative**
4. ✅ No breakage (graceful degradation to face-first)

**Recommendation**: Complete steps 4-8 before validation run.

---

## ESTIMATED EFFORT

- **Steps 4-5**: ~60 lines, 8-10 edits, 30 min
- **Step 6**: ~40 lines, 4-6 edits, 20 min
- **Step 7**: ~30 lines, 2-3 edits, 10 min
- **Step 8**: ~5 lines, 1 edit, 5 min
- **Validation**: Run + report generation, 15 min

**Total**: ~80 minutes focused work

---

## NEXT ACTIONS

1. Add `turn_timeline` param to `_build_window_targets()` signature + call
2. Inject turn detection logic into window loop (line 551-560)
3. Add force-switch on `subtitle_turn_changed == True`
4. Implement hold/cooldown logic in `_turn_based_targets()`
5. Add metrics tracking to `_build_window_targets()` return
6. Update `highlight.py` to pass `subtitle_segments`
7. Run validation + generate `PHASE3C_COMPLETION_REPORT.md`

