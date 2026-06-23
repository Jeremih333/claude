# STORY PIPELINE EXECUTION MAP
**Date**: 2026-06-19 01:25 AM MSK  
**Purpose**: Complete execution graph of story candidate pipeline

---

## 🎯 DUAL PATH ARCHITECTURE

Pipeline has **TWO PARALLEL PATHS** for candidate generation:

```
┌─────────────────────────────────────┐
│  pick_candidates() [line 8350]     │
│  Feature flag check:                │
│  use_story_centric_pipeline?        │
└─────────────┬───────────────────────┘
              │
     ┌────────┴────────┐
     │                 │
   TRUE              FALSE
     │                 │
     ▼                 ▼
┌─────────┐      ┌──────────┐
│ PATH A  │      │ PATH B   │
│ STORY   │      │ LEGACY   │
│ CENTRIC │      │ TEMPORAL │
└─────────┘      └──────────┘
```

---

## 📊 PATH A: STORY-CENTRIC MODE (NEW)

**Feature Flag**: `cfg["use_story_centric_pipeline"] = True`

**Entry Point**: `pick_candidates()` line 8362

### PHASE 1: Full Episode Transcription

**Location**: `highlight.py:8364`

```python
self.subtitle_info = self._transcribe_full_episode(video_path)
```

**Flow**:
```
_transcribe_full_episode() [line 5331]
  ↓
extract_audio_to_wav(video_path, wav_path)
  ↓
transcribe_segment(wav_path, language="auto")
  → subtitle.py:60 (SOURCE OF TRUTH)
  ↓
RETURNS: subtitle_info
  {
    "segments": [
      {
        "start": float,
        "end": float,
        "text": str,
        "speaker": str|None,
        "words": [...]
      }
    ],
    "summary": {...}
  }
```

**Output**: Full episode subtitle_info with ALL segments

---

### PHASE 2: Story Chain Generation

**Location**: `highlight.py:5417`

```python
story_chains = build_story_chains_for_episode(
    subtitle_info,
    cfg=self.cfg,
    source_id=video_path
)
```

**Delegates to**: `pipeline/montage/story_pipeline.py:48-158`

#### STEP 2.1: Extract Dialogue Turns

**Location**: `story_pipeline.py:96`

```python
turns = extract_dialogue_turns(segments)
```

**Delegates to**: `pipeline/montage/dialogue_parser.py:30-101`

**Logic**:
- Groups consecutive segments by same speaker
- Identifies turn boundaries (speaker changes)
- Classifies turn types: statement, question, exclamation, continuation

**Output**:
```python
turns = [
  {
    "turn_id": str,
    "start": float,
    "end": float,
    "speaker": str,
    "text": str,
    "turn_type": "statement|question|exclamation",
    "is_continuation": bool
  }
]
```

**Authority**: **TURNS ARE SPEAKER-FIRST** (from subtitle segments, NOT face detection)

---

#### STEP 2.2: Group Conversations

**Location**: `story_pipeline.py:103`

```python
all_blocks = group_conversations(
    turns, 
    max_gap_seconds=max_gap,  # default: 2.0s
    source_id=source_id
)
```

**Delegates to**: `pipeline/montage/conversation_grouper.py:175-340`

**Logic**:
- Primary split: temporal gap > max_gap_seconds
- BRIDGE conditions (override split):
  - Same speakers continuing
  - Topic token overlap
  - Monologue continuation
  - Incomplete sentence endings

**Config**:
```python
max_gap = float(cfg.get("story_max_gap_seconds", 2.0))
```

**Output**:
```python
blocks = [
  {
    "conversation_id": str,
    "turns": [...],
    "start": float,
    "end": float,
    "speakers": set,
    "topic_tokens": set
  }
]
```

**Potential Starvation**: If max_gap_seconds too strict → many small blocks → no 35s+ chains

---

#### STEP 2.3: Build Story Fragments

**Location**: `story_pipeline.py:116`

```python
fragments = build_story_fragments(block_turns)
```

**Delegates to**: `pipeline/montage/story_fragments.py:130-290`

**Logic**:
- Classifies each turn into story role:
  - **hook**: Question, setup, problem introduction
  - **setup**: Context, exposition, character intro
  - **escalation**: Conflict, tension, complication
  - **payoff**: Resolution, punchline, answer

**Output**:
```python
fragments = [
  StoryFragment(
    role="hook|setup|escalation|payoff",
    start=float,
    end=float,
    turn_ids=[...],
    text=str,
    confidence=float
  )
]
```

---

#### STEP 2.4: Build Story Chain

**Location**: `story_pipeline.py:119`

```python
chain = build_story_chain(fragments, conversation_id=conversation_id)
```

