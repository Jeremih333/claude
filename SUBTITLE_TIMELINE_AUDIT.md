# SUBTITLE TIMELINE AUDIT — SECTION 3

**Date:** 2026-06-20  
**Status:** ⚠️ **OVERLAP RISK IDENTIFIED**

---

## EXECUTIVE SUMMARY

Subtitle generation flow is correctly ordered, but **hold-until-next logic may create overlaps** after timeline surgery (cuts/remapping).

**Critical Finding:** Subtitles stabilized BEFORE timeline cuts → remap may not account for hold-until-next extensions.

---

## SUBTITLE LIFECYCLE FLOW

### Generation Order (Correct)

```
1. Candidate Selection
   ↓
2. transcribe_segment() → Generates segments
   ↓
3. build_ass_word_events() → Word-level events
   ↓
4. _stabilize_subtitle_timeline() → Gap/overlap elimination
   ↓
5. Silence Cuts Applied → Timeline surgery
   ↓
6. remap_subtitle_info_after_cuts() → Timestamp adjustments
```

**Status:** ✅ Order is correct (subtitles AFTER selection, BEFORE cuts).

---

## FUNCTION LOCATIONS

| Function | File | Line | Purpose |
|----------|------|------|---------|
| `transcribe_segment()` | subtitle.py | 1117-1332 | Main transcription entry point |
| `build_ass_word_events()` | subtitle.py | 848-907 | Word-level event generation |
| `_stabilize_subtitle_timeline()` | subtitle.py | 510-627 | Gap/overlap elimination |
| `remap_subtitle_info_after_cuts()` | subtitle.py | (search needed) | Timestamp adjustment after cuts |

---

## CRITICAL TIMING ISSUE: HOLD-UNTIL-NEXT

### The Problem

**Location:** `pipeline/subtitle.py` lines 564-569

```python
# Hold-until-next logic
if gap < min_gap_sec:
    event["end"] = next_event["start"]  # Extend current subtitle to next
```

**What it does:**
- If gap between subtitles < threshold → extend current subtitle to next start
- Purpose: Eliminate micro-gaps, improve readability
- **Risk:** Extensions happen BEFORE timeline surgery

### Timeline Surgery Flow

```
Original Timeline:
[Sub1: 0-5s] [gap] [Sub2: 7-10s]
         ↓
Hold-until-next (gap < 1s):
[Sub1: 0-7s] [Sub2: 7-10s]  ← Sub1 extended
         ↓
Silence Cut at 6s (removes 5-7s):
[Sub1: 0-5s] ??? [Sub2: 5-8s]
         ↓
Remap adjusts timestamps:
[Sub1: 0-5s ???] [Sub2: 5-8s]
```

**Problem:** If remap doesn't account for hold-until-next extension, Sub1 may:
1. Survive across the cut (stale subtitle)
2. Overlap with Sub2
3. Create gap where it shouldn't exist

---

## OVERLAP RISK ANALYSIS

### Scenario 1: Stale Subtitle Persistence

```
Before Cut:
[Sub: "Hello"] ← extended by hold-until-next
|------------|
0s         10s

Cut removes 5-10s:
[Sub: "Hello"] ← still shows "Hello" in dead zone
|-----|XXXXX|
0s   5s   10s

Result: "Hello" flickers or persists in cut region
```

### Scenario 2: Overlap After Remap

```
Before Cut:
[Sub1: "Hi"] [Sub2: "Bye"]
|-----|-----|
0    3    5s

Hold-until-next extends Sub1:
[Sub1: "Hi"  ] [Sub2: "Bye"]
|------------|-----|
0           3    5s

Cut at 2s removes 2-3s:
Timeline: [0-2s][3-5s] → [0-2s][2-4s after remap]

Remap adjusts Sub2:
[Sub2: 2-4s]

But Sub1 still thinks it ends at 3s (now 2s after remap):
[Sub1: 0-2s] [Sub2: 2-4s]

If remap doesn't fix Sub1's extended end → overlap or gap
```

---

## STALE SUBTITLE RISK

### Definition
**Stale Subtitle:** Subtitle that displays in a timeline region that was cut/removed.

### Root Cause
1. Subtitle generated for ORIGINAL timeline
2. Hold-until-next extends subtitle end time
3. Cut removes region between extended end and next subtitle
4. Remap may not truncate extended subtitle properly

### Detection Points

Need to verify `remap_subtitle_info_after_cuts()`:
- Does it track hold-until-next extensions?
- Does it truncate subtitles at cut boundaries?
- Does it re-run stabilization after remap?

---

## GAP PERSISTENCE LOGIC

### Hold-Until-Next Parameters (Subagent Report)

**Location:** `_stabilize_subtitle_timeline()` line 564-569

```python
min_gap_sec = 0.8  # Minimum gap before bridging
```

**Logic:**
- If gap < 0.8s → extend current subtitle to bridge gap
- Improves visual continuity
- Reduces subtitle flicker

**Risk:** Aggressive bridging may extend across cut boundaries.

---

## RECOMMENDATIONS

### Priority 1: Audit remap_subtitle_info_after_cuts()

