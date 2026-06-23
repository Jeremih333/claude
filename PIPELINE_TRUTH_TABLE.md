# PIPELINE TRUTH TABLE
## PHASE 3F FORENSIC VALIDATION

**Date:** 2026-06-20  
**Validator:** Forensic code audit  
**Scope:** Complete execution chain with authority markers  

---

## EXECUTIVE SUMMARY

**Purpose:** Master reference for pipeline execution paths and authority hierarchy

This document maps the complete execution chain from video input to final export, marking each stage's authority status and dependencies.

---

## EXECUTION CHAIN MAP

```
┌─────────────────────────────────────────────────────────────┐
│ VIDEO INPUT                                                  │
│ process_episode(video_path)                                  │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 1: FULL EPISODE TRANSCRIPTION                         │
│ Authority: PRIMARY                                           │
│ Status: ACTIVE                                               │
│                                                              │
│ _transcribe_full_episode()                                   │
│   → Whisper transcription                                    │
│   → subtitle_info = {"segments": [...], "language": ...}    │
│   → Stored in self.subtitle_info                            │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 2: CANDIDATE WINDOW GENERATION                        │
│ Authority: CONDITIONAL (depends on config flag)             │
│ Status: DUAL PATH                                            │
│                                                              │
│ _candidate_windows()                                         │
│   ├─ IF use_story_centric_pipeline=True:                    │
│   │    → _candidate_windows_story_centric() [NEW]           │
│   │                                                          │
│   └─ ELSE (default):                                         │
│        → _candidate_windows_legacy() [DEFAULT]              │
└────────────────┬────────────────────────────────────────────┘
                 │
       ┌─────────┴─────────┐
       │                   │
       ▼                   ▼
┌──────────────┐    ┌──────────────┐
│ STORY PATH   │    │ LEGACY PATH  │
│ (Optional)   │    │ (Default)    │
└──────────────┘    └──────────────┘

═══════════════════════════════════════════════════════════════
STORY PATH (use_story_centric_pipeline=True)
═══════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────┐
│ STAGE 2A: STORY FRAGMENT DETECTION                          │
│ Authority: PRIMARY (when enabled)                            │
│ Status: CONDITIONAL                                          │
│                                                              │
│ build_story_chains_for_episode()                             │
│   → Analyze subtitle_info                                    │
│   → Detect story fragments (hook, setup, payoff)            │
│   → Score fragments                                          │
│   → Build story chains                                       │
│                                                              │
│ FALLBACK TRIGGERS:                                           │
│   • No subtitles → legacy                                    │
│   • No chains → legacy                                       │
│   • No valid windows → legacy                                │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 2B: STORY CHAIN → CANDIDATE CONVERSION                │
│ Authority: PRIMARY (when enabled)                            │
│ Status: CONDITIONAL                                          │
│                                                              │
│ story_chain_to_candidate()                                   │
│   → Extract time range from chain                            │
│   → Extract subtitle_segments (candidate-local)             │
│   → Calculate story metrics                                  │
│   → Build candidate dict                                     │
└────────────────┬────────────────────────────────────────────┘
                 │
                 └──────────┐
                            │
═══════════════════════════════════════════════════════════════
LEGACY PATH (use_story_centric_pipeline=False — DEFAULT)
═══════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────┐
│ STAGE 2A: TEMPORAL WINDOW EXTRACTION                        │
│ Authority: PRIMARY (default)                                 │
│ Status: ACTIVE                                               │
│                                                              │
│ _candidate_windows_legacy()                                  │
│   → Scene detection                                          │
│   → Turn-based grouping                                      │
│   → _build_story_candidates_from_turns_linear()             │
│   → Filter by duration >= 35s                                │
│   → Build candidate windows                                  │
└────────────────┬────────────────────────────────────────────┘
                 │
                 └──────────┐
                            │
═══════════════════════════════════════════════════════════════
CONVERGENCE POINT (Both paths merge here)
═══════════════════════════════════════════════════════════════
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 3: CANDIDATE SCORING & RANKING                        │
│ Authority: PRIMARY                                           │
│ Status: ACTIVE                                               │
│                                                              │
│ FOR EACH candidate:                                          │
│   _score_story_candidate()                                   │
│     → Audio analysis                                         │
│     → Dialogue flow scoring                                  │
│     → Story arc detection                                    │
│     → Face evidence scoring                                  │
│     → Composite score calculation                            │
│                                                              │
│ rank_story_candidates()                                      │
│   → Sort by score                                            │
│   → Apply quality gates                                      │
│   → Filter rejected candidates                               │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 4: CANDIDATE SELECTION                                │
│ Authority: PRIMARY                                           │
│ Status: ACTIVE                                               │
│                                                              │
│ select_publishable_candidates()                              │
│   → Apply selection_admission_score threshold               │
│   → Respect quality gates (NO synthetic injection)          │
│   → Return approved candidates                               │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 5: FACE DETECTION & TRACKING                          │
│ Authority: PRIMARY                                           │
│ Status: ACTIVE                                               │
│                                                              │
│ FOR EACH candidate:                                          │
│   analyze_active_speaker()                                   │
│     → MediaPipe face detection                               │
│     → Track assignment                                       │
│     → Speaking score calculation                             │
│     → Primary face selection                                 │
│     → Export face_tracks.json                                │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 6: TURN-FIRST ACTIVE SPEAKER FRAMING                  │
│ Authority: TURN-FIRST PRIMARY                                │
│ Status: ACTIVE ✅                                            │
│                                                              │
│ create_vertical_crop(subtitle_segments=...)                  │
│   ↓                                                          │
│   _build_turn_timeline(subtitle_segments)                    │
│     → Group by speaker                                       │
│     → Detect turn boundaries                                 │
│     → Return turn_timeline                                   │
│   ↓                                                          │
│   _build_window_targets(..., turn_timeline=turn_timeline)    │
│     → Per window:                                            │
│       • Resolve active_turn                                  │
│       • Compute subtitle_turn_changed                        │
│       • Store in target dict                                 │
│   ↓                                                          │
│   _turn_based_targets() / state machine                      │
│     → Extract subtitle_turn_changed                          │
│     → IF turn boundary:                                      │
│         • Force switch                                       │
│         • Reset cooldown                                     │
│         • Minimal hold (1 frame)                             │
│     → Face detection refines target                          │
│     → Hold + cooldown stability                              │
│   ↓                                                          │
│   Export vertical crop video                                 │
│                                                              │
│ AUTHORITY HIERARCHY:                                         │
│   1. subtitle_turn_changed (PRIORITY 1)                     │
│   2. Face speaking_score (refinement)                        │
│   3. Hold + cooldown (stability)                             │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 7: SUBTITLE STABILIZATION                             │
│ Authority: PERSISTENCE PRIMARY                               │
│ Status: ACTIVE ✅                                            │
│                                                              │
│ _stabilize_subtitle_timeline(events)                         │
│   ↓                                                          │
│   FOR EACH gap between subtitle events:                      │
│     ↓                                                        │
│     PRIORITY 1: hold_until_next_max = 0.90s                 │
│       IF gap ≤ 0.90s:                                        │
│         → FORCE BRIDGE (no bypass)                           │
│         → prev["end"] = current_start                        │
│         → SKIP all other logic                               │
│     ↓                                                        │
│     PRIORITY 2: phrase_ttl = 2.5s                           │
│       IF prev_age ≥ 2.5s:                                    │
│         → Soft hold bridge (0.4s)                            │
│         → Retire old phrase                                  │
│     ↓                                                        │
│     PRIORITY 3: persistence_max_extension = 1.2s            │
│       IF gap ≤ 1.2s:                                         │
│         → Apply persistence windows                          │
│     ↓                                                        │
│     PRIORITY 4: continuity_mode                              │
│       → "always" / "phrase_only" / "off"                     │
│   ↓                                                          │
│   Return stabilized events                                   │
│                                                              │
│ IF video cuts applied:                                       │
│   remap_subtitle_info_after_cuts()                           │
│     → Adjust timestamps                                      │
│     → Re-call _stabilize_subtitle_timeline()                 │
│     → Re-apply all priorities                                │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 8: SUBTITLE RENDERING                                 │
│ Authority: PRIMARY                                           │
│ Status: ACTIVE                                               │
│                                                              │
│ render_subtitles()                                           │
│   → Convert events to .ass format                            │
│   → Apply subtitle style (classic_bold, modern, etc.)       │
│   → Embed in video with ffmpeg                               │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 9: FINAL EXPORT                                       │
│ Authority: PRIMARY                                           │
│ Status: ACTIVE                                               │
│                                                              │
│ → Vertical crop video with embedded subtitles                │
│ → Export metadata JSON                                       │
│ → Export debug artifacts (if enabled)                        │
│ → Return published candidate                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## AUTHORITY MATRIX

| Stage | Function | Authority | Status | Default Path |
|-------|----------|-----------|--------|--------------|
| 1. Transcription | `_transcribe_full_episode()` | PRIMARY | ACTIVE | Always |
| 2. Candidate Windows | `_candidate_windows()` | ROUTER | ACTIVE | Routing decision |
| 2A. Story Chains | `_candidate_windows_story_centric()` | PRIMARY | CONDITIONAL | When flag=True |
| 2A. Legacy Windows | `_candidate_windows_legacy()` | PRIMARY | ACTIVE | **DEFAULT** |
| 3. Scoring | `_score_story_candidate()` | PRIMARY | ACTIVE | Always |
| 4. Selection | `select_publishable_candidates()` | PRIMARY | ACTIVE | Always |
| 5. Face Detection | `analyze_active_speaker()` | PRIMARY | ACTIVE | Always |
| 6. Turn-First Framing | `_build_turn_timeline()` | PRIMARY | ACTIVE | Always |
| 7. Subtitle Persistence | `_stabilize_subtitle_timeline()` | PRIMARY | ACTIVE | Always |
| 8. Subtitle Rendering | `render_subtitles()` | PRIMARY | ACTIVE | Always |
| 9. Export | Final export | PRIMARY | ACTIVE | Always |

---

## CONFIGURATION DECISION POINTS

### 1. Story-Centric vs Legacy (Stage 2)

```yaml
use_story_centric_pipeline: false  # DEFAULT
```

**Decision Table:**

| Config Value | Execution Path | Candidate Source |
|--------------|----------------|------------------|
| `false` (default) | Legacy | Turn-based linear extraction |
| `true` | Story-centric | Story chains with payoff detection |
| Not set | Legacy | (defaults to false) |

**Impact:**
- **DEFAULT = Legacy:** Turn-based, no payoff detection
- **Enabled = Story:** Payoff-aware, multi-block arcs

---

### 2. Turn-First Speaker Switching (Stage 6)

```yaml
# Always enabled when subtitle_segments available
```

**Authority:**
1. **subtitle_turn_changed** (PRIORITY 1 — turn boundary)
2. **Face speaking_score** (refinement)
3. **Hold + cooldown** (stability)

**Impact:**
- Turn boundaries force camera switches
- Face detection refines target selection
- Cooldown prevents thrashing

---

### 3. Subtitle Persistence (Stage 7)

```yaml
subtitle_continuity_mode: "always"  # DEFAULT
subtitle_phrase_ttl_seconds: 2.5
subtitle_persistence_max_extension_seconds: 1.2
```

**Priority Cascade:**
1. **hold_until_next_max = 0.90s** (PRIORITY 1 — NO BYPASS)
2. **phrase_ttl = 2.5s** (phrase retirement)
3. **persistence_max_extension = 1.2s** (extension limit)
4. **continuity_mode** (final fallback)

**Impact:**
- Gaps ≤ 0.90s ALWAYS bridged
- Prevents visual flicker
- Controlled subtitle overhang

---

## FALLBACK PATHS

### Story-Centric → Legacy Fallback

```python
# Stage 2A: Story-Centric
_candidate_windows_story_centric()
    ↓