**Delegates to**: `pipeline/montage/story_chain_builder.py:312-510`

**Logic**:
- Assembles fragments into complete story arc
- Requires: hook → setup → escalation → payoff
- Calculates completion_score based on filled roles

**Output**:
```python
chain = StoryChain(
    hook=StoryFragment|None,
    setup=StoryFragment|None,
    escalation=StoryFragment|None,
    payoff=StoryFragment|None,
    fragments=[...],
    is_complete=bool,  # all 4 roles filled?
    completion_score=float,
    story_arc_shape=str,
    speakers=set,
    start=float,
    end=float
)
```

---

#### STEP 2.5: Extend Chain for Payoff

**Location**: `story_pipeline.py:138`

```python
if not chain.is_complete:
    chain = try_extend_chain_for_payoff(chain, all_blocks)
```

**Delegates to**: `pipeline/montage/story_chain_builder.py:513-670`

**Logic**:
- If chain missing payoff, search adjacent conversation blocks
- Looks for matching topic tokens
- Extends chain.end to include payoff fragment
- Sets chain.search_extended = True

**Potential Fix Target**: Expand search radius if payoff not found

---

#### STEP 2.6: Filter by Duration ← **STARVATION POINT #1**

**Location**: `story_pipeline.py:142-147`

```python
min_dur = min(35.0, min_seconds)
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]

# Fallback: keep any chain with fragments if all filtered out
if not filtered and extended_chains:
    filtered = [c for c in extended_chains if c.fragments]
```

**Hard Gate**: **duration >= 35s**

**Fallback Behavior**: Returns ANY chain if all < 35s

---

#### STEP 2.7: Rank Chains

**Location**: `story_pipeline.py:150-156`

```python
filtered.sort(
    key=lambda c: (
        1 if c.is_complete else 0,  # complete chains first
        float(c.completion_score),   # then by score
    ),
    reverse=True,
)
```

**Output**: Ranked list of StoryChain objects

---

### PHASE 3: Convert Chains to Candidate Windows

**Location**: `highlight.py:5426-5447`

```python
for chain in story_chains:
    candidate = story_chain_to_candidate(chain, source="story_pipeline")
    windows.append((
        candidate["start"],
        candidate["end"],
        "story_pipeline"
    ))
```

**Delegates to**: `story_pipeline.py:161-217` (`story_chain_to_candidate()`)

**Output**:
```python
candidate = {
    "start": float,
    "end": float,
    "duration": float,
    "source": "story_pipeline",
    "story_unit_type": "story_chain",
    "score": float,  # completion_score
    "score_breakdown": {
        "completion_score": float,
        "is_complete": bool,
        "arc_shape": str,
        "hook_filled": bool,
        "setup_filled": bool,
        "escalation_filled": bool,
        "payoff_filled": bool
    },
    "story_summary": {...},
    "story_chain": {...},
    "story_fragments": [...]
}
```

**Windows returned**:
```python
windows = [
    (chain1.start, chain1.end, "story_pipeline"),
    (chain2.start, chain2.end, "story_pipeline"),
    ...
]
```

---

### PHASE 4: Per-Window Processing

**Location**: `highlight.py:8385-8435`

```python
for window_start, window_end, source in windows:
    summary = self._extract_audio_summary(video_path, window_start, window_end)
    
    # Admission gate (may reject)
    admission = self._dialogue_flow_admission(summary)
    if not admission.get("admit", False):
        rejected.append(...)
        continue
    
    # Build candidates from this window
    # (story-centric windows already have story_chain, so this is lightweight)
    built = self._build_story_candidates_from_turns_linear(...)
    if not built:
        built = self._build_story_candidates_from_window(...)
    if not built:
        fallback = self._fallback_window_candidate(...)
        if fallback is None:
            # VIOLATION: artificial injection (lines 8419-8433)
            fallback = {...}  # DELETE THIS
        built = [fallback]
    
    story_candidates.extend(built)
```

---

## 📊 PATH B: LEGACY TEMPORAL MODE (OLD)

**Feature Flag**: `cfg["use_story_centric_pipeline"] = False`

**Entry Point**: `_candidate_windows_legacy()` line 5462

### PHASE 1: Scene Detection

**Location**: `highlight.py:5465`

```python
scenes = detect_scenes(video_path, cfg=self.cfg)
```

**Delegates to**: `pipeline/scene_detect.py`

**Logic**:
- Uses PySceneDetect or FFmpeg scene detection
- Detects hard cuts, fades, shot boundaries

**Output**:
```python
scenes = [
    (start_seconds, end_seconds),
    ...
]
```

**Issue**: Scene boundaries ≠ story boundaries

---

### PHASE 2: Generate Temporal Windows

