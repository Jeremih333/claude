# ACTIVE_SPEAKER_REBUILD.md
**PHASE 2 ROOT CAUSE RECOVERY — Turn-First vs Face-First Authority**

---

## PROBLEM STATEMENT

**Config says**: `reframe_switch_on_dialogue_turn = True`  
**Code does**: Face tracking → speaker detection → switch on face change  
**Subtitle turns**: NEVER consulted for reframe timing

**Result**: Camera doesn't follow dialogue speaker changes properly

---

## CURRENT FLOW (Face-First) ❌

### Execution Trace:

```python
# 1. User config
cfg["reframe_switch_on_dialogue_turn"] = True  # config.py:228

# 2. Reframe invocation
create_vertical_crop(
    video_path, start, end,
    use_active_speaker=True,  # ✅ Enabled
    reframe_mode="speaker_focus",
    ...
)

# 3. Face tracking (active_speaker.py)
face_tracks = estimate_face_tracks(
    video_path, start, end,
    sample_fps=2,
    detector_profile="light"
)
# Returns: [{ts, faces: [{box, speaking_score, listener_score}]}]

# 4. Speaker selection (face_crop.py:22-35)
def _pick_center(local_tracks, reframe_mode):
    if reframe_mode == "speaker_focus":
        detected = [item for item in local_tracks if item["detected"]]
        if detected:
            # ❌ PROBLEM: Picks face with largest bbox
            best = max(detected, key=lambda item: item["box_w"] * item["box_h"])
            return (best["center_x"], best["center_y"]), best["box_w"] * best["box_h"]

# 5. Speaker switch detection (face_crop.py:1850+)
previous_speaker_anchor = None
for window in windows:
    current_speaker_anchor = _determine_speaker_anchor(window)  # Based on face bbox
    
    if previous_speaker_anchor is not None and current_speaker_anchor != previous_speaker_anchor:
        speaker_switches += 1  # ❌ Switch based on FACE change, not dialogue turn
        
    previous_speaker_anchor = current_speaker_anchor
```

### Problems:

1. **Face bbox size determines speaker** — largest face wins, NOT active speaker
2. **speaking_score exists but ignored** — picked by bbox size, not speaking_score
3. **Subtitle turns NEVER consulted** — turn boundaries not used for switch timing
4. **speaker_switch counter misleading** — counts face changes, not dialogue turns
5. **Config flag `reframe_switch_on_dialogue_turn` is a lie** — has no effect

---

## ROOT CAUSE ANALYSIS

### Evidence #1: Face Detection Returns No Turn Data

**Location**: `active_speaker.py:estimate_face_tracks()`

**Returns**:
```python
[
    {
        "ts": 12.5,  # Timestamp
        "faces": [
            {
                "box_x": 0.3, "box_y": 0.2, "box_w": 0.15, "box_h": 0.20,
                "center_x": 0.375, "center_y": 0.30,
                "speaking_score": 0.72,  # ✅ Present but UNUSED
                "listener_score": 0.15,
                "confidence": 0.88
            }
        ]
    },
    ...
]
```

**Problem**: No `speaker_id`, no `turn_start`, no `turn_end` — just face bboxes at sample points

---

### Evidence #2: _pick_center() Ignores speaking_score

**Location**: `face_crop.py:22-35`

```python
def _pick_center(local_tracks, reframe_mode):
    if not local_tracks:
        return (0.5, 0.5), 0.0
    if reframe_mode == "speaker_focus":
        detected = [item for item in local_tracks if item["detected"]]
        if detected:
            # ❌ Sorts by bbox size, NOT speaking_score
            best = max(detected, key=lambda item: item["box_w"] * item["box_h"])
            return (best["center_x"], best["center_y"]), best["box_w"] * best["box_h"]
```

**Should be**:
```python
# Sort by speaking_score first, bbox size as tiebreaker
best = max(detected, key=lambda item: (
    float(item.get("speaking_score", 0.0)),
    item["box_w"] * item["box_h"]
))
```

---

### Evidence #3: Subtitle Segments NOT Passed to Reframe

**Location**: `highlight.py:11020-11300` (reframe invocation)

```python
cropped, reframe_debug = create_vertical_crop(
    video_path,
    candidate["start"],
    candidate["end"],
    target_w=int(reframe_cfg.get("vertical_w", 720)),
    target_h=int(reframe_cfg.get("vertical_h", 1280)),
    use_active_speaker=bool(reframe_cfg.get("use_visual_asd", True)),
    # ... many parameters
    # ❌ MISSING: subtitle_segments=subtitle_info.get("segments")
)
```

