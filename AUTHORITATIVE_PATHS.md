# AUTHORITATIVE_PATHS.md
**PHASE 2 ROOT CAUSE RECOVERY — System Architecture Truth Map**

---

## PURPOSE

Определить: какие компоненты authoritative (SOURCE OF TRUTH), какие obsolete (legacy), какие violations (удалить), и как rewire execution flow.

---

## ✅ ACTIVE AUTHORITATIVE (KEEP — SOURCE OF TRUTH)

### 1. **story_pipeline.py** (217 lines)
**Function**: `build_story_chains_for_episode()`
**Status**: ✅ **AUTHORITATIVE** — Primary story-centric pipeline

**Flow**:
```python
subtitle_segments
  → extract_dialogue_turns()        # dialogue_parser.py
  → group_conversations()           # conversation_grouper.py
  → build_story_fragments()         # story_fragments.py
  → build_story_chain()             # story_chain_builder.py
  → try_extend_chain_for_payoff()   # story_chain_builder.py
  → filter (duration >= 35s)
  → rank by completion_score
  → return StoryChain[]
```

**Why Keep**:
- Clean separation of concerns
- Semantic grouping (turn → block → fragment → chain)
- Payoff extension search (adjacent blocks)
- No synthetic injection
- No artificial scoring

**Integration**: Used when `cfg["use_story_centric_pipeline"] = True` (line 8361)

---

### 2. **dialogue_parser.py**
**Function**: `extract_dialogue_turns()`
**Status**: ✅ **AUTHORITATIVE** — Turn extraction from segments

**Responsibilities**:
- Merge adjacent segments by same speaker
- Classify turn type (question/statement/exclamation)
- Extract speaker attribution
- Clean text

**Why Keep**: Clean, focused, no side effects

---

### 3. **conversation_grouper.py**
**Function**: `group_conversations()`
**Status**: ✅ **AUTHORITATIVE** — Semantic grouping with bridge conditions

**Logic**:
```python
max_gap = 2.0s  # ⚠️ MAY NEED TUNING (see STORY_CHAIN_FAILURES.md)

# Split on gap > max_gap, UNLESS:
BRIDGE CONDITIONS:
1. Same speakers (overlap >= 0.50)
2. Topic continuity (token overlap >= 0.18)
3. Monologue continuation (single speaker)
```

**Why Keep**: Sophisticated bridge logic, semantic awareness

**Tuning Needed**: May need to raise max_gap to 3.5s (see RC-2)

---

### 4. **story_fragments.py**
**Function**: `build_story_fragments()`
**Status**: ✅ **AUTHORITATIVE** — Fragment role classification

**Responsibilities**:
- Chunk turns into fragments
- Score each fragment (hook/setup/escalation/payoff)
- Assign roles based on keyword signals
- Extract conflict/emotion signals

**Why Keep**: Semantic classification, no hacks

**Tuning Needed**: Keyword lists may need expansion (domain-specific)

---

### 5. **story_chain_builder.py**
**Functions**:
- `build_story_chain()`
- `build_story_summary()`
- `try_extend_chain_for_payoff()`

**Status**: ✅ **AUTHORITATIVE** — Chain assembly + payoff search

**Why Keep**:
- Clean StoryChain/StorySummary dataclasses
- Positional fallbacks when keyword classification fails
- Payoff extension searches adjacent blocks
- No synthetic data

**Tuning Needed**: Payoff search thresholds (see STORY_CHAIN_FAILURES.md)

---

### 6. **subtitle.py**
**Function**: `transcribe_segment()` + `build_ass_word_events()`
**Status**: ✅ **AUTHORITATIVE** — Transcription + ASS rendering

**Responsibilities**:
- Whisper transcription (SOURCE OF TRUTH for text)
- Word-level timing
- ASS subtitle event generation
- Timeline stabilization

**Why Keep**: Core transcription logic, well-tested

