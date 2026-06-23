# UPSTREAM STARVATION MAP
**Date**: 2026-06-19 01:25 AM MSK  
**Purpose**: Identify exact points where candidate count collapses to zero

---

## 🎯 EXECUTIVE SUMMARY

Pipeline has **4 STARVATION POINTS** where candidates are lost.  
All share common trait: **35-second minimum duration requirement**.

If episode content has NO 35s+ story arcs → **GUARANTEED STARVATION**.

---

## 📍 STARVATION POINT #1: Story Pipeline Duration Filter

**Location**: `pipeline/montage/story_pipeline.py:143`

**Code**:
```python
# 5. Filter: must meet minimum duration threshold
min_dur = min(35.0, min_seconds)
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]
```

**Trigger Condition**:
- ALL story chains < 35 seconds

**Fallback** (line 146):
```python
# If all chains were filtered out, fall back to keeping any non-empty chain
if not filtered and extended_chains:
    filtered = [c for c in extended_chains if c.fragments]
```

**Impact**:
- PRIMARY filter in story-centric mode
- Returns ANY chain with fragments as last resort
- May produce <35s chains if nothing else available

**Upstream Dependencies**:
1. `extract_dialogue_turns()` → dialogue_parser.py
2. `group_conversations()` → conversation_grouper.py (max_gap_seconds=2.0)
3. `build_story_fragments()` → story_fragments.py
4. `build_story_chain()` → story_chain_builder.py
5. `try_extend_chain_for_payoff()` → payoff extension search

**Starvation Cascade**:
```
segments [OK] 
  → turns [OK] 
    → conversation blocks [OK] 
      → story fragments [OK] 
        → story chains [OK] 
          → FILTER (duration >= 35s) [FAILS] 
            → filtered = [] ← STARVATION
```

---

## 📍 STARVATION POINT #2: Legacy Linear Builder

**Location**: `pipeline/highlight.py:5824-5920`

**Code** (line 5883):
```python
candidate_start = max(window_start, cluster_start - left_pad)
candidate_end = min(window_end, turns[end_index][1] + right_pad)
if candidate_end - candidate_start < min_story:
    # Try to extend with probe logic (lines 5884-5900)
```

**Config**:
```python
min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
```

**Trigger Condition**:
- Cannot build 35s+ cluster from dialogue turns
- Extension probe fails to reach 35s

**Impact**:
- Returns empty `[]` if no valid clusters
- Used in legacy mode (use_story_centric_pipeline=False)

**Hard Gates**:
1. Line 5828: `if len(turns) < 1 and speech_density < 0.18: return []`
2. Line 5883: `if duration < min_story: [try extend]`
3. Line 5875: `if candidate_end - candidate_start > max_story: break` (60s cap)

---

## 📍 STARVATION POINT #3: Legacy Window Builder

**Location**: `pipeline/highlight.py:5669-5800`

**Code** (line 5729):
```python
duration = candidate_end - candidate_start
if duration < max(12.0, min_story * 0.5):
    continue
```

**Config**:
```python
min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
# Minimum: 12s OR 17.5s (half of 35s)
```

**Trigger Condition**:
- All turn clusters < 12s (absolute floor)
- All clusters < 17.5s (typical floor)

**Impact**:
- Skips short candidates in loop
- Returns empty `candidates = []` if nothing qualifies

**Hard Gates**:
1. Line 5673: `if len(turns) < 1 and speech_density < 0.18: return []`
2. Line 5729: `if duration < max(12.0, min_story * 0.5): continue`
3. Line 5720: `if candidate_end - candidate_start > max_story: break` (60s cap)

---

## 📍 STARVATION POINT #4: Fallback Window Candidate

**Location**: `pipeline/highlight.py:5987-6044`

**Code** (lines 5993-5996):
```python
speech_density_value = float(summary.get("speech_density", 0.0))
if speech_density_value < 0.18 or duration < max(
    35.0, float(self.cfg.get("min_candidate_seconds", 35))
):
    return None
```

**Trigger Conditions**:
- speech_density < 0.18 (low dialogue)
- duration < 35s

**Impact**:
- Returns `None`
- Caller at line 8415-8418 receives None
- **TRIGGERS ARTIFICIAL INJECTION** (lines 8419-8433) ← VIOLATION

**Artificial Injection Code** (DELETE THIS):
```python
if fallback is None:
    # PHASE 1 FIX: Create minimal candidate instead of rejecting
    fallback = {
        "start": window_start,
        "end": window_end,
        "fallback_reason": "insufficient_context_minimal_candidate",
        "score": 0.35,  # FAKE SCORE
    }
```

---

## 🔗 STARVATION CASCADE (FULL FLOW)

