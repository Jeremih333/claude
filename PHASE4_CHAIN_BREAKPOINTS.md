# PHASE 4 — STORY CHAIN BREAKPOINTS AUDIT
**Date:** 2026-06-21  
**Status:** AUDIT COMPLETE — DO NOT MODIFY YET  

---

## EXECUTIVE SUMMARY

**Mission:** Identify why good multi-turn scenes fragment into isolated pieces.

**Finding:** **6 AGGRESSIVE BREAKPOINTS** + **4 HARD DURATION FLOORS** detected.

**Impact:** Dialogue payoffs cut before emotional resolution, narrative continuity destroyed.

---

## BREAKPOINT MAP

### BREAKPOINT 1: Conversation Gap Limit (CRITICAL ⚠️)

**File:** `pipeline/montage/conversation_grouper.py`  
**Context:** Groups dialogue turns into conversation blocks  
**Current Threshold:** `max_gap_seconds = 2.0` (line 104 in story_pipeline.py)

**Logic:**
```python
# story_pipeline.py:91
max_gap: float = float(cfg.get("story_max_gap_seconds", 2.0))

# conversation_grouper.py (inferred from usage)
# If gap between turns > max_gap_seconds:
#     Start new conversation block
#     Previous chain TERMINATES
```

**Impact:**
- Short conversational pauses (2.5s-3.5s) **BREAK CHAINS**
- Natural dramatic pauses **TERMINATE STORIES**
- Emotional beats with silence **FRAGMENT SCENES**

**False Negatives:**
- Speaker thinking pause → chain break
- Reaction shot pause → chain break  
- Dramatic tension silence → chain break

**Severity:** 🔴 **CRITICAL** — Most destructive breakpoint

**Recommendation:** Increase to 2.8-4.0s with semantic override

---

### BREAKPOINT 2: Minimum Duration Filter (CRITICAL ⚠️)

**File:** `pipeline/montage/story_pipeline.py`  
**Lines:** 142-143

```python
min_dur = min(35.0, min_seconds)
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]
```

**Hard Floor:** 35.0 seconds (from `target_story_min_seconds` config)

**Impact:**
- Chains 30-34s **DELETED** regardless of quality
- Short but complete story arcs **REJECTED**
- High-quality 28s scenes **LOST**

**False Negatives:**
- 32s complete punchline → rejected
- 29s emotional confession → rejected
- 33s reveal moment → rejected

**Severity:** 🔴 **CRITICAL** — Hard rejection, no appeal

**Recommendation:** Replace with weighted penalty, not hard reject

---

### BREAKPOINT 3: Payoff Extension Window Limit

**File:** `pipeline/montage/story_chain_builder.py`  
**Lines:** 814, 836-839

```python
def try_extend_chain_for_payoff(
    chain: StoryChain,
    all_blocks: list[dict],
    *,
    max_extension_seconds: float = 120.0,
) -> StoryChain:
    # ...
    for block in all_blocks or []:
        block_start = _as_float(block.get("start", float("inf")))
        
        # Block must begin after chain ends and within extension window
        if block_start < chain.end:
            continue
        if block_start > chain.end + max_extension_seconds:  # LINE 839
            continue
```

**Hard Limit:** 120.0 seconds (2 minutes)

**Impact:**
- Payoff beyond 120s from chain end **UNREACHABLE**
- Long narrative arcs **INCOMPLETE**
- Delayed resolutions **LOST**

**False Negatives:**
- Setup at 0:30, payoff at 2:35 (125s gap) → no match
- Conflict at 1:00, resolution at 3:05 (125s gap) → no match

**Severity:** ⚠️ **HIGH** — Limits narrative scope

**Recommendation:** Increase to 180s (3 minutes)

---

### BREAKPOINT 4: Payoff Extension Match Threshold (AGGRESSIVE)

**File:** `pipeline/montage/story_chain_builder.py`  
**Lines:** 852-858, 879

**Speaker Overlap Logic:**
```python
# Lines 852-858
if chain_speakers and block_speakers:
    speaker_overlap = len(chain_speakers & block_speakers) / max(
        len(chain_speakers), len(block_speakers), 1
    )
else:
    speaker_overlap = 0.0
```

**Topic Token Overlap Logic:**
```python
# Lines 872-877
if chain.topic_tokens and block_topic_tokens:
    topic_overlap = len(chain.topic_tokens & block_topic_tokens) / max(
        len(chain.topic_tokens), len(block_topic_tokens), 1
    )
else:
    topic_overlap = 0.0
```

**Match Condition (LINE 879):**
```python
if speaker_overlap < 0.4 and topic_overlap < 0.25:
    continue  # REJECT BLOCK
```

