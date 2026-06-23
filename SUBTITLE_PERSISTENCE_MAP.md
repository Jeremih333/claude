# SUBTITLE_PERSISTENCE_MAP.md
**PHASE 2 ROOT CAUSE RECOVERY — Gap/Drift/Desync Analysis**

---

## PROBLEM STATEMENT

**Observed Issues**:
1. Subtitles disappear during mid-sentence pauses (0.6-1.2s gaps)
2. Timeline drift after silence trimming
3. Subtitle desync on long clips (5-10s removed segments)
4. Word events flicker between sentence boundaries

---

## ROOT CAUSE #1: persist_gap TOO SHORT (0.55s)

### Current Config

**Location**: `config.py:230`
```python
"subtitle_persist_gap_seconds": 0.55,
"subtitle_clear_gap_seconds": 1.35,
```

### Problem

Natural dialogue often has pauses **0.6-1.2s**:
- Speaker thinking / reaction
- Comedic timing
- Emphasis pause

**Current logic**:
```python
if gap < persist_gap (0.55s):
    # Bridge subtitle (keep visible)
elif gap >= clear_gap (1.35s):
    # Clear subtitle
else:
    # Gap blink (flicker) ← PROBLEM ZONE: 0.55-1.35s
```

**Result**: Pauses 0.6-1.2s fall in "gap blink" zone → subtitle flickers

---

### Solution

**Raise thresholds**:
```python
"subtitle_persist_gap_seconds": 0.85,  # Was 0.55
"subtitle_clear_gap_seconds": 1.80,    # Was 1.35
```

**Rationale**:
- 0.85s covers most natural pauses (< 1.0s)
- 1.80s clear_gap prevents overly long stale subtitles
- Gap blink zone: 0.85-1.80s (larger, but less frequent)

**Alternative**: Tie to conversation grouping
```python
# Use same gap threshold as story_max_gap_seconds
persist_gap = min(0.85, cfg.get("story_max_gap_seconds", 2.0) * 0.45)
```

---

## ROOT CAUSE #2: TIMELINE REMAP AFTER TRIM

### Current Flow

**Location**: `subtitle_pipeline.py:17`
```python
def remap_subtitles_after_cuts(
    subtitle_info: dict,
    removed_segments: list[tuple[float, float]],
    ...
):
    """Remap subtitle timestamps after silence trimming."""
```

### Problem

**Silence trimming removes segments**:
```
Original timeline:
0s ─── 10s [SPEECH] ─── 15s [5s SILENCE REMOVED] ─── 20s [SPEECH] ─── 30s

After trim:
0s ─── 10s [SPEECH] ─── 15s [SPEECH continues] ─── 25s
                           ↑
                    Original 20s → now 15s
                    5s shift applied
```

**Remap logic shifts all timestamps**:
- If removes [15.0, 20.0] → all events after 15.0 shift by -5.0s
- **Problem**: Old persist_gap bridges no longer align
- Events created for [20.0-22.0] now [15.0-17.0] → may create NEW gaps

---

### Solution

**Option A: Rebuild ASS events after trim** (PREFERRED)
```python
# Instead of remapping old events:
# 1. Apply silence cuts to video
# 2. Get NEW segment timings from trimmed video
# 3. Rebuild ASS events from scratch with new timings

# Pros: Clean, no drift accumulation
# Cons: Slightly slower (re-runs build_ass_word_events)
```

**Option B: Validate remap logic**
```python
# Current remap may have off-by-one errors
# Audit timeline surgery logic:
def remap_subtitles_after_cuts(subtitle_info, removed_segments, ...):
    # For each removed segment [cut_start, cut_end]:
    #   shift = cut_end - cut_start
    #   for each event after cut_end:
    #       event.start -= shift
    #       event.end -= shift
    
    # VALIDATE: No negative timestamps, no overlaps, no gaps > persist_gap
```

**Recommendation**: Option A (rebuild) for reliability

---

## ROOT CAUSE #3: WORD-LEVEL GRANULARITY

### Current Rendering Mode

**Location**: `subtitle.py:build_ass_word_events()`
```python
display_mode = str(cfg.get("subtitle_display_mode", "sentence_highlight"))
renderer_mode = str(cfg.get("subtitle_renderer_mode", "persistent_sentence_layer"))
```

**Modes**:
- `word_highlight`: Each word gets separate event
- `sentence_highlight`: Sentence-level events ✅ CURRENT
- `persistent_sentence_layer`: Sentence + persistence ✅ CURRENT

### Problem

**Word-level mode** (if enabled):
```
Word events:
[12.0-12.3] "Hello"
[12.3-12.6] "how"
[12.6-13.0] "are"
[13.0-13.4] "you"

Gaps between words:
12.3→12.3 (0.0s)
12.6→12.6 (0.0s)
13.0→13.0 (0.0s)

If word end ≠ next word start → gap!
If gap > persist_gap (0.55s) → subtitle clears
```

**Result**: Flicker between words

---

### Solution

**Already correct**: Config uses `persistent_sentence_layer`
```python
"subtitle_renderer_mode": "persistent_sentence_layer",  # ✅ GOOD
```