### Story-Centric Mode Flow:
```
_transcribe_full_episode()
  → subtitle_info: {segments: [...]}
    ↓
build_story_chains_for_episode()
  → extract_dialogue_turns(segments)
    → turns: [{start, end, speaker, text}]
      ↓
  → group_conversations(turns, max_gap=2.0s)
    → blocks: [{conversation_id, turns}]
      ↓ (POTENTIAL STARVATION: if max_gap too strict)
  → build_story_fragments(block_turns)
    → fragments: [{role: hook|setup|escalation|payoff}]
      ↓
  → build_story_chain(fragments)
    → chain: {hook, setup, escalation, payoff, completion_score}
      ↓
  → try_extend_chain_for_payoff(chain)
    → chain.is_complete? 
      ↓
  → FILTER: duration >= 35s ← STARVATION POINT #1
    → filtered = []
      ↓
    → story_chains = [] ← NO CANDIDATES
```

### Legacy Mode Flow:
```
detect_scenes()
  → windows: [(start, end, "scene")]
    ↓
FOR EACH window:
  _extract_audio_summary(window)
    → {turns, speech_density, silence_ratio}
      ↓
  _dialogue_flow_admission()
    → admit=True/False
      ↓ (POTENTIAL STARVATION: if rejected)
  IF admitted:
    _build_story_candidates_from_turns_linear()
      → REQUIRES: duration >= 35s ← STARVATION POINT #2
        → [] if fails
          ↓
    _build_story_candidates_from_window()
      → REQUIRES: duration >= 12s/17.5s ← STARVATION POINT #3
        → [] if fails
          ↓
    _fallback_window_candidate()
      → REQUIRES: speech_density >= 0.18, duration >= 35s
        → None if fails ← STARVATION POINT #4
          ↓
        → ARTIFICIAL INJECTION (lines 8419-8433) ← VIOLATION
```

---

## 📊 STARVATION STATISTICS

### Common Denominator: 35s Minimum

**Story pipeline**: 35s hard floor (with <35s fallback)  
**Legacy linear**: 35s hard floor  
**Legacy window**: 12s absolute / 17.5s typical floor  
**Legacy fallback**: 35s hard floor + speech_density >= 0.18

### Content Characteristics That Trigger Starvation:

1. **Fragmented dialogue**: Many short exchanges < 35s
2. **Low speech density**: Sparse dialogue, lots of silence/music
3. **Wide conversation gaps**: Gaps > 2.0s break conversation blocks
4. **Incomplete story arcs**: No hook→setup→escalation→payoff structure
5. **Short scenes**: Scene detection produces <35s windows

---

## 🔬 ROOT CAUSE ANALYSIS

### Question: Why does starvation occur?

**Answer A**: Content genuinely lacks 35s+ story arcs  
→ **Fix**: Lower duration threshold to 25s OR expand chains

**Answer B**: Conversation grouping too strict (max_gap=2.0s)  
→ **Fix**: Increase max_gap_seconds to 3.0s or 4.0s

**Answer C**: Story chain extension fails to find payoff  
→ **Fix**: Improve `try_extend_chain_for_payoff()` search

**Answer D**: Scene detection produces poor windows  
→ **Fix**: Use story-centric mode instead of legacy

**Answer E**: Speech density calculation too conservative  
→ **Fix**: Review audio_analysis.py speech detection

### Current State: MASKED BY ARTIFICIAL INJECTION

The artificial candidate injection (lines 8419-8433) and forced minimum count (minimum_candidate_count=12) **hide the real starvation point**.

**Must delete these violations FIRST**, then re-run to see natural starvation.

---

## 🎯 DIAGNOSTIC PROTOCOL

### Step 1: Add Counters at Each Starvation Point

```python
# story_pipeline.py line 143
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]
print(f"[STARVATION #1] story_chains: {len(extended_chains)} → filtered: {len(filtered)} (min_dur={min_dur}s)")

# highlight.py line 5828
if len(turns) < 1 and speech_density < 0.18:
    print(f"[STARVATION #2] linear_builder: rejected (turns={len(turns)}, speech_density={speech_density:.2f})")
    return []

# highlight.py line 5729
if duration < max(12.0, min_story * 0.5):
    print(f"[STARVATION #3] window_builder: skip {duration:.1f}s < {max(12.0, min_story * 0.5):.1f}s")
    continue

# highlight.py line 5996
if speech_density_value < 0.18 or duration < 35.0:
    print(f"[STARVATION #4] fallback: reject speech_density={speech_density_value:.2f}, duration={duration:.1f}s")
    return None
```

### Step 2: Run Test Episode

```bash
python main.py --episode episode01_test.avi --config settings.yaml
```

### Step 3: Analyze Output

Count which starvation point fires most frequently:
- `[STARVATION #1]` → story pipeline duration filter
- `[STARVATION #2]` → linear builder insufficient turns
- `[STARVATION #3]` → window builder duration floor
- `[STARVATION #4]` → fallback speech_density/duration gate

### Step 4: Apply Evidence-Backed Fix

**DO NOT** fix speculatively.  
**ONLY** fix after diagnostic confirms root cause.

---

## ✅ SUCCESS CRITERIA

After fixes:
1. ✅ **Natural starvation visible** (no artificial inflation)
2. ✅ **Diagnostics identify real bottleneck**
3. ✅ **Fix targets root cause** (not symptoms)
4. ✅ **Output quality > output quantity**

---

**END OF UPSTREAM STARVATION MAP**
