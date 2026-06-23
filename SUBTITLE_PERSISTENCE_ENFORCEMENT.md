# SUBTITLE_PERSISTENCE_ENFORCEMENT.md
**PHASE 3 STABILIZATION — Subtitle Timeline Lock Implementation**

---

## OBJECTIVE

Enforce **strict subtitle persistence** model:

**RULE**: Subtitle NEVER disappears if next subtitle exists and gap < 900ms

**Current problem**: Subtitles flicker during natural pauses (0.6-1.2s)

**Solution**: Hard-enforce persistence, raise thresholds (PHASE 2), add buffer logic

---

## CURRENT STATE

### Config Settings (config.py:230-231)

```python
"subtitle_persist_gap_seconds": 0.55,  # ⚠️ TOO LOW
"subtitle_clear_gap_seconds": 1.35,    # ⚠️ BORDERLINE
"subtitle_continuity_mode": "always_on_short_gaps",  # ✅ GOOD
"subtitle_renderer_mode": "persistent_sentence_layer",  # ✅ GOOD
```

### Gap Zones

```
Gap < 0.55s    → BRIDGE (keep visible) ✅
Gap 0.55-1.35s → GAP BLINK (flicker) ❌ PROBLEM ZONE
Gap >= 1.35s   → CLEAR (subtitle off) ✅
```

**Natural dialogue pauses**: 0.6-1.2s fall in PROBLEM ZONE

---

## ROOT CAUSES

### 1. persist_gap TOO SHORT (0.55s)

**Location**: `config.py:230`

**Problem**:
- Natural pauses: 0.6-1.2s (thinking, reaction, comedic timing)
- Current threshold: 0.55s
- Result: Most natural pauses fall in gap_blink zone

**Evidence from subtitle.py**:
```python
def _subtitle_persist_gap_seconds(cfg) -> float:
    return max(0.18, float((cfg or {}).get("subtitle_persist_gap_seconds", 0.55)))

# In _stabilize_subtitle_timeline():
persist_gap = _subtitle_persist_gap_seconds(cfg)  # 0.55s
clear_gap = float(cfg.get("subtitle_clear_gap_seconds", max(persist_gap * 2.5, 4.0)))
blink_threshold = _subtitle_gap_blink_threshold_seconds(cfg)  # 0.18s

# Logic:
if gap <= persist_gap:
    # Bridge gap (extend previous subtitle end time)
elif gap >= clear_gap:
    # Clear subtitle (leave gap)
else:
    # Gap blink zone (flicker risk)
    gap_blink_count += 1
```

---

### 2. No Hard "Hold Until Next" Rule

**Current logic** (subtitle.py:~700-800):
- Bridges gaps < persist_gap
- BUT doesn't enforce "hold until next subtitle enters"
- Relies on threshold alone

**Missing**:
```python
if next_subtitle_exists and gap < 0.9s:
    force_bridge()  # HARD RULE: never empty visual gap
```

---

### 3. Timeline Remap After Trim

**Problem**:
- Silence trimming removes segments
- Subtitle timestamps remapped AFTER cut
- May create NEW gaps where none existed

**Current flow**:
1. Generate subtitles (original timeline)
2. Apply silence cuts (removes video segments)
3. Remap subtitles to new timeline
4. Result: Gaps may shift, bridges may break

**Ideal flow**:
1. Apply silence cuts
2. Build final video timeline
3. Generate subtitles on FINAL timeline
4. No remapping needed

---

## IMPLEMENTATION PLAN

### STEP 1: Raise Persistence Thresholds (PHASE 2 CARRYOVER)

**File**: `pipeline/config.py`

**Lines 230-231**:

**Current**:
```python
"subtitle_persist_gap_seconds": 0.55,
"subtitle_clear_gap_seconds": 1.35,
```

**New**:
```python
"subtitle_persist_gap_seconds": 0.85,  # Raise from 0.55
"subtitle_clear_gap_seconds": 1.80,    # Raise from 1.35
```

**Rationale**:
- 0.85s covers most natural pauses (< 1.0s)
- 1.80s prevents overly long stale subtitles
- Gap blink zone: 0.85-1.80s (wider, but natural pauses < 1.0s mostly covered)

**Also update validation ranges** (lines 239-246):

**Current**:
```python
merged["subtitle_persist_gap_seconds"] = max(
    0.18,
    min(float(merged.get("subtitle_persist_gap_seconds", 0.55)), 0.85),  # ⚠️ Ceiling already 0.85
)

merged["subtitle_clear_gap_seconds"] = max(
    merged["subtitle_persist_gap_seconds"],
    min(float(merged.get("subtitle_clear_gap_seconds", 1.35)), 2.0),  # ⚠️ Ceiling 2.0
)
```

**Already correct ceiling values** — just change defaults

---

### STEP 2: Enforce "Hold Until Next" Semantics

**File**: `pipeline/subtitle.py`

**Location**: `_stabilize_subtitle_timeline()` function (~line 700-800)