**Problem:** **BOTH conditions must fail** to reject.

**But:** Thresholds are too strict:
- `speaker_overlap >= 0.4` = 40% speaker match required
- `topic_overlap >= 0.25` = 25% topic token match required

**Impact:**
- Different speakers with same topic → may fail both
- Same speakers with topic shift → may fail both
- Semantic continuity without exact token match → fails

**False Negatives:**
- A tells story to B, C responds to B → speaker_overlap < 0.4
- Setup: "robbery", payoff: "police arrest" → topic tokens differ
- Emotional reaction without repeating keywords → topic_overlap < 0.25

**Severity:** ⚠️ **MEDIUM-HIGH** — Too strict for real dialogue

**Recommendation:** 
- Lower speaker_overlap to 0.30
- Lower topic_overlap to 0.18
- Add semantic similarity as third criterion

---

### BREAKPOINT 5: Complete Chain No Extension

**File:** `pipeline/montage/story_chain_builder.py`  
**Line:** 826

```python
def try_extend_chain_for_payoff(
    chain: StoryChain,
    all_blocks: list[dict],
    *,
    max_extension_seconds: float = 120.0,
) -> StoryChain:
    if chain.is_complete:  # LINE 826
        return chain  # IMMEDIATE RETURN, NO EXTENSION
```

**Logic:** If chain already has hook + setup + escalation + payoff → skip extension.

**Problem:** `is_complete` may be **FALSE POSITIVE** from weak fragment roles.

**Example Scenario:**
1. Fragment 1 (role="hook") → weak question
2. Fragment 2 (role="setup") → brief context
3. Fragment 3 (role="escalation") → minor conflict
4. Fragment 4 (role="payoff") → **INCOMPLETE RESOLUTION** (payoff_score=0.15)
5. `is_complete = True` (all 4 roles filled)
6. Extension **SKIPPED** even though payoff is weak

**Impact:**
- Weak payoffs **NOT IMPROVED**
- Better payoff in next block **IGNORED**
- False sense of completion

**Severity:** ⚠️ **MEDIUM** — Quality degradation

**Recommendation:** Check `payoff_score >= 0.40` before skipping extension

---

### BREAKPOINT 6: Emergency Fallback Filter

**File:** `pipeline/montage/story_pipeline.py`  
**Lines:** 145-147

```python
# If all chains were filtered out, fall back to keeping any non-empty chain
if not filtered and extended_chains:
    filtered = [c for c in extended_chains if c.fragments]
```

**Logic:** Only activates if **ALL chains rejected** by duration filter.

**Problem:** This is an **emergency escape hatch**, not a solution.

**Impact:**
- Most chains still lost to duration filter
- Emergency fallback rarely triggers
- **No proactive rescue** for near-threshold chains

**Severity:** 🟡 **LOW** — Passive safety net only

**Recommendation:** Add proactive rescue for chains 30-35s with high quality

---

## HARD DURATION FLOORS

### FLOOR 1: Story Pipeline Minimum (35s)

**File:** `pipeline/montage/story_pipeline.py`  
**Line:** 90, 142

```python
min_seconds: float = float(cfg.get("target_story_min_seconds", 35.0))
# ...
min_dur = min(35.0, min_seconds)
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]
```

**Hard Reject:** Chains < 35.0s **DELETED**

**Config Key:** `target_story_min_seconds` (default: 35.0)

**Impact:** SHORT BUT COMPLETE scenes rejected

---

### FLOOR 2: Legacy Builder Minimum (35s)

**File:** `pipeline/highlight.py`  
**Lines:** 5479, 5509, 5830, 5903, 6012

**Multiple Instances:**

1. **_candidate_windows_legacy (5479, 5509):**
```python
min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
# ...
if end - start < max(35.0, float(self.cfg.get("min_candidate_seconds", 35))):
    continue
```

2. **_build_story_candidates_from_turns_linear (5830, 5903):**
```python
min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
# ...
if duration >= max(35.0, float(self.cfg.get("min_candidate_seconds", 35))):
```

3. **_build_story_candidates_from_window (5675):**
```python
min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
```

4. **_fallback_window_candidate (6012):**
```python
if speech_density_value < 0.18 or duration < max(
    35.0, float(self.cfg.get("min_candidate_seconds", 35))
):
    return None
```

**Hard Reject:** Multiple 35s floors across ALL candidate builders

---

### FLOOR 3: Window Validation Minimum (35s)

**File:** `pipeline/highlight.py`  
**Line:** 5444

```python
# _candidate_windows_story_centric
min_duration = max(35.0, float(self.cfg.get("min_candidate_seconds", 35)))
if end - start < min_duration:
    continue
```