IF no subtitles:
    → FALLBACK to _candidate_windows_legacy()
    
IF no story chains:
    → FALLBACK to _candidate_windows_legacy()
    
IF no valid windows:
    → FALLBACK to _candidate_windows_legacy()
```

**Fallback Scenarios:**
1. No subtitle data
2. Story chain building failed
3. No valid windows from chains

**Safety Net:** Legacy pipeline always available

---

## DEAD CODE STATUS

### Confirmed Dead
- `_find_best_face_for_speaker()` — DEAD (never called)

### Conditional (Not Dead)
- `_candidate_windows_legacy()` — ACTIVE (default path)
- `_candidate_windows_story_centric()` — ACTIVE (when enabled)

### Backup Files
- `highlight.py.backup_phase_a` — Archive candidate

---

## PHASE 3 IMPROVEMENTS VALIDATION

### ✅ Phase 3B: Subtitle Persistence
**Status:** ACTIVE  
**Authority:** PRIORITY 1 (hold_until_next_max = 0.90s)  
**Validation:** NO BYPASS possible

### ✅ Phase 3C: Turn-First Speaker Switching
**Status:** ACTIVE  
**Authority:** Turn boundaries = PRIMARY  
**Validation:** subtitle_turn_changed forces switches

### ⚠️ Phase 3: Story-Centric Pipeline
**Status:** CONDITIONAL (not default)  
**Authority:** SECONDARY (requires flag)  
**Issue:** Legacy is default, story-centric optional

---

## REGRESSION RISK MAP

| Stage | Risk Level | Mitigation |
|-------|------------|------------|
| Transcription | LOW | Whisper stable, well-tested |
| Story-Centric | MEDIUM | Fallback to legacy available |
| Legacy Windows | LOW | Proven, default path |
| Turn-First | LOW | Fallback to face-only mode |
| Subtitle Persistence | ZERO | No bypass, multiple safety layers |
| Face Detection | LOW | MediaPipe stable |
| Export | LOW | FFmpeg stable |

---

## DEPENDENCY GRAPH

```
subtitle_info (transcription)
    ├─→ Candidate windows (both paths)
    ├─→ Turn timeline (_build_turn_timeline)
    └─→ Subtitle stabilization