**Tuning Needed**: Persistence gaps (see SUBTITLE_PERSISTENCE_MAP.md)

---

### 7. **active_speaker.py**
**Function**: `estimate_face_tracks()`
**Status**: ✅ **AUTHORITATIVE** — Face detection + tracking

**Responsibilities**:
- MediaPipe face detection
- Speaking score estimation
- Face bbox tracking over time

**Why Keep**: Core visual analysis

**Rewiring Needed**: Must be invoked AFTER turn boundary detection (see ACTIVE_SPEAKER_REBUILD.md)

---

### 8. **face_crop.py**
**Function**: `create_vertical_crop()`
**Status**: ✅ **AUTHORITATIVE** — Reframing logic

**Responsibilities**:
- Vertical 9:16 crop generation
- Face-centered framing
- Speaker switching detection
- Motion smoothing

**Why Keep**: Core reframing logic

**Rewiring Needed**: Add turn_timeline input parameter (see ACTIVE_SPEAKER_REBUILD.md)

---

## ⚠️ OBSOLETE / LEGACY (REVIEW FOR DELETE)

### 1. **_build_story_candidates_from_turns_linear()**
**Location**: `highlight.py:5824-5920`
**Purpose**: Build candidates from linear turn sequence

**Problem**:
- Hardcoded 35s minimum duration
- No payoff extension search
- Overlaps with story_pipeline.py functionality

**Status**: ⚠️ **POTENTIALLY OBSOLETE** if story_pipeline.py works

**Decision**: Keep for now as fallback, mark for review

---

### 2. **_build_story_candidates_from_window()**
**Location**: `highlight.py:5669-5800`
**Purpose**: Build candidates from fixed time window

**Problem**:
- Scene-based, not dialogue-based
- No semantic grouping
- Lower quality than story_pipeline

**Status**: ⚠️ **POTENTIALLY OBSOLETE**

**Decision**: Keep as fallback, mark for review

---

### 3. **_fallback_window_candidate()**
**Location**: `highlight.py:5987-6044`
**Purpose**: Create fallback candidate when builders fail

**Problem**:
- Low-quality output
- Returns None frequently → triggers artificial injection

**Status**: ⚠️ **POTENTIALLY OBSOLETE**

**Decision**: Keep as last resort, but should rarely fire if story_pipeline works

---

### 4. **_candidate_windows_legacy()**
**Location**: `highlight.py:5462-5520`
**Purpose**: Scene detection-based window generation

**Problem**:
- Scene cuts don't align with dialogue boundaries
- Obsoleted by story_pipeline

**Status**: ⚠️ **OBSOLETE** when use_story_centric_pipeline=True

**Decision**: Keep for legacy mode, mark for deprecation

---

## 🔴 VIOLATIONS (DELETE FIRST — PRIORITY 1)

### VIOLATION #1: Artificial Candidate Injection
**Location**: `highlight.py:8419-8433`
**Code**:
```python
if fallback is None:
    # PHASE 1 FIX: Create minimal candidate instead of rejecting
    fallback = {
        "start": window_start,
        "end": window_end,
        "duration": window_end - window_start,
        "source": source,
        "fallback_reason": "insufficient_context_minimal_candidate",
        "score": 0.35,  # Low but acceptable baseline ← SYNTHETIC
        "score_breakdown": {
            "story_clarity_score": 0.30,
            "story_completeness_score": 0.25,
            "speech_density": 0.40,
        }
    }
```

**Problem**:
- Synthetic score=0.35 (not based on real analysis)
- Synthetic score_breakdown (fabricated values)
- Masks builder failure rate
- Hides upstream starvation

**Verdict**: 🔴 **DELETE** (RULE A violation)

**Replacement**: Let builder failures be visible, return empty list

---

