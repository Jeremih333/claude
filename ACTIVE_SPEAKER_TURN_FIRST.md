# ACTIVE_SPEAKER_TURN_FIRST.md
**PHASE 3 STABILIZATION — Turn-First Speaker Authority Implementation**

---

## OBJECTIVE

Переписать face_crop.py speaker switching logic:

**FROM**: Face detection → speaker selection (face-first)  
**TO**: Dialogue turn → face refinement (turn-first)

**Critical Rule**: Subtitle turn boundaries = PRIMARY trigger, face tracking = SECONDARY refinement

---

## CURRENT ARCHITECTURE (BROKEN)

### Evidence of Face-First Logic

**Location**: `face_crop.py:22-35`
```python
def _pick_center(local_tracks, reframe_mode):
    if reframe_mode == "speaker_focus":
        detected = [item for item in local_tracks if item["detected"]]
        if detected:
            best = max(detected, key=lambda item: item["box_w"] * item["box_h"])
            # ❌ Selects face with largest bbox, ignores speaking_score
            return (best["center_x"], best["center_y"]), best["box_w"] * best["box_h"]
```

**Problem**: Largest face wins, NOT active speaker from dialogue

**speaker_confidence calculation** (lines ~180-250):
- Uses face detection confidence
- Uses mouth motion proxy
- Uses subtitle_turn_alignment_score (but NOT as primary trigger)
- Result: scoring metric, not authority trigger

**Switch detection** (lines ~1700-2100):
- `track_changed = _track_key(candidate_track_id) != _track_key(current_track_id)`
- Detects face track changes
- NOT dialogue turn changes
- speaker_switches counts face bbox switches

---

## REQUIRED ARCHITECTURE (TURN-FIRST)

### New Priority Order

```
PRIORITY 1: dialogue_turn_changed
  ├─ HARD TRIGGER: force switch window 250-400ms
  ├─ Locked for minimum 1.5s per speaker
  └─ Grace period if face not detected

PRIORITY 2: active_speaker_lock (from turn)
  ├─ Preserve speaker for turn duration
  ├─ Ignore face bbox size
  └─ Only release on next turn boundary

PRIORITY 3: face_confidence (refinement only)
  ├─ Used to SELECT face for known speaker
  ├─ speaking_score > bbox_size priority
  └─ Smoothing for stability

PRIORITY 4: face_persistence (stabilization)
  ├─ Keep last frame during brief occlusion
  ├─ Smooth transitions between turns
  └─ Prevent jitter
```

---

## IMPLEMENTATION PLAN

### STEP 1: Add subtitle_segments Parameter

**File**: `pipeline/face_crop.py`

**Function**: `create_vertical_crop()`

**Current signature** (~line 100):
```python
def create_vertical_crop(
    video_path,
    start,
    end,
    *,
    target_w=720,
    target_h=1280,
    use_active_speaker=True,
    reframe_mode="speaker_focus",
    ...
):
```

**Add parameter**:
```python
def create_vertical_crop(
    video_path,
    start,
    end,
    *,
    subtitle_segments=None,  # ✅ NEW
    target_w=720,
    target_h=1280,
    use_active_speaker=True,
    reframe_mode="speaker_focus",
    ...
):
    subtitle_segments = subtitle_segments or []
```

**Lines to add**: ~3 lines

---

### STEP 2: Build Turn Timeline Helper

**File**: `pipeline/face_crop.py`

**Location**: After helper functions (~line 100)

**New function**:
```python
def _build_turn_timeline(segments, start, end):
    """Extract dialogue turn boundaries from subtitle segments.
    
    Merges consecutive segments by same speaker, clips to [start, end] window.
    
    Parameters
    ----------
    segments : list[dict]
        Subtitle segments with keys: start, end, speaker, text
    start : float
        Clip start time (seconds)
    end : float
        Clip end time (seconds)
    
    Returns
    -------
    list[dict]
        Turn timeline: [{"start": float, "end": float, "speaker": str, "text": str}]
    """
    if not segments:
        return []
    
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
        
        # Skip segments outside clip window
        if seg_end < start or seg_start > end:
            continue
        
        # Clip to window boundaries
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
                    "text": " ".join(current_texts).strip(),
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
            "text": " ".join(current_texts).strip(),
        })
    
    return turns
```

**Lines to add**: ~60 lines

---

### STEP 3: Speaker-Face Matching Helper