**Hard Reject:** Story-centric windows < 35s **FILTERED**

---

### FLOOR 4: Duration Policy Hard Max

**File:** `pipeline/highlight.py`  
**Lines:** Various in scoring functions

**Context:** `_candidate_duration_policy()` returns:
```python
{
    "hard_max_seconds": ...,
    "soft_target_seconds": ...,
    "duration_preference": ...
}
```

**Usage:** Referenced in multiple scoring functions

**Impact:** Enforces upper bounds (60s typical)

---

## CHAIN TERMINATION CONDITIONS

### TERMINATION 1: No Subtitle Data

**File:** `pipeline/highlight.py`  
**Line:** 5412-5414

```python
subtitle_info = getattr(self, 'subtitle_info', None)
if not subtitle_info or not subtitle_info.get('segments'):
    return self._candidate_windows_legacy(video_path)
```

**Trigger:** Missing or empty subtitle segments

**Result:** **IMMEDIATE FALLBACK** to legacy pipeline (story-centric disabled)

**Severity:** 🔴 **CRITICAL** — Entire story mode abandoned

---

### TERMINATION 2: Zero Story Chains

**File:** `pipeline/highlight.py`  
**Line:** 5423-5425

```python
if not story_chains:
    return self._candidate_windows_legacy(video_path)
```

**Trigger:** `build_story_chains_for_episode()` returns empty list

**Result:** **FALLBACK** to legacy pipeline

**Cascade Effect:** If story_max_gap too strict → no chains → legacy mode

---

### TERMINATION 3: No Valid Windows

**File:** `pipeline/highlight.py`  
**Line:** 5457-5458

```python
if not windows:
    return self._candidate_windows_legacy(video_path)
```

**Trigger:** All story chain windows filtered by duration/validation

**Result:** **FALLBACK** to legacy pipeline

---

### TERMINATION 4: Runtime Config Mutation

**File:** `pipeline/highlight.py`  
**Line:** 8408-8414

```python
if self.subtitle_info and self.subtitle_info.get('segments'):
    # ...
else:
    _emit(
        progress_callback,
        "warning",
        "Episode transcription returned no segments; falling back to legacy mode"
    )
    self.cfg["use_story_centric_pipeline"] = False  # PERMANENT DISABLE
```

**Trigger:** Empty transcription result

**Result:** **Config mutated** → story-centric disabled for entire episode

**Severity:** 🔴 **CRITICAL** — No recovery possible

---

## STORY COMPLETION SCORING

### Completion Score Formula

**File:** `pipeline/montage/story_chain_builder.py`  
**Line:** 659-662

```python
completion_score = 0.25 * sum(
    1 for part in (hook, setup, escalation, payoff) if part
)
is_complete = bool(hook and setup and escalation and payoff)
```

**Formula:** `completion_score = 0.25 × (filled_slots / 4)`

**Values:**
- 4/4 slots filled → `completion_score = 1.00`, `is_complete = True`
- 3/4 slots filled → `completion_score = 0.75`, `is_complete = False`
- 2/4 slots filled → `completion_score = 0.50`, `is_complete = False`
- 1/4 slots filled → `completion_score = 0.25`, `is_complete = False`

**Problem:** **Binary `is_complete` check** ignores quality of filled slots.

**Example:**
- Weak hook + weak setup + weak escalation + weak payoff = `is_complete = True`
- Strong hook + strong setup + strong escalation + NO payoff = `is_complete = False`

**Impact:** Quality not factored into completion

---

### Context Completeness Score

**File:** `pipeline/montage/story_chain_builder.py`  
**Line:** 753-759

```python
context_completeness_score = min(
    1.0,
    0.25 * (1 if characters else 0)
    + 0.25 * (1 if topic_terms else 0)
    + 0.25 * (1 if setup else 0)
    + 0.25 * (1 if escalation else 0),
)
```

**Formula:** Equally weighted presence checks (25% each)

**Problem:** Presence ≠ Quality

---

### Story Summary Confidence

**File:** `pipeline/montage/story_chain_builder.py`  
**Line:** 761-764

```python
confidence = min(
    1.0,
    0.25 + story_completion_score * 0.35 + context_completeness_score * 0.35,
)
```

**Formula:** `confidence = 0.25 + (completion × 0.35) + (context × 0.35)`

**Range:** 0.25 (minimum baseline) to 1.00

**Problem:** Low confidence chains (0.40-0.55) may still be emotionally coherent

---

## CHAIN MERGE LOGIC

**Status:** ❌ **NOT FOUND**

**Expected Location:** Should be in story_pipeline.py or story_chain_builder.py