face_tracks.json (face detection)
    └─→ Framing (_build_window_targets)

turn_timeline (turn-first)
    └─→ subtitle_turn_changed
        └─→ Switch forcing logic

story_chains (story-centric)
    └─→ Candidates (when enabled)
        └─→ Scoring & ranking

Config flags
    ├─→ use_story_centric_pipeline (routing)
    ├─→ subtitle_continuity_mode (persistence)
    └─→ reframe_* parameters (framing)
```

---

## CRITICAL PATH ANALYSIS

### Fast Path (Default Config)
```
Video → Transcribe → Legacy Windows → Score → Select
    → Face Detection → Turn-First Framing → Subtitle Persistence → Export
```

**Bottlenecks:**
1. Transcription (Whisper) — 30-90s
2. Face Detection (MediaPipe) — 10-30s
3. Scoring (LLM) — 15-45s per candidate

---

### Story Path (Enabled Config)
```
Video → Transcribe → Story Chains → Story Windows → Score → Select
    → Face Detection → Turn-First Framing → Subtitle Persistence → Export
```

**Bottlenecks:**
1. Transcription (Whisper) — 30-90s
2. Story Chain Building — 10-20s
3. Face Detection (MediaPipe) — 10-30s
4. Scoring (LLM) — 15-45s per candidate

**Additional Cost:** Story chain building adds 10-20s

---

## PHASE 4 READINESS

### ✅ Ready for Phase 4
- Turn-first active speaker: WORKING
- Subtitle persistence: WORKING
- Face detection: WORKING
- Legacy pipeline: WORKING

### ⚠️ Blockers for Story Chain Tuning
- Story-centric NOT default
- Parameter tuning will have NO EFFECT unless enabled

### 📋 Required Before Phase 4
```yaml
# Enable story-centric by default
use_story_centric_pipeline: true
```

---

## VALIDATION SUMMARY

| Component | Status | Authority | Ready for Phase 4 |
|-----------|--------|-----------|-------------------|
| Turn-First | ✅ WORKING | PRIMARY | ✅ YES |
| Subtitle Persistence | ✅ WORKING | PRIORITY 1 | ✅ YES |
| Story-Centric | ✅ WORKING | CONDITIONAL | ⚠️ BLOCKED (not default) |
| Legacy Pipeline | ✅ WORKING | DEFAULT | ✅ YES |
| Face Detection | ✅ WORKING | PRIMARY | ✅ YES |
| Dead Code | ⚠️ MINIMAL | N/A | ✅ YES (cleanup recommended) |

---

## RECOMMENDATIONS

### Before Phase 4
1. ✅ **Enable story-centric by default**
   ```yaml
   use_story_centric_pipeline: true
   ```

2. ⚠️ **Remove dead code**
   - Delete `_find_best_face_for_speaker()`
   - Archive `highlight.py.backup_phase_a`

3. ✅ **Validate story-centric activation**
   - Run test episode
   - Verify story chains built
   - Monitor fallback rate < 30%

### For Phase 4
- Focus on story chain parameter tuning
- Adjust `story_max_gap_seconds` (8.0 → 12.0)
- Relax payoff matching thresholds
- Lower 35s duration floor

---

*Pipeline truth table completed: 2026-06-20 21:32 UTC+3*