**No subtitle data passed** → reframe logic has no access to dialogue turns

---

### Evidence #4: speaker_switch Detection Ignores Turns

**Location**: `face_crop.py:1845-1870`

```python
speaker_switches = 0
speaker_switch_log = []
previous_speaker_anchor = None

for window in windows:
    # ❌ _determine_speaker_anchor() uses FACE tracking only
    current_speaker_anchor = _determine_speaker_anchor(window, face_tracks)
    
    if previous_speaker_anchor is not None and current_speaker_anchor is not None:
        if current_speaker_anchor != previous_speaker_anchor:
            speaker_switches += 1
            switch_label = f"{previous_speaker_anchor}->{current_speaker_anchor}"
            speaker_switch_log.append((switch_label, confidence))
    
    previous_speaker_anchor = current_speaker_anchor
```

**Problem**: `current_speaker_anchor` derived from face tracking, NOT subtitle turn boundaries

---

## DESIRED FLOW (Turn-First) ✅

### Execution Trace:

```python
# 1. Pass subtitle segments to reframe
create_vertical_crop(
    video_path, start, end,
    subtitle_segments=subtitle_info.get("segments"),  # ✅ NEW PARAMETER
    use_active_speaker=True,
    ...
)

# 2. Build turn timeline from segments
def _build_turn_timeline(segments, start, end):
    """Extract dialogue turn boundaries with speaker attribution."""
    turns = []
    for seg in segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))
        speaker = str(seg.get("speaker", "") or "UNKNOWN")
        
        # Clip to candidate window
        if seg_end < start or seg_start > end:
            continue
        
        turns.append({
            "start": max(start, seg_start),
            "end": min(end, seg_end),
            "speaker": speaker,
            "text": seg.get("text", "")
        })
    
    return turns

turn_timeline = _build_turn_timeline(subtitle_segments, start, end)
# Returns: [
#     {"start": 12.5, "end": 15.2, "speaker": "Speaker_A", "text": "..."},
#     {"start": 15.8, "end": 18.9, "speaker": "Speaker_B", "text": "..."},
#     ...
# ]

# 3. For each reframe window, determine TURN-BASED speaker
for window in windows:
    window_ts = window["ts"]
    
    # ✅ Find active turn at this timestamp
    active_turn = None
    for turn in turn_timeline:
        if turn["start"] <= window_ts <= turn["end"]:
            active_turn = turn
            break
    
    if active_turn:
        current_speaker = active_turn["speaker"]
        
        # ✅ Lookup face bbox for this speaker
        speaker_face = _find_face_for_speaker(
            window["faces"],
            speaker_id=current_speaker
        )
        
        if speaker_face:
            # Use this face for framing
            window["target_face"] = speaker_face
        else:
            # FALLBACK: Use face with highest speaking_score
            window["target_face"] = max(
                window["faces"],
                key=lambda f: f.get("speaking_score", 0.0)
            )

# 4. Speaker switch detection (turn-based)
previous_speaker = None
for turn in turn_timeline:
    current_speaker = turn["speaker"]
    
    if previous_speaker and current_speaker != previous_speaker:
        speaker_switches += 1  # ✅ Switch based on DIALOGUE TURN
        switch_label = f"{previous_speaker}->{current_speaker}"
        speaker_switch_log.append((switch_label, turn["start"]))
    
    previous_speaker = current_speaker
```

### Benefits:

1. ✅ **Turn boundaries PRIMARY** — reframe follows dialogue, not face detection
2. ✅ **Face tracking SECONDARY** — used to refine framing for known speaker
3. ✅ **speaker_switch accurate** — counts dialogue turn changes
4. ✅ **Config flag honored** — `reframe_switch_on_dialogue_turn` actually works
5. ✅ **Fallback graceful** — if face not found → use speaking_score heuristic

---

## IMPLEMENTATION PLAN

### PHASE 2.4.1: Add subtitle_segments Parameter

**File**: `face_crop.py`

**Change**:
```python
def create_vertical_crop(
    video_path,
    start,
    end,
    *,
    target_w=720,
    target_h=1280,
    subtitle_segments=None,  # ✅ NEW PARAMETER
    use_active_speaker=True,
    ...
):
    subtitle_segments = subtitle_segments or []
    
    # Build turn timeline
    turn_timeline = _build_turn_timeline(subtitle_segments, start, end) if subtitle_segments else []
```