**File**: `pipeline/face_crop.py`

**Location**: After _build_turn_timeline()

**New function**:
```python
def _find_best_face_for_speaker(faces, speaker_hint=None, turn_timeline=None, window_ts=None):
    """Find face bbox most likely to be the active speaker.
    
    Priority:
    1. Face with highest speaking_score (audio-based)
    2. Face with largest bbox (visual dominance)
    3. Fallback to frame center if no faces
    
    Parameters
    ----------
    faces : list[dict]
        Face detections with keys: speaking_score, box_w, box_h, center_x, center_y
    speaker_hint : str, optional
        Speaker ID from dialogue turn (currently unused, reserved for face recognition)
    turn_timeline : list[dict], optional
        Turn timeline for context (currently unused)
    window_ts : float, optional
        Current timestamp for turn lookup (currently unused)
    
    Returns
    -------
    tuple
        ((center_x, center_y), strength)
    """
    if not faces:
        return (0.5, 0.5), 0.0
    
    # Sort by speaking_score (primary), bbox size (secondary)
    faces_sorted = sorted(
        faces,
        key=lambda f: (
            float(f.get("speaking_score", 0.0)),
            float(f.get("box_w", 0.0)) * float(f.get("box_h", 0.0))
        ),
        reverse=True,
    )
    
    best = faces_sorted[0]
    center = (
        float(best.get("center_x", 0.5)),
        float(best.get("center_y", 0.5))
    )
    strength = float(best.get("box_w", 0.0)) * float(best.get("box_h", 0.0))
    
    return center, strength
```

**Lines to add**: ~35 lines

---

### STEP 4: Modify Window Processing Loop

**File**: `pipeline/face_crop.py`

**Location**: Inside `create_vertical_crop()`, ~line 400-600

**Current logic** (simplified):
```python
# Build turn timeline (NEW)
turn_timeline = _build_turn_timeline(subtitle_segments, start, end) if subtitle_segments else []

# Window loop
for window_idx, window in enumerate(windows):
    window_ts = window["ts"]
    
    # CURRENT: Face-first detection
    current_speaker_anchor = _determine_speaker_anchor(window, face_tracks)
```

**New logic**:
```python
# Build turn timeline (after face_tracks estimation)
turn_timeline = _build_turn_timeline(subtitle_segments, start, end) if subtitle_segments else []

# Track active turn
active_turn = None
previous_turn_speaker = None

# Window loop
for window_idx, window in enumerate(windows):
    window_ts = window["ts"]
    
    # ========================================
    # TURN-FIRST LOGIC (NEW)
    # ========================================
    
    # Find active turn at this timestamp
    active_turn_at_ts = None
    for turn in turn_timeline:
        if turn["start"] <= window_ts <= turn["end"]:
            active_turn_at_ts = turn
            break
    
    if active_turn_at_ts:
        # Turn-based speaker authority
        current_speaker_id = active_turn_at_ts["speaker"]
        
        # Detect turn change
        turn_changed = (
            previous_turn_speaker is not None 
            and current_speaker_id != previous_turn_speaker
        )
        
        if turn_changed:
            # HARD TRIGGER: Force switch on turn boundary
            # Set flag for switch logic downstream
            window["turn_switch_trigger"] = True
            window["turn_speaker"] = current_speaker_id
        
        # Find face for this speaker (by speaking_score)
        window_faces = window.get("faces", [])
        if window_faces:
            target_center, target_strength = _find_best_face_for_speaker(
                window_faces,
                speaker_hint=current_speaker_id,
                turn_timeline=turn_timeline,
                window_ts=window_ts,
            )
            window["turn_based_center"] = target_center
            window["turn_based_strength"] = target_strength
        
        previous_turn_speaker = current_speaker_id
    
    else:
        # No active turn → FALLBACK to face-first (legacy)
        window["turn_switch_trigger"] = False
        window["turn_speaker"] = None
        window["turn_based_center"] = None
    
    # Continue with existing logic (will now use turn_based_center if available)
    # ...
```

**Lines to modify/add**: ~80 lines

**Integration points**:
- Inject turn_based_center into existing target selection
- Respect turn_switch_trigger for hard switches
- Fallback gracefully if turn_timeline empty

---

### STEP 5: Update Speaker Switch Detection

**File**: `pipeline/face_crop.py`

**Location**: After window processing loop (~line 1845-1870)