**Current logic**:
```python
def _stabilize_subtitle_timeline(events, cfg):
    persist_gap = _subtitle_persist_gap_seconds(cfg)
    clear_gap = cfg.get("subtitle_clear_gap_seconds", max(persist_gap * 2.5, 4.0))
    
    for i, current in enumerate(events):
        if i > 0:
            prev = events[i - 1]
            gap = current_start - float(prev["end"])
            
            if gap > 0.0 and gap <= persist_gap:
                # Bridge gap
                prev["end"] = current_start
                persisted_gaps_count += 1
```

**Add HARD RULE**:
```python
def _stabilize_subtitle_timeline(events, cfg):
    persist_gap = _subtitle_persist_gap_seconds(cfg)
    clear_gap = cfg.get("subtitle_clear_gap_seconds", max(persist_gap * 2.5, 4.0))
    
    # ✅ NEW: Hard hold-until-next threshold
    hold_until_next_max = 0.90  # Never allow gap < 900ms if next exists
    
    for i, current in enumerate(events):
        if i > 0:
            prev = events[i - 1]
            gap = current_start - float(prev["end"])
            
            if gap > 0.0:
                # PRIORITY 1: Hard hold-until-next rule
                if gap <= hold_until_next_max:
                    # Force bridge (no flicker allowed)
                    prev["end"] = current_start
                    persisted_gaps_count += 1
                
                # PRIORITY 2: Normal persist_gap logic
                elif gap <= persist_gap:
                    # Bridge gap
                    prev["end"] = current_start
                    persisted_gaps_count += 1
                
                # PRIORITY 3: Gap blink detection
                elif gap < clear_gap:
                    # Gap blink zone (still visible but not bridged)
                    if gap <= blink_threshold:
                        gap_blink_count += 1
```

**Lines to modify**: ~20 lines

**Rationale**:
- 900ms = max natural micro-pause
- If next subtitle exists within 900ms → ALWAYS bridge
- Overrides normal persist_gap logic
- Guarantees no visual gap < 900ms

---

### STEP 3: Add Subtitle Frame Buffer

**File**: `pipeline/subtitle.py`

**Location**: `build_ass_word_events()` function (~line 900-1100)

**Concept**: Persistent subtitle frame buffer

**Current**:
- Each subtitle event has discrete [start, end]
- Gap between events = no subtitle visible

**New**:
- Add frame buffer: extend end time by small epsilon (50-100ms)
- Next subtitle start overlaps with buffer
- Result: Smooth transition, no empty frames

**Implementation**:
```python
def build_ass_word_events(segments, cfg=None):
    # ... existing code ...
    
    # ✅ NEW: Frame buffer for smooth transitions
    frame_buffer_ms = 80  # 80ms overlap for smooth transition
    
    for i, event in enumerate(events):
        # Extend end time by buffer (unless it overlaps next subtitle start)
        if i + 1 < len(events):
            next_start = float(events[i + 1]["start"])
            buffer_end = float(event["end"]) + (frame_buffer_ms / 1000.0)
            
            # Only extend if it doesn't push past next subtitle
            if buffer_end <= next_start:
                event["end"] = buffer_end
```

**Lines to add**: ~15 lines

**Benefit**: Eliminates micro-gaps between subtitles

---

### STEP 4: Verify Timeline Remap Order

**File**: `pipeline/highlight.py`

**Location**: Montage assembly flow (~line 10000-11000)

**Required order**:
```
1. Build candidate (video segment)
2. Apply silence cuts (trim video)
3. Generate FINAL timeline
4. Remap subtitles to FINAL timeline  ← MUST BE AFTER CUTS
5. Render subtitles (ASS file)
```

**Check current flow**:
```python
# Search for:
# - silence trimming invocation
# - subtitle remap invocation
# - verify order
```

**If wrong order**: Reorder pipeline calls

**Validation**:
- Subtitles remapped AFTER final montage cut
- No timing drift
- No unexpected gaps

---

## VALIDATION CRITERIA

### Success Metrics

**1. No Gap Blink in Normal Dialogue**
- Before: `gap_blink_count > 0` frequently
- After: `gap_blink_count = 0` for most clips

**2. Subtitles Persist Across Natural Pauses**
- Pauses 0.6-0.8s → subtitle stays visible
- Before: Flickers
- After: Stays visible

**3. Clear on Intentional Long Pauses**
- Pauses > 1.8s → subtitle clears
- Scene changes, dramatic pauses
- Subtitle properly removed

**4. No Timeline Drift**
- Long clips (10+ min) → subtitles align throughout
- Before: Desync after 5-10min
- After: Perfect alignment

**5. Smooth Transitions**
- No flicker between adjacent subtitles
- Frame buffer prevents micro-gaps

---

## TESTING PLAN

### Test Case 1: Natural Dialogue Pauses
**Input**: Dialogue with 0.7-0.9s pauses (comedic timing)
**Expected**: Subtitle persists (no flicker)

### Test Case 2: Long Silence
**Input**: 3s silence between sentences
**Expected**: Subtitle clears after 1.8s

### Test Case 3: Long Clip
**Input**: 15min episode, heavy silence trimming
**Expected**: Subtitles align at start, middle, end (no drift)