**Lines to add**: ~30 lines for `_build_turn_timeline()` helper

---

### PHASE 2.4.2: Build Turn Timeline Helper

**File**: `face_crop.py`

**New Function**:
```python
def _build_turn_timeline(segments, start, end):
    """Extract dialogue turn boundaries from subtitle segments.
    
    Returns:
        list[dict]: [{"start": float, "end": float, "speaker": str, "text": str}]
    """
    turns = []
    current_speaker = None
    current_start = None
    current_end = None
    current_texts = []
    
    for seg in segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))
        speaker = str(seg.get("speaker", "") or "UNKNOWN")
        text = str(seg.get("text", "") or "")
        
        # Skip segments outside candidate window
        if seg_end < start or seg_start > end:
            continue
        
        # Clip to window
        seg_start = max(start, seg_start)
        seg_end = min(end, seg_end)
        
        # Merge consecutive segments by same speaker
        if speaker == current_speaker and current_end is not None:
            # Extend current turn
            current_end = seg_end
            current_texts.append(text)
        else:
            # Save previous turn
            if current_speaker is not None:
                turns.append({
                    "start": current_start,
                    "end": current_end,
                    "speaker": current_speaker,
                    "text": " ".join(current_texts).strip()
                })
            
            # Start new turn
            current_speaker = speaker
            current_start = seg_start
            current_end = seg_end
            current_texts = [text]
    
    # Save final turn
    if current_speaker is not None:
        turns.append({
            "start": current_start,
            "end": current_end,
            "speaker": current_speaker,
            "text": " ".join(current_texts).strip()
        })
    
    return turns
```

---

### PHASE 2.4.3: Modify Window Processing Loop

**File**: `face_crop.py` (inside `create_vertical_crop()`)

**Current** (lines ~400-600):
```python
for window_idx, window in enumerate(windows):
    # Face tracking determines speaker
    current_speaker_anchor = _determine_speaker_anchor(window, face_tracks)
```

**New**:
```python
for window_idx, window in enumerate(windows):
    window_ts = window["ts"]
    
    # ✅ TURN-FIRST: Find active turn at this timestamp
    active_turn = None
    if turn_timeline:
        for turn in turn_timeline:
            if turn["start"] <= window_ts <= turn["end"]:
                active_turn = turn
                break
    
    if active_turn:
        # Turn-based speaker authority
        current_speaker = active_turn["speaker"]
        
        # Find face bbox for this speaker (by speaking_score proximity)
        target_face = _find_best_face_for_speaker(
            window.get("faces", []),
            speaker_hint=current_speaker
        )
    else:
        # FALLBACK: Face-first (legacy behavior)
        target_face = _pick_center(window.get("local_tracks", []), reframe_mode)
```

---

### PHASE 2.4.4: Add Speaker-Face Matching Helper

**File**: `face_crop.py`

**New Function**:
```python
def _find_best_face_for_speaker(faces, speaker_hint=None):
    """Find face bbox most likely to be the active speaker.
    
    Priority:
    1. Face with highest speaking_score
    2. Face with largest bbox (as tiebreaker)
    3. Fallback to frame center if no faces
    
    Args:
        faces: list of face dicts with speaking_score, box_w, box_h
        speaker_hint: optional speaker ID (currently unused, reserved for future face recognition)
    
    Returns:
        (center_x, center_y), strength
    """
    if not faces:
        return (0.5, 0.5), 0.0
    
    # Sort by speaking_score (primary), bbox size (secondary)
    faces = sorted(
        faces,
        key=lambda f: (
            float(f.get("speaking_score", 0.0)),
            float(f.get("box_w", 0.0)) * float(f.get("box_h", 0.0))
        ),
        reverse=True
    )
    
    best = faces[0]
    return (
        float(best.get("center_x", 0.5)),
        float(best.get("center_y", 0.5))
    ), float(best.get("box_w", 0.0)) * float(best.get("box_h", 0.0))
```

---

### PHASE 2.4.5: Update Speaker Switch Detection

**File**: `face_crop.py` (lines ~1845-1870)