**Current logic**:
```python
speaker_switches = 0
previous_speaker_anchor = None

for window in windows:
    current_speaker_anchor = _determine_speaker_anchor(window, face_tracks)
    
    if previous_speaker_anchor is not None and current_speaker_anchor != previous_speaker_anchor:
        speaker_switches += 1  # ❌ Counts face changes
```

**New logic**:
```python
speaker_switches = 0
speaker_switch_log = []
previous_turn_speaker = None

# If turn_timeline available, count TURN changes (authoritative)
if turn_timeline:
    for turn in turn_timeline:
        current_speaker = turn["speaker"]
        
        if previous_turn_speaker and current_speaker != previous_turn_speaker:
            speaker_switches += 1
            switch_confidence = 1.0  # Turn-based switches are certain
            switch_label = f"{previous_turn_speaker}->{current_speaker}"
            speaker_switch_log.append({
                "label": switch_label,
                "confidence": switch_confidence,
                "timestamp": turn["start"],
            })
            
            if progress_callback:
                progress_callback(
                    f"[reframe] turn_switch={switch_label} at {turn['start']:.1f}s"
                )
        
        previous_turn_speaker = current_speaker

else:
    # FALLBACK: Count face changes (legacy behavior)
    previous_speaker_anchor = None
    for window in windows:
        current_speaker_anchor = _determine_speaker_anchor(window, face_tracks)
        
        if previous_speaker_anchor is not None and current_speaker_anchor != previous_speaker_anchor:
            speaker_switches += 1
            speaker_switch_log.append({
                "label": f"{previous_speaker_anchor}->{current_speaker_anchor}",
                "confidence": 0.5,  # Face-based switches less certain
                "timestamp": window.get("ts", 0.0),
            })
        
        previous_speaker_anchor = current_speaker_anchor
```

**Lines to modify**: ~30 lines

---

### STEP 6: Pass subtitle_segments from highlight.py

**File**: `pipeline/highlight.py`

**Location**: Reframe invocation (~line 11020-11300)

**Current code**:
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

**Add parameter**:
```python
cropped, reframe_debug = create_vertical_crop(
    video_path,
    candidate["start"],
    candidate["end"],
    subtitle_segments=subtitle_info.get("segments") if subtitle_info else None,  # ✅ NEW
    target_w=int(reframe_cfg.get("vertical_w", 720)),
    target_h=int(reframe_cfg.get("vertical_h", 1280)),
    use_active_speaker=bool(reframe_cfg.get("use_visual_asd", True)),
    # ... rest of params
)
```

**Lines to add**: ~1 line (parameter addition)

---

## VALIDATION CRITERIA

### Success Metrics

**1. Turn Switch Accuracy**
- speaker_switches count MUST equal dialogue turn changes
- Before: Counts face bbox changes (misleading)
- After: Counts dialogue turn changes (accurate)

**2. Camera Follows Dialogue**
- Visual validation: camera switches when speaker changes in transcript
- Before: Camera switches on face detection changes (random timing)
- After: Camera switches at subtitle turn boundaries (synchronized)

**3. speaking_score Priority**
- Face with highest speaking_score selected (not largest bbox)
- Before: Largest face wins regardless of audio
- After: Audio-active face wins

**4. Graceful Fallback**
- If subtitle_segments=None → legacy face-first behavior
- No crashes, no black frames
- Backward compatible

**5. Metrics Accuracy**
- speaker_confidence_score reflects turn alignment
- speaker_switches reflects real turn changes
- Debug logs show "turn_switch" events

---

## TESTING PLAN

### Test Case 1: Two-Person Dialogue (A-B-A-B)
**Input**: Episode with clear turn structure:
- [0-5s] Speaker A
- [5-10s] Speaker B
- [10-15s] Speaker A
- [15-20s] Speaker B

**Expected**:
- speaker_switches = 3 (A→B, B→A, A→B)
- Camera follows speaker changes (visual check)
- No face jitter between turns

### Test Case 2: Monologue (Single Speaker)
**Input**: 60s single speaker

**Expected**:
- speaker_switches = 0
- Camera stable on speaker
- No unnecessary recentering

### Test Case 3: No Subtitle Data (Fallback)
**Input**: Candidate without subtitle_segments

**Expected**:
- Fallback to face-first behavior
- No crashes
- Legacy metrics still computed

### Test Case 4: Multi-Face Scene
**Input**: 3+ faces visible, 2 speakers alternating