**Location**: `highlight.py:5475-5515`

**Logic**:
- Merges short scenes
- Splits long scenes into overlapping windows
- Applies min_window_seconds / max_window_seconds constraints

**Config**:
```python
min_window_seconds = 35.0  # minimum candidate length
max_window_seconds = 60.0  # maximum candidate length
window_overlap = 15.0      # overlap for long scenes
```

**Output**:
```python
windows = [
    (start, end, "scene"),
    (start, end, "scene_split_1"),
    ...
]
```

---

### PHASE 3: Per-Window Processing (SAME AS PATH A)

**Location**: `highlight.py:8385-8435`

For each window:

#### STEP 3.1: Extract Audio Summary

**Location**: `highlight.py:8391`

```python
summary = self._extract_audio_summary(video_path, window_start, window_end)
```

**Delegates to**: `highlight.py:5521-5664`

**Output**:
```python
summary = {
    "turns": [(start, end), ...],
    "speech_density": float,
    "silence_ratio": float,
    "audio_energy": float,
    "word_count": int
}
```

---

#### STEP 3.2: Admission Gate

**Location**: `highlight.py:8392`

```python
admission = self._dialogue_flow_admission(summary)
if not admission.get("admit", False):
    rejected.append(...)
    continue
```

**Delegates to**: `highlight.py:3845-3950`

**Gates**:
- Minimum dialogue flow score
- Minimum turn count
- Minimum speech density

**Rejection Reasons**:
- `low_dialogue_flow`
- `insufficient_context`
- `too_much_silence`

---

#### STEP 3.3: Build Story Candidates (Linear)

**Location**: `highlight.py:8402`

```python
built = self._build_story_candidates_from_turns_linear(
    window_start, window_end, source, summary
)
```

**Delegates to**: `highlight.py:5824-5920`

**Logic**: ← **STARVATION POINT #2**
- Clusters dialogue turns sequentially
- Requires: duration >= 35s
- Returns `[]` if cannot build 35s+ cluster

---

#### STEP 3.4: Build Story Candidates (Window)

**Location**: `highlight.py:8404`

```python
if not built:
    built = self._build_story_candidates_from_window(
        window_start, window_end, source, summary
    )
```

**Delegates to**: `highlight.py:5669-5800`

**Logic**: ← **STARVATION POINT #3**
- Builds overlapping turn clusters
- Requires: duration >= 12s OR 17.5s
- Returns `[]` if no valid clusters

---

#### STEP 3.5: Fallback Window Candidate

**Location**: `highlight.py:8415`

```python
if not built:
    fallback = self._fallback_window_candidate(
        window_start, window_end, source, summary
    )
```

**Delegates to**: `highlight.py:5987-6044`

**Logic**: ← **STARVATION POINT #4**
- Last resort: create candidate from raw window
- Requires: speech_density >= 0.18 AND duration >= 35s
- Returns `None` if fails

---

#### STEP 3.6: Artificial Injection ← **VIOLATION**

**Location**: `highlight.py:8418-8433`

```python
if fallback is None:
    # PHASE 1 FIX: Create minimal candidate instead of rejecting
    fallback = {
        "start": window_start,
        "end": window_end,
        "fallback_reason": "insufficient_context_minimal_candidate",
        "score": 0.35,  # FAKE SCORE
        "score_breakdown": {
            "story_clarity_score": 0.30,
            "story_completeness_score": 0.25,
            "speech_density": 0.40,
        }
    }
    built = [fallback]
```

**Verdict**: **DELETE IMMEDIATELY** (RULE A violation)

---

## 🔀 PATH COMPARISON

| Feature | PATH A (Story) | PATH B (Legacy) |
|---------|----------------|-----------------|
| **Window Source** | Story chains | Scene detection |
| **Semantic Aware** | ✅ Yes | ❌ No |
| **Speaker Tracking** | ✅ Yes | ⚠️ Limited |
| **Story Arc** | ✅ Complete | ❌ Partial |
| **Duration Floor** | 35s (with fallback) | 35s (hard gate) |
| **Starvation Risk** | Medium | High |
| **Artificial Injection** | ❌ No | ✅ Yes (lines 8419-8433) |

---

## 🎯 RECOMMENDED PATH

**Use PATH A (Story-Centric)** with fixes:
1. ✅ Already semantic-aware
2. ✅ Speaker tracking correct
3. ✅ No artificial injection
4. ⚠️ May need duration floor lowered to 25s
5. ⚠️ May need conversation grouping relaxed (max_gap 3-4s)

**Avoid PATH B (Legacy)** unless:
- No transcription available
- Emergency fallback only

---

**END OF STORY PIPELINE EXECUTION MAP**