### VIOLATION #2: Minimum Candidate Count Top-Up
**Location**: `highlight.py:9385-9418`
**Code**:
```python
# PHASE 1 FIX 1.5: Guarantee minimum candidate count per episode
minimum_candidate_count = 12

if len(picked) < minimum_candidate_count and ranked:
    remaining_candidates = [c for c in ranked if c not in picked]
    remaining_candidates.sort(key=lambda x: float(x.get("score", 0.0) or 0.0), reverse=True)
    
    needed = minimum_candidate_count - len(picked)
    for candidate in remaining_candidates[:needed]:
        if not overlap:
            picked.append(candidate)
            if len(picked) >= minimum_candidate_count:
                break
```

**Problem**:
- Forces 12 candidates minimum regardless of quality
- Episode with 3 natural candidates → force-topped to 12
- Masks starvation: can't see when pipeline fails
- Artificial quota

**Verdict**: 🔴 **DELETE** (RULE A violation)

**Replacement**: Return natural count, even if 0-5 outputs

---

### VIOLATION #3: Scorer Gate Bypass (TEMPORARY FLAG)
**Location**: `highlight.py:9064`
**Code**:
```python
# PHASE A BYPASS: Temporarily disable scorer gates
phase_a_bypass = True  # TEMP production experiment
```

**Problem**:
- Disables ALL quality gates
- Masks real rejection reasons
- Production experiment that became permanent

**Verdict**: ⚠️ **TEMPORARY** — Review for production

**Decision**:
- For PHASE 2 diagnostics: SET to `False` to see real gate behavior
- After diagnostics: Either keep gates OR delete gates entirely (not bypass)
- No permanent bypass flags in production

---

## 🔧 REWIRE CANDIDATES (PRIORITY 2)

### REWIRE #1: _dialogue_flow_admission()
**Location**: `highlight.py:6142-6220` (approx)
**Current**: Hard gate on speech coverage/turn count
**Problem**: May be too strict (30-40% rejection rate)

**Decision**:
- Keep for now
- After DELETE violations: Run diagnostics
- If admission is bottleneck → relax thresholds
- If not → keep as-is

---

### REWIRE #2: Story Pipeline Integration
**Location**: `highlight.py:8360-8379`
**Current**:
```python
if use_story_pipeline:
    self.subtitle_info = self._transcribe_full_episode(video_path)
    # ... but then legacy builders ALSO run
```

**Problem**: Dual path execution — story_pipeline produces candidates, then legacy builders run and may override

**Fix**:
```python
if use_story_pipeline:
    # Use ONLY story_pipeline, skip legacy builders
    story_chains = build_story_chains_for_episode(self.subtitle_info, cfg=self.cfg)
    story_candidates = [story_chain_to_candidate(chain) for chain in story_chains]
    return story_candidates, []  # Skip legacy path
else:
    # Legacy path (scene-based)
    # ... existing code
```

**Priority**: HIGH — prevents dual path conflict

---

### REWIRE #3: Active Speaker Authority
**Location**: `face_crop.py:130-280` (create_vertical_crop)
**Current**: Face-first (face tracking → speaker detection)
**Needed**: Turn-first (subtitle turn → face lookup)

**See**: ACTIVE_SPEAKER_REBUILD.md for detailed plan

**Priority**: MEDIUM — affects speaker switching quality

---

### REWIRE #4: Overlap Threshold
**Location**: `highlight.py:9155-9183`
**Current**: 95% overlap threshold for deduplication
**Consideration**: May need to lower to 85% for better diversity

**Decision**:
- Keep 95% for now
- After other fixes: Test with 85-90%
- Only if output diversity too low

**Priority**: LOW — cosmetic improvement

---

## 📊 EXECUTION PATH MAP

### CURRENT STATE (Dual Path):

```
pick_candidates()
  ↓
use_story_centric_pipeline?
  ├─ YES: transcribe_full_episode()
  │         → BUT legacy builders ALSO run ⚠️
  │         → CONFLICT: which candidates win?
  │
  └─ NO:  _candidate_windows_legacy()
            → scene-based windows
            → legacy builders
```