**Expected**:
- Camera follows active speaker (by turn)
- Ignores non-speaking faces
- speaking_score prioritized over bbox size

### Test Case 5: Face Occlusion
**Input**: Speaker face temporarily occluded mid-turn

**Expected**:
- Camera holds position (grace period)
- No premature switch
- Recovers when face returns

---

## IMPLEMENTATION CHECKLIST

### Pre-Flight
- [ ] Backup face_crop.py (git commit)
- [ ] Review current speaker switching logs
- [ ] Prepare test episodes with clear turn structure

### Step 1: Parameter Addition
- [ ] Add subtitle_segments parameter to create_vertical_crop()
- [ ] Test: Verify function signature doesn't break existing calls

### Step 2: Turn Timeline Builder
- [ ] Implement _build_turn_timeline()
- [ ] Unit test: Verify speaker merging logic
- [ ] Unit test: Verify window clipping
- [ ] Test: Empty segments → returns []

### Step 3: Face Matching Helper
- [ ] Implement _find_best_face_for_speaker()
- [ ] Test: speaking_score priority over bbox
- [ ] Test: Empty faces → returns center (0.5, 0.5)

### Step 4: Window Loop Integration
- [ ] Build turn_timeline after face_tracks
- [ ] Add turn lookup in window loop
- [ ] Inject turn_based_center into target selection
- [ ] Implement turn_switch_trigger flag
- [ ] Test: turn_timeline empty → legacy behavior

### Step 5: Switch Detection Update
- [ ] Replace face-based switch counting with turn-based
- [ ] Add speaker_switch_log with timestamps
- [ ] Implement fallback for no turn_timeline
- [ ] Test: speaker_switches matches turn count

### Step 6: highlight.py Integration
- [ ] Pass subtitle_segments from highlight.py
- [ ] Test: subtitle_info=None → no crash
- [ ] Test: subtitle_info.segments → turn-first active

### Validation
- [ ] Run Test Case 1 (A-B-A-B dialogue)
- [ ] Run Test Case 2 (monologue)
- [ ] Run Test Case 3 (no subtitles)
- [ ] Run Test Case 4 (multi-face)
- [ ] Run Test Case 5 (occlusion)
- [ ] Visual validation: camera follows turns
- [ ] Metrics validation: speaker_switches accurate
- [ ] Log validation: turn_switch events present

---

## ROLLBACK PLAN

### If Turn-First Breaks Reframe
**Symptom**: Black frames, crashes, bad framing

**Action**:
1. Check subtitle_segments=None fallback works
2. Verify face-first legacy path intact
3. Add debug logging for turn lookup
4. If fatal: revert face_crop.py changes

### If Speaker Switches Over-Count
**Symptom**: speaker_switches >> actual turn changes

**Action**:
1. Check speaker merging in _build_turn_timeline()
2. Verify consecutive same-speaker segments merged
3. Add logging for turn boundaries

### If Camera Doesn't Follow Turns
**Symptom**: Camera ignores dialogue changes

**Action**:
1. Verify turn_based_center injected into target selection
2. Check turn_switch_trigger flag respected
3. Add debug logging for turn_changed detection

---

## ESTIMATED EFFORT

**Total**: 2-3 days

**Breakdown**:
- Day 1: Steps 1-3 (helpers + infrastructure)
- Day 2: Steps 4-5 (integration + switch detection)
- Day 3: Step 6 + validation

**Complexity**: MEDIUM
- Clean architecture (helpers + integration)
- Fallback reduces risk
- No hacks required

---

## BENEFITS

1. ✅ **Camera follows dialogue** — switches at turn boundaries
2. ✅ **speaker_switches accurate** — reflects real turn changes
3. ✅ **speaking_score utilized** — audio signal drives face selection
4. ✅ **Config flag honored** — `reframe_switch_on_dialogue_turn` actually works
5. ✅ **Backward compatible** — fallback to face-first if no subtitles
6. ✅ **Deterministic** — turn boundaries are objective, not heuristic

---

## CONCLUSION

Turn-first architecture aligns reframe logic with semantic reality: **dialogue drives framing, faces refine it**. This fixes RC-4 from PHASE 2 forensics and makes speaker switching deterministic.

**Key principle**: Subtitle turns = SOURCE OF TRUTH, face tracking = refinement layer.

**Ready for execution**: All steps defined, validation clear, rollback safe.