### Test Case 4: Rapid Dialogue
**Input**: Fast-paced exchange, 0.2-0.4s gaps
**Expected**: Subtitles persist continuously, smooth transitions

### Test Case 5: Frame Buffer
**Input**: Adjacent subtitles with 60ms gap
**Expected**: No visible gap (frame buffer fills it)

---

## IMPLEMENTATION CHECKLIST

### Pre-Flight
- [ ] Backup config.py, subtitle.py
- [ ] Document current gap_blink_count baseline
- [ ] Prepare test episodes with varied pause patterns

### Step 1: Raise Thresholds
- [ ] Update subtitle_persist_gap_seconds: 0.55 → 0.85
- [ ] Update subtitle_clear_gap_seconds: 1.35 → 1.80
- [ ] Verify validation ranges (already correct)
- [ ] Test: Load config, verify new values

### Step 2: Hold-Until-Next Rule
- [ ] Add hold_until_next_max = 0.90
- [ ] Implement PRIORITY 1 logic (force bridge < 900ms)
- [ ] Test: Verify gaps < 900ms always bridged
- [ ] Test: Verify gap_blink_count reduced

### Step 3: Frame Buffer
- [ ] Add frame_buffer_ms = 80
- [ ] Extend subtitle end times (with overlap check)
- [ ] Test: Verify no micro-gaps between subtitles
- [ ] Test: Visual smoothness check

### Step 4: Remap Order Validation
- [ ] Locate silence trimming code
- [ ] Locate subtitle remap code
- [ ] Verify: remap happens AFTER cuts
- [ ] If wrong: Reorder pipeline calls
- [ ] Test: Long clip, verify no drift

### Validation
- [ ] Run Test Case 1 (natural pauses)
- [ ] Run Test Case 2 (long silence)
- [ ] Run Test Case 3 (long clip)
- [ ] Run Test Case 4 (rapid dialogue)
- [ ] Run Test Case 5 (frame buffer)
- [ ] Metrics: gap_blink_count = 0 or near-zero
- [ ] Visual: No subtitle flicker

---

## ROLLBACK PLAN

### If Subtitles Overlap
**Symptom**: Multiple subtitle lines visible simultaneously

**Action**:
1. Check frame_buffer_ms value (reduce to 50ms)
2. Verify hold_until_next logic doesn't force overlaps
3. Add overlap detection validation

### If Subtitles Stay Too Long
**Symptom**: Stale subtitles visible during silence

**Action**:
1. Check clear_gap threshold (lower from 1.80s if needed)
2. Verify long silences properly detected
3. Add max_hold_time limit (e.g., 2.5s absolute)

### If Timeline Drift Persists
**Symptom**: Subtitles desync on long clips

**Action**:
1. Verify remap happens after ALL cuts
2. Check cumulative shift calculation in remap logic
3. Add validation: total duration = original - sum(removed)

---

## ESTIMATED EFFORT

**Total**: 1 day

**Breakdown**:
- Step 1: 15 minutes (config changes)
- Step 2: 2 hours (hold-until-next logic)
- Step 3: 1 hour (frame buffer)
- Step 4: 2 hours (remap order validation)
- Validation: 2 hours

**Complexity**: LOW
- Mostly threshold tuning
- Clean logic additions
- Low risk

---

## BENEFITS

1. ✅ **No flicker on natural pauses** — subtitles stay visible < 1.0s gaps
2. ✅ **Smooth transitions** — frame buffer eliminates micro-gaps
3. ✅ **Deterministic** — hold-until-next is hard rule, not heuristic
4. ✅ **Better UX** — viewers don't lose context during pauses
5. ✅ **Timeline stable** — remap order ensures no drift

---

## CONFIGURATION SUMMARY

### Final Config Values (config.py)

```python
# Subtitle persistence (PHASE 3 values)
"subtitle_persist_gap_seconds": 0.85,   # Was 0.55
"subtitle_clear_gap_seconds": 1.80,     # Was 1.35
"subtitle_continuity_mode": "always_on_short_gaps",  # Unchanged
"subtitle_renderer_mode": "persistent_sentence_layer",  # Unchanged
"subtitle_gap_blink_threshold_ms": 180,  # Unchanged
```

### Gap Zones (NEW)

```
Gap < 0.85s    → BRIDGE (keep visible) ✅
Gap 0.85-1.80s → GAP BLINK (rare, mostly > 1.0s) ⚠️
Gap >= 1.80s   → CLEAR (subtitle off) ✅

HARD RULE: Gap < 0.90s → FORCE BRIDGE (if next exists)
```

---

## CONCLUSION

Subtitle persistence enforcement eliminates flicker through:
1. **Higher thresholds** (0.85s covers natural pauses)
2. **Hard hold-until-next rule** (< 900ms always bridged)
3. **Frame buffer** (80ms overlap for smoothness)
4. **Timeline order validation** (remap after cuts)

**Key principle**: Subtitle visibility = SOURCE OF TRUTH, gaps < 900ms = never allowed.

**Ready for execution**: Steps clear, validation defined, rollback safe.