### DESIRED STATE (Clean Paths):

```
pick_candidates()
  ↓
use_story_centric_pipeline?
  ├─ YES: story_pipeline.build_story_chains_for_episode()
  │         → RETURN story chains ✅
  │         → SKIP legacy builders
  │
  └─ NO:  _candidate_windows_legacy()
            → legacy builders (fallback mode)
```

---

## 🎯 DELETION ORDER

### PHASE 2.1 (Days 1-2): DELETE VIOLATIONS

**Step 1**: Delete artificial candidate injection
```python
# DELETE lines 8419-8433
# REPLACE with:
if fallback is None:
    built = []  # Let failure be visible
```

**Step 2**: Delete minimum_candidate_count top-up
```python
# DELETE lines 9385-9418
# No replacement needed — return natural count
```

**Step 3**: Disable scorer gate bypass (for diagnostics)
```python
# SET line 9064:
phase_a_bypass = False  # Enable gates for diagnostics
```

---

### PHASE 2.2 (Days 3-4): REWIRE STORY PIPELINE

**Step 1**: Make story_pipeline exclusive path
```python
if use_story_pipeline:
    story_chains = build_story_chains_for_episode(...)
    story_candidates = [story_chain_to_candidate(c) for c in story_chains]
    return story_candidates, []  # SKIP legacy builders
```

**Step 2**: Tune story_pipeline parameters (see STORY_CHAIN_FAILURES.md)
- Raise max_gap_seconds: 2.0 → 3.5
- Lower duration floor: 35s → 25s
- Relax payoff extension thresholds

---

### PHASE 2.3+ (Days 5-10): OTHER FIXES

- Subtitle persistence (SUBTITLE_PERSISTENCE_MAP.md)
- Active speaker authority (ACTIVE_SPEAKER_REBUILD.md)
- Validation

---

## 📋 COMPONENT STATUS SUMMARY

| Component | Status | Action |
|-----------|--------|--------|
| story_pipeline.py | ✅ AUTHORITATIVE | Keep + Tune |
| dialogue_parser.py | ✅ AUTHORITATIVE | Keep |
| conversation_grouper.py | ✅ AUTHORITATIVE | Keep + Tune |
| story_fragments.py | ✅ AUTHORITATIVE | Keep |
| story_chain_builder.py | ✅ AUTHORITATIVE | Keep + Tune |
| subtitle.py | ✅ AUTHORITATIVE | Keep + Tune |
| active_speaker.py | ✅ AUTHORITATIVE | Keep + Rewire |
| face_crop.py | ✅ AUTHORITATIVE | Keep + Rewire |
| _build_story_candidates_from_turns_linear() | ⚠️ LEGACY | Review after story_pipeline validated |
| _build_story_candidates_from_window() | ⚠️ LEGACY | Review after story_pipeline validated |
| _fallback_window_candidate() | ⚠️ LEGACY | Keep as last resort |
| _candidate_windows_legacy() | ⚠️ OBSOLETE | Deprecate when story_pipeline stable |
| Artificial candidate injection | 🔴 VIOLATION | DELETE (Priority 1) |
| minimum_candidate_count top-up | 🔴 VIOLATION | DELETE (Priority 1) |
| phase_a_bypass flag | ⚠️ TEMPORARY | Disable for diagnostics, then decide |

---

## ✅ NEXT STEPS

1. **DELETE** violations (lines 8419-8433, 9385-9418)
2. **DISABLE** phase_a_bypass (set to False)
3. **RUN** diagnostics to see natural starvation
4. **REWIRE** story_pipeline to be exclusive path
5. **TUNE** story_pipeline parameters based on diagnostics
6. **VALIDATE** natural output quality

---

**CONCLUSION**: System has clean authoritative paths (story_pipeline), but PHASE 1 violations + dual path execution mask their effectiveness. Priority: DELETE violations, make story_pipeline exclusive, tune parameters.