**If word-level needed**: Raise persist_gap OR force sentence mode

---

## ROOT CAUSE #4: DESYNC ON LONG CUTS

### Scenario

**Episode**: 40min, silence trimming removes 120s total
**Multiple cuts**:
- Cut #1: [100-105] → 5s removed
- Cut #2: [200-210] → 10s removed
- Cut #3: [500-520] → 20s removed
- ...

**Cumulative shift**: Events at t=2400s → shift by -120s → now t=2280s

**Problem**: If ANY remap has off-by-one error → cascade through timeline

**Example**:
```python
# Buggy remap:
for seg in removed_segments:
    cut_start, cut_end = seg
    shift = cut_end - cut_start
    
    for event in events:
        if event.start >= cut_end:  # ❌ Should be > not >=
            event.start -= shift
```

**If cut_end = 100.0 and event.start = 100.0**:
- Buggy: shifts event (wrong — event at boundary should NOT shift)
- Correct: doesn't shift event

---

### Solution

**Audit remap logic** in `subtitle_pipeline.py`:
```python
def remap_subtitles_after_cuts(subtitle_info, removed_segments, ...):
    # Sort removed segments by start time
    removed_segments = sorted(removed_segments, key=lambda x: x[0])
    
    # Calculate cumulative shift
    cumulative_shift = 0.0
    shift_breakpoints = []
    
    for cut_start, cut_end in removed_segments:
        shift_breakpoints.append((cut_end, cumulative_shift))
        cumulative_shift += (cut_end - cut_start)
    
    # Apply shifts
    for segment in subtitle_info.get("segments", []):
        seg_start = float(segment.get("start", 0.0))
        seg_end = float(segment.get("end", 0.0))
        
        # Find applicable shift
        shift = 0.0
        for breakpoint, breakpoint_shift in shift_breakpoints:
            if seg_start > breakpoint:  # ✅ Strict >, not >=
                shift = breakpoint_shift
        
        segment["start"] = max(0.0, seg_start - shift)
        segment["end"] = max(0.0, seg_end - shift)
```

**Validation**:
- No negative timestamps
- Event order preserved
- No overlaps
- Total duration = original - sum(removed durations)

---

## ROOT CAUSE #5: TIMELINE STABILIZATION LOGIC

### Current Stabilization

**Location**: `subtitle.py:_stabilize_subtitle_timeline()`

```python
def _stabilize_subtitle_timeline(events, cfg):
    """
    Bridge short gaps, clear long gaps, detect flicker.
    
    Logic:
    - If gap < persist_gap → bridge (extend prev event end time)
    - If gap >= clear_gap → clear (leave gap)
    - Between persist_gap and clear_gap → "gap blink" (flicker risk)
    """
    persist_gap = _subtitle_persist_gap_seconds(cfg)  # 0.55s
    clear_gap = cfg.get("subtitle_clear_gap_seconds", 1.35)
    
    # ... bridging logic
```

**Returns**:
```python
{
    "subtitle_event_overlap_count": int,
    "subtitle_persisted_gaps_count": int,
    "subtitle_gap_blink_count": int,
}
```

### Problem

**Gap blink zone**: 0.55-1.35s
- Natural dialogue pauses often fall here
- Result: subtitle flicker

---

### Solution

**Raise thresholds** (as above):
```python
persist_gap = 0.85  # Was 0.55
clear_gap = 1.80    # Was 1.35
```

**Gap blink zone**: 0.85-1.80s (wider, but natural pauses < 1.0s mostly covered)

---

## CONFIGURATION ANALYSIS

### Current Settings (config.py)

```python
# Subtitle rendering
"subtitle_renderer_mode": "persistent_sentence_layer",  # ✅ GOOD
"subtitle_continuity_mode": "always_on_short_gaps",     # ✅ GOOD
"subtitle_persist_gap_seconds": 0.55,                   # ⚠️ TOO LOW
"subtitle_clear_gap_seconds": 1.35,                     # ⚠️ BORDERLINE
"subtitle_hide_when_silent": True,                      # ✅ GOOD
```

### Recommended Changes

```python
"subtitle_persist_gap_seconds": 0.85,   # Raise from 0.55
"subtitle_clear_gap_seconds": 1.80,     # Raise from 1.35
```

**Validation range** (config.py:239-246):
```python
merged["subtitle_persist_gap_seconds"] = max(
    0.18,  # Floor
    min(
        float(merged.get("subtitle_persist_gap_seconds", 0.55)),
        0.85   # ✅ NEW CEILING (was unbounded)
    ),
)

merged["subtitle_clear_gap_seconds"] = max(
    merged["subtitle_persist_gap_seconds"],  # Must be >= persist_gap
    min(
        float(merged.get("subtitle_clear_gap_seconds", 1.35)),
        2.0  # ✅ NEW CEILING
    ),
)
```

---

## RELATIONSHIP TO STORY PIPELINE

### Story Grouping Uses Similar Gap Logic

**Location**: `story_pipeline.py:91`
```python
max_gap = float(cfg.get("story_max_gap_seconds", 2.0))
```