**Current**:
```python
speaker_switches = 0
previous_speaker_anchor = None

for window in windows:
    current_speaker_anchor = _determine_speaker_anchor(window, face_tracks)
    
    if previous_speaker_anchor and current_speaker_anchor != previous_speaker_anchor:
        speaker_switches += 1
```

**New**:
```python
speaker_switches = 0
previous_speaker = None

# ✅ Count turn changes, not face changes
for turn in turn_timeline:
    current_speaker = turn["speaker"]
    
    if previous_speaker and current_speaker != previous_speaker:
        speaker_switches += 1
        switch_confidence = 1.0  # Turn-based switches are certain
        switch_label = f"{previous_speaker}->{current_speaker}"
        speaker_switch_log.append((switch_label, switch_confidence))
        
        if progress_callback:
            progress_callback(
                f"[reframe] speaker_turn_switch={switch_label} at {turn['start']:.1f}s"
            )
    
    previous_speaker = current_speaker
```

---

### PHASE 2.4.6: Pass Subtitle Segments from highlight.py

**File**: `highlight.py` (lines ~11020-11300)

**Current**:
```python
cropped, reframe_debug = create_vertical_crop(
    video_path,
    candidate["start"],
    candidate["end"],
    target_w=int(reframe_cfg.get("vertical_w", 720)),
    target_h=int(reframe_cfg.get("vertical_h", 1280)),
    use_active_speaker=bool(reframe_cfg.get("use_visual_asd", True)),
    # ... many params
)
```

**Add**:
```python
cropped, reframe_debug = create_vertical_crop(
    video_path,
    candidate["start"],
    candidate["end"],
    subtitle_segments=subtitle_info.get("segments") if subtitle_info else None,  # ✅ NEW
    target_w=int(reframe_cfg.get("vertical_w", 720)),
    # ... rest of params
)
```

---

## VALIDATION CRITERIA

### Success Metrics:

1. ✅ **speaker_switches count matches dialogue turn changes**
   - Before: Counts face bbox changes (misleading)
   - After: Counts dialogue turn changes (accurate)

2. ✅ **Reframe timing aligns with dialogue turns**
   - Before: Camera switches on face detection changes
   - After: Camera switches when speaker changes in dialogue

3. ✅ **speaking_score influences framing**
   - Before: Largest face wins
   - After: Face with highest speaking_score wins

4. ✅ **Graceful fallback when no turns**
   - If subtitle_segments empty → legacy face-first behavior

5. ✅ **Config flag honored**
   - `reframe_switch_on_dialogue_turn = True` → uses turn timeline
   - `reframe_switch_on_dialogue_turn = False` → uses face-first (legacy)

---

## TESTING PLAN

### Test Case 1: Two-Person Dialogue
**Input**: Episode with clear A-B-A-B turn structure
**Expected**: 
- speaker_switches = number of turn changes
- Reframe follows speaker changes (visual validation)

### Test Case 2: Monologue
**Input**: Single speaker for 60s
**Expected**: 
- speaker_switches = 0
- Camera stays centered on speaker

### Test Case 3: No Subtitle Data
**Input**: Candidate without subtitle_segments
**Expected**: 
- Fallback to face-first behavior
- No crashes, graceful degradation

### Test Case 4: Multi-Face Scene
**Input**: 3+ faces visible, 2 speakers
**Expected**: 
- Camera follows active speaker (by turn)
- Ignores non-speaking faces

---

## IMPLEMENTATION ESTIMATE

**Time**: 2 days

**Files Modified**:
- `face_crop.py` (~150 lines added/modified)
- `highlight.py` (~5 lines added)

**Complexity**: Medium — clean refactor, no hacks

**Risk**: Low — fallback to legacy behavior if subtitle_segments missing

---

## BENEFITS

1. ✅ **Fixes RC-4** — Active speaker authority corrected (turn-first not face-first)
2. ✅ **Honors config** — `reframe_switch_on_dialogue_turn` flag actually works
3. ✅ **Improves framing quality** — camera follows dialogue, not random face changes
4. ✅ **Accurate metrics** — speaker_switches counts real turn changes
5. ✅ **speaking_score finally used** — face selection uses audio signal
6. ✅ **Backward compatible** — fallback to face-first if no subtitle data

---

**CONCLUSION**: Current system is face-first despite config claiming turn-first. Fix: Pass subtitle_segments to reframe, build turn_timeline, use turn boundaries as PRIMARY authority for speaker detection, face tracking as SECONDARY refinement.