**Current Behavior:** 
- Chains are built **per conversation block**
- No cross-block merging detected
- Each chain is independent

**Gap:** No logic to merge adjacent chains with high continuity

**Recommendation:** Add chain stitching for high speaker/topic overlap

---

## AGGRESSIVE BREAKPOINT SUMMARY

| ID | Breakpoint | Threshold | Severity | Line | File |
|----|-----------|-----------|----------|------|------|
| 1 | **Conversation gap limit** | **2.0s** | 🔴 CRITICAL | 91 | story_pipeline.py |
| 2 | **Minimum duration filter** | **35.0s** | 🔴 CRITICAL | 142 | story_pipeline.py |
| 3 | **Payoff extension window** | **120.0s** | ⚠️ HIGH | 839 | story_chain_builder.py |
| 4 | **Speaker overlap threshold** | **0.40** | ⚠️ MEDIUM-HIGH | 879 | story_chain_builder.py |
| 5 | **Topic overlap threshold** | **0.25** | ⚠️ MEDIUM-HIGH | 879 | story_chain_builder.py |
| 6 | **Complete chain no extension** | N/A | ⚠️ MEDIUM | 826 | story_chain_builder.py |

---

## FALSE NEGATIVE CHAIN BREAKS

### Category 1: Natural Dialogue Pauses
- Thinking pause (2.5s) → **CHAIN BREAK**
- Dramatic silence (3.0s) → **CHAIN BREAK**
- Reaction beat (2.2s) → **CHAIN BREAK**

### Category 2: Short Complete Scenes
- 32s complete punchline → **REJECTED**
- 29s emotional reveal → **REJECTED**
- 33s confession + reaction → **REJECTED**

### Category 3: Semantic Continuity Without Tokens
- Setup: "I can't believe this"  
  Payoff: "You should have told me" → **NO TOPIC OVERLAP**
- Different characters discussing same event → **SPEAKER OVERLAP < 0.4**

### Category 4: Long Narrative Arcs
- Setup at 0:30, payoff at 2:35 (125s gap) → **BEYOND EXTENSION WINDOW**

### Category 5: Weak Payoff Not Improved
- Chain marked complete with payoff_score=0.18 → **NO EXTENSION ATTEMPTED**

---

## CONFIGURATION PARAMETERS

### Story Pipeline Config Keys

| Key | Default | Usage | Severity |
|-----|---------|-------|----------|
| `use_story_centric_pipeline` | `False` | Enable/disable story mode | 🔴 CRITICAL |
| `story_max_gap_seconds` | `2.0` | Max gap between turns | 🔴 CRITICAL |
| `target_story_min_seconds` | `35.0` | Minimum chain duration | 🔴 CRITICAL |
| `min_candidate_seconds` | `35.0` | Minimum window duration | 🔴 CRITICAL |
| `story_soft_max_seconds` | `45.0` | Target duration | ⚠️ MEDIUM |
| `story_hard_max_seconds` | `60.0` | Maximum duration | ⚠️ MEDIUM |
| `allow_story_extension_seconds` | `60.0` | Max chain extension | ⚠️ MEDIUM |

---

## RECOMMENDATIONS PRIORITY

### PRIORITY 1 (CRITICAL — Implement First)
1. ✅ **Increase `story_max_gap_seconds`** from 2.0 to **3.5-4.0s**
2. ✅ **Replace duration hard reject** with weighted penalty
3. ✅ **Add semantic override** for gap tolerance

### PRIORITY 2 (HIGH — Implement Next)
4. ✅ **Increase payoff extension window** from 120s to **180s**
5. ✅ **Relax speaker overlap** from 0.40 to **0.30**
6. ✅ **Relax topic overlap** from 0.25 to **0.18**
7. ✅ **Add semantic similarity** as third matching criterion

### PRIORITY 3 (MEDIUM — Quality Improvements)
8. ✅ **Check payoff_score** before skipping extension (threshold: 0.40)
9. ✅ **Add proactive rescue** for 30-35s high-quality chains
10. ✅ **Add chain merge logic** for adjacent high-continuity blocks

---

## NEXT STEPS

After this audit:

1. ✅ **TASK 2**: Relax story continuity gates (story_max_gap_seconds, payoff matching)
2. ✅ **TASK 3**: Remove hard floor damage (replace with weighted penalties)
3. ✅ **TASK 4**: Implement chain continuation priority scoring
4. ✅ **TASK 5**: Add payoff protection window
5. ✅ **TASK 6**: Add validation metrics
6. ✅ **TASK 7**: Generate completion report

---

*Audit completed: 2026-06-21 22:25 UTC+3*
*DO NOT MODIFY CODE YET — WAIT FOR TASK 2-7 EXECUTION*