**Location**: `conversation_grouper.py:103`
```python
def group_conversations(turns, max_gap_seconds=2.0, ...):
    # Split conversations on gap > max_gap
```

### Alignment Opportunity

**Proposal**: Use same gap threshold family
```python
# Base gap: conversation splitting
base_gap = cfg.get("story_max_gap_seconds", 2.0)  # 2.0s

# Subtitle persist: 40-45% of base
subtitle_persist = base_gap * 0.42  # 0.84s (≈ 0.85s)

# Subtitle clear: 80-90% of base
subtitle_clear = base_gap * 0.90   # 1.80s
```

**Benefit**: Single tuning parameter affects both story grouping AND subtitle persistence

---

## IMPLEMENTATION PLAN

### PHASE 2.3.1: Raise Persistence Thresholds

**File**: `config.py`

**Change**:
```python
# Line 230
"subtitle_persist_gap_seconds": 0.85,  # Was 0.55
"subtitle_clear_gap_seconds": 1.80,    # Was 1.35
```

**Validation**:
```python
# Lines 239-246
merged["subtitle_persist_gap_seconds"] = max(
    0.18,
    min(float(merged.get("subtitle_persist_gap_seconds", 0.85)), 0.85),
)

merged["subtitle_clear_gap_seconds"] = max(
    merged["subtitle_persist_gap_seconds"],
    min(float(merged.get("subtitle_clear_gap_seconds", 1.80)), 2.0),
)
```

**Time**: 15 minutes  
**Risk**: Low — config change only

---

### PHASE 2.3.2: Audit Timeline Remap Logic (OPTIONAL)

**File**: `subtitle_pipeline.py`

**Review**: `remap_subtitles_after_cuts()`
- Validate boundary conditions (strict `>` not `>=`)
- Check cumulative shift calculation
- Add validation: no negative timestamps, no overlaps

**Time**: 1-2 hours  
**Risk**: Medium — complex timeline surgery

**Decision**: DEFER to validation phase — if desync persists after threshold raise, audit remap

---

### PHASE 2.3.3: Option to Rebuild ASS (FUTURE)

**File**: `subtitle_pipeline.py` + `highlight.py`

**Implementation**:
```python
def rebuild_ass_after_trim(
    trimmed_video_path,
    original_subtitle_info,
    cfg
):
    """
    Instead of remapping, rebuild ASS events from trimmed video.
    
    1. Re-transcribe trimmed segments (use cache if available)
    2. Build ASS events with new timestamps
    3. Return fresh subtitle_info
    """
    # ... implementation
```

**Time**: 1 day  
**Risk**: Medium — new code path

**Decision**: FUTURE enhancement — only if remap audit doesn't fix desync

---

## VALIDATION CRITERIA

### Success Metrics

1. ✅ **Subtitle persists across natural pauses < 1.0s**
   - Before: Flickers at 0.6-0.8s pauses
   - After: Stays visible

2. ✅ **No gap blink in normal dialogue**
   - Before: "gap_blink_count" > 0 frequently
   - After: "gap_blink_count" = 0 for most clips

3. ✅ **No timeline drift on long clips**
   - Before: Desync after 5-10min
   - After: Subtitles align throughout

4. ✅ **Clear on intentional long pauses > 1.8s**
   - Scene changes, dramatic pauses → subtitle clears

---

## TESTING PLAN

### Test Case 1: Natural Dialogue Pauses
**Input**: Dialogue with 0.7-0.9s pauses (comedic timing)
**Expected**: Subtitle persists (no flicker)

### Test Case 2: Long Silence
**Input**: 3s silence between sentences
**Expected**: Subtitle clears

### Test Case 3: Long Clip (10+ min)
**Input**: 15min episode, heavy silence trimming
**Expected**: Subtitles align at start, middle, end (no drift)

### Test Case 4: Rapid Dialogue
**Input**: Fast-paced exchange, 0.2-0.4s gaps
**Expected**: Subtitles persist continuously

---

## SUMMARY

### Root Causes Identified

1. **persist_gap=0.55s too short** → flicker on natural pauses
2. **Timeline remap after trim** → potential drift accumulation
3. **Word-level events** → N/A (already using sentence mode)
4. **Remap boundary bugs** → potential off-by-one errors
5. **Gap blink zone too wide** → 0.55-1.35s catches too many natural pauses

### Priority Fixes

**HIGH PRIORITY** (PHASE 2.3.1):
- Raise `subtitle_persist_gap_seconds`: 0.55 → 0.85
- Raise `subtitle_clear_gap_seconds`: 1.35 → 1.80

**MEDIUM PRIORITY** (Validation):
- Audit remap logic if desync persists

**LOW PRIORITY** (Future):
- Rebuild ASS after trim (alternative to remap)

---

**CONCLUSION**: Subtitle persistence issues stem from too-short persist_gap (0.55s) catching natural dialogue pauses (0.6-1.2s). Fix: Raise threshold to 0.85s. Timeline remap may have issues but should be audited only if desync persists after threshold change.