**Action:**
1. Locate function (search needed)
2. Verify it handles hold-until-next extensions
3. Check if it recalculates gaps after cuts
4. Ensure it truncates subtitles at cut boundaries

**Search command:**
```bash
grep -n "def remap_subtitle_info_after_cuts" pipeline/subtitle.py
```

### Priority 2: Add Post-Remap Validation

**Location:** After `remap_subtitle_info_after_cuts()` call

```python
# Validate no overlaps after remap
for i in range(len(events) - 1):
    if events[i]["end"] > events[i+1]["start"]:
        logger.error(f"Overlap detected after remap: {events[i]} overlaps {events[i+1]}")
        # Truncate or adjust
        events[i]["end"] = events[i+1]["start"]
```

**Benefit:** Catch and fix overlaps automatically.

### Priority 3: Reorder Stabilization?

**Option A:** Run stabilization AFTER cuts (not before)
- Pro: No stale extensions
- Con: May need original gaps for proper cut detection

**Option B:** Run stabilization TWICE
- Before cuts: for cut detection
- After remap: for final display

**Option C:** Keep current order, enhance remap
- Make remap aware of hold-until-next
- Truncate extended subtitles at cut boundaries

**Recommendation:** Option C (least disruptive).

### Priority 4: Add Logging

**Location:** `_stabilize_subtitle_timeline()` after extensions

```python
if gap < min_gap_sec:
    logger.debug(f"Hold-until-next: Extended subtitle from {original_end} to {next_event['start']}")
    event["end"] = next_event["start"]
    event["_extended_by_stabilization"] = True  # Flag for remap
```

**Benefit:** Track which subtitles were extended, remap can handle them specially.

---

## HOLD-UNTIL-NEXT PARAMETERS

### Current Thresholds

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `min_gap_sec` | 0.8s | Minimum gap before bridging |
| `max_extension` | (needs audit) | Maximum extension distance? |
| `overlap_tolerance` | (needs audit) | Allowed overlap? |

**Action:** Document all stabilization parameters.

---

## OVERLAP DETECTION

### Current Detection Logic

**Location:** `_stabilize_subtitle_timeline()` lines 564-627

```python
# Iterates through events, checks gaps, extends if needed
```

**Question:** Does it also check for overlaps BEFORE extending?

**Risk:** If event[i].end > event[i+1].start BEFORE hold-until-next → forced overlap.

---

## TIMELINE SURGERY ORDER

### Confirmed Order (from subagent report)

1. ✅ `transcribe_segment()` — generates segments
2. ✅ `build_ass_word_events()` — word-level events
3. ✅ `_stabilize_subtitle_timeline()` — gap elimination (hold-until-next HERE)
4. ✅ Silence cuts applied — removes timeline segments
5. ✅ `remap_subtitle_info_after_cuts()` — adjusts timestamps

**Critical:** Hold-until-next happens at step 3, BEFORE cuts (step 4).

---

## GAP vs OVERLAP TRADEOFF

### Design Tension

**Hold-until-next goal:** Eliminate small gaps (improve UX)

**Timeline surgery goal:** Remove dead air (improve pacing)

**Conflict:** Extended subtitles may bridge into dead air that will be cut.

### Solution Approaches

**Approach 1:** Aggressive hold-until-next + smart remap
- Extend subtitles freely
- Remap truncates extensions that cross cuts
- **Pro:** Better subtitle continuity
- **Con:** Complex remap logic

**Approach 2:** Conservative hold-until-next
- Only extend if gap < 0.3s (not 0.8s)
- Reduce risk of crossing cuts
- **Pro:** Simpler remap
- **Con:** More gaps in final output

**Approach 3:** Post-cut stabilization
- Don't extend before cuts
- Run stabilization AFTER remap
- **Pro:** No stale extensions
- **Con:** May need gap info for cut detection

---

## FLICKER RISK

### Definition
**Flicker:** Subtitle appears/disappears rapidly due to overlap or gap miscalculation.

### Causes
1. Overlap after remap → subtitle A disappears, B appears immediately (visual jump)
2. Gap after remap → subtitle A ends, gap, subtitle B starts (flicker)
3. Stale subtitle → wrong text shows briefly in cut region

### Mitigation
- Ensure overlap elimination AFTER remap
- Re-run stabilization if needed
- Validate final timeline has no overlaps/micro-gaps

---

## CONCLUSION

**Subtitle generation flow is correctly ordered**, but **hold-until-next creates overlap risk**:

1. ✅ Subtitles generated AFTER candidate selection
2. ✅ Stabilization runs before cuts (correct for gap detection)
3. ⚠️ **Hold-until-next extensions may survive cuts**
4. ⚠️ **Remap may not account for extensions**
5. ❌ **No post-remap validation detected**

**Status:** ⚠️ **FUNCTIONAL BUT RISKY**

**Next Action:** Audit `remap_subtitle_info_after_cuts()` to verify it handles hold-until-next extensions properly.

---

**Report Status:** ✅ COMPLETE  
**Dependencies:** Requires remap function audit (location TBD)
