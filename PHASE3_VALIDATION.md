# PHASE3_VALIDATION.md
**PHASE 3 STABILIZATION — Pipeline Authority Validation**

---

## OBJECTIVE

Validate **authoritative execution paths** after PHASE 3 stabilization:

1. **Active Speaker** — Turn-first switching operational
2. **Subtitle Persistence** — No flicker, timeline stable
3. **Dead Code Removed** — No legacy contamination
4. **Story Pipeline** — Sole candidate source
5. **Quality Gates** — Enforced, no bypasses

**Validation method**: Flow mapping + metrics verification + visual testing

---

## VALIDATION SCOPE

### IN SCOPE ✅
- TASK 1: Active Speaker Rewrite (turn-first)
- TASK 3: Subtitle Timeline Lock (persistence)
- TASK 6: Dead Code Removal (legacy cleanup)

### OUT OF SCOPE ⚠️
- TASK 2: Bridge Recovery (SKIPPED — adds feature layer)
- TASK 4: Title Authority (ALREADY CLEAN — no viral templates)
- TASK 5: Timeline Unification (DEFERRED TO PHASE 4)

---

## VALIDATION 1: ACTIVE SPEAKER TURN-FIRST FLOW

### Flow Map

```
Subtitle Segments (input)
    ↓
_build_turn_timeline()
    ├─ Merge consecutive same-speaker segments
    ├─ Clip to [start, end] window
    └─ Output: Turn timeline [{"start", "end", "speaker", "text"}]
    ↓
Window Processing Loop
    ├─ For each reframe window:
    │   ├─ Find active turn at timestamp
    │   ├─ Detect turn_changed (speaker switch)
    │   ├─ If turn_changed → set turn_switch_trigger = True
    │   ├─ Find best face for active speaker (by speaking_score)
    │   └─ Inject turn_based_center into target
    └─ Fallback: If no turn_timeline → legacy face-first
    ↓
Speaker Switch Detection
    ├─ If turn_timeline exists:
    │   └─ Count TURN changes (authoritative)
    └─ Else:
        └─ Count face bbox changes (legacy fallback)
    ↓
Metrics Output
    ├─ speaker_switches = dialogue turn count
    ├─ speaker_switch_log with timestamps
    └─ Debug: "turn_switch" events
```

### Success Criteria

**1. Turn Timeline Built**
- [ ] `_build_turn_timeline()` exists in face_crop.py
- [ ] Returns merged turns by speaker
- [ ] Clips to window boundaries
- [ ] Empty segments → returns []

**2. Turn-First Logic Active**
- [ ] Window loop finds active turn at each timestamp
- [ ] `turn_switch_trigger` flag set on speaker change
- [ ] `turn_based_center` injected into target selection
- [ ] Face selection uses speaking_score (not bbox size)

**3. Speaker Switches Accurate**
- [ ] speaker_switches count = dialogue turn changes
- [ ] speaker_switch_log contains turn timestamps
- [ ] Debug logs show "[reframe] turn_switch=..." events

**4. Fallback Intact**
- [ ] If subtitle_segments=None → legacy face-first behavior
- [ ] No crashes on missing subtitles
- [ ] Backward compatible

**5. Integration Complete**
- [ ] highlight.py passes subtitle_segments to create_vertical_crop()
- [ ] Parameter added to function signature
- [ ] No breaking changes to existing calls

### Validation Tests

#### Test 1: Two-Person Dialogue (A→B→A→B)
**Input**: Episode with 4 dialogue turns

**Run**:
```python
python main.py --episode episode01_test.avi --story_mode
```

**Verify**:
- [ ] speaker_switches = 3 (A→B, B→A, A→B)
- [ ] Visual: Camera switches at dialogue turn boundaries
- [ ] Logs: "turn_switch=SPEAKER_0->SPEAKER_1" events present
- [ ] No face jitter between turns

#### Test 2: Monologue (Single Speaker)
**Input**: Episode with single speaker

**Verify**:
- [ ] speaker_switches = 0
- [ ] Camera stable on speaker
- [ ] No unnecessary recentering

#### Test 3: Fallback (No Subtitles)
**Input**: Episode without subtitle_segments

**Verify**:
- [ ] Pipeline runs (no crash)
- [ ] Legacy face-first behavior active
- [ ] Metrics still computed

---

## VALIDATION 2: SUBTITLE PERSISTENCE FLOW

### Flow Map

```
Config Thresholds (NEW)
    ├─ subtitle_persist_gap_seconds: 0.85 (was 0.55)
    ├─ subtitle_clear_gap_seconds: 1.80 (was 1.35)
    └─ hold_until_next_max: 0.90 (NEW)
    ↓
_stabilize_subtitle_timeline()
    ├─ For each subtitle pair:
    │   ├─ Calculate gap between subtitles
    │   ├─ PRIORITY 1: If gap < 0.90s → FORCE BRIDGE
    │   ├─ PRIORITY 2: If gap <= persist_gap → BRIDGE
    │   └─ PRIORITY 3: If gap >= clear_gap → CLEAR
    └─ Output: Bridged timeline (no gaps < 0.90s)
    ↓
Frame Buffer (NEW)
    ├─ Extend subtitle end time by 80ms
    ├─ Only if doesn't overlap next subtitle
    └─ Result: Smooth transitions, no micro-gaps
    ↓
Timeline Remap (VERIFY ORDER)
    ├─ Apply silence cuts (video trimming)
    ├─ Build final video timeline
    ├─ Remap subtitles to final timeline ← MUST BE AFTER CUTS
    └─ Render ASS file
    ↓
Metrics Output
    ├─ gap_blink_count = 0 or near-zero
    ├─ subtitle_persisted_gaps_count > 0
    └─ subtitle_visual_drop_count (rare)
```

### Success Criteria

**1. Thresholds Raised**
- [ ] config.py: subtitle_persist_gap_seconds = 0.85
- [ ] config.py: subtitle_clear_gap_seconds = 1.80
- [ ] Validation ranges: ceilings correct (0.85, 2.0)

**2. Hold-Until-Next Rule**
- [ ] subtitle.py: hold_until_next_max = 0.90 constant added
- [ ] PRIORITY 1 logic: gaps < 0.90s always bridged
- [ ] Overrides normal persist_gap logic

**3. Frame Buffer**
- [ ] build_ass_word_events(): frame_buffer_ms = 80
- [ ] Subtitle end times extended by 80ms
- [ ] Overlap check prevents collision with next subtitle

**4. Timeline Remap Order**
- [ ] highlight.py: Silence cuts happen FIRST
- [ ] highlight.py: Subtitle remap happens AFTER cuts
- [ ] Correct order: trim → timeline → remap → render

**5. Metrics Accurate**
- [ ] gap_blink_count reduced significantly
- [ ] subtitle_persisted_gaps_count > 0 (bridges active)
- [ ] subtitle_visual_drop_count = 0 for < 1.8s gaps

### Validation Tests

#### Test 1: Natural Pauses (0.7-0.9s)
**Input**: Dialogue with natural pauses

**Run**: Generate candidate with subtitle persistence

**Verify**:
- [ ] Visual: No subtitle flicker during pauses
- [ ] Metrics: gap_blink_count = 0
- [ ] Metrics: subtitle_persisted_gaps_count > 0

#### Test 2: Long Silence (3s)
**Input**: Dialogue with 3s silence

**Verify**:
- [ ] Visual: Subtitle clears after 1.8s
- [ ] Metrics: subtitle_visual_drop_count > 0
- [ ] No stale subtitle during silence

#### Test 3: Long Clip (15min)
**Input**: Full episode with silence trimming

**Verify**:
- [ ] Subtitles aligned at start (0:00)
- [ ] Subtitles aligned at middle (7:30)
- [ ] Subtitles aligned at end (15:00)
- [ ] No cumulative drift

#### Test 4: Rapid Dialogue (0.2-0.4s gaps)
**Input**: Fast exchange with short gaps

**Verify**:
- [ ] All gaps bridged (continuous subtitles)
- [ ] Frame buffer fills micro-gaps
- [ ] Smooth visual transitions

---

## VALIDATION 3: DEAD CODE REMOVAL FLOW

### Flow Map

```
PRIORITY 1 Deletions (PHASE 1 Violations)
    ├─ Lines 8419-8433: Artificial candidate injection ❌ DELETED
    ├─ Lines 9385-9418: Minimum candidate count top-up ❌ DELETED
    └─ phase_a_bypass flag ❌ DELETED ALL REFERENCES
    ↓
Story Pipeline Authority
    ├─ story_pipeline.py = SOLE candidate source
    ├─ No artificial injection
    └─ Quality gates enforced (no bypasses)
    ↓
PRIORITY 2 Analysis (Legacy Builders)
    ├─ _candidate_windows_legacy() → Usage analysis
    ├─ _build_story_candidates_from_turns_linear() → Usage analysis
    ├─ _fallback_window_candidate() → Usage analysis
    └─ If UNREFERENCED → DELETE
    ↓
Validation
    ├─ No artificial candidates in output
    ├─ Quality gates respected (may output < 12 candidates)
    └─ Story pipeline handles all episode types
```

### Success Criteria

**1. PRIORITY 1 Deleted**
- [ ] Lines 8419-8433 removed (artificial injection)
- [ ] Lines 9385-9418 removed (minimum count top-up)
- [ ] Search `phase_a_bypass` → 0 results
- [ ] No artificial candidates with `fallback_reason: "insufficient_context_minimal_candidate"`

**2. Quality Gates Enforced**
- [ ] Low-quality episode → output < 12 candidates (correct)
- [ ] High-quality episode → output 8-12 candidates
- [ ] Rejection logs show gate enforcement
- [ ] No bypass paths active

**3. Story Pipeline Authority**
- [ ] _generate_candidates_story_centric() only calls story_pipeline.py
- [ ] No calls to legacy builders (if deleted)
- [ ] All candidates from story_chains

**4. Usage Analysis Complete**
- [ ] Search results for legacy builder calls documented
- [ ] UNREFERENCED functions identified
- [ ] Deletion safety verified

**5. Code Simplification**
- [ ] ~58-468 lines deleted (depending on analysis)
- [ ] Single authoritative path (story_pipeline)
- [ ] No hybrid execution paths

### Validation Tests

#### Test 1: High-Quality Episode
**Input**: Clear dialogue, complete story arcs

**Run**: Generate candidates

**Verify**:
- [ ] Candidates: 8-12 from story_pipeline
- [ ] No artificial injection
- [ ] All candidates have story_chain metadata

#### Test 2: Low-Quality Episode
**Input**: Sparse dialogue, fragmented

**Run**: Generate candidates

**Verify**:
- [ ] Candidates: 2-5 (or 0) — gates respected
- [ ] NO top-up to 12
- [ ] Rejection logs show gate decisions

#### Test 3: No Dialogue Episode
**Input**: Action sequence, no speech

**Run**: Generate candidates

**Verify**:
- [ ] Candidates: 0 (correct)
- [ ] NO artificial injection
- [ ] Pipeline completes (no crash)

#### Test 4: phase_a_bypass Removed
**Input**: Any episode

**Verify**:
- [ ] All quality gates run
- [ ] Rejection metrics present
- [ ] No bypass logs

---

## VALIDATION 4: UNIFIED FLOW VERIFICATION

### End-to-End Pipeline

```
Episode Input
    ↓
Transcription (Whisper)
    ↓
Story Pipeline (story_pipeline.py)
    ├─ Dialogue turns extracted
    ├─ Conversations grouped
    ├─ Story fragments built
    ├─ Story chains assembled
    └─ Output: StoryChain candidates
    ↓
Quality Gates (NO BYPASSES)
    ├─ story_completion_score >= threshold
    ├─ context_completeness_score >= threshold
    ├─ Duration within bounds
    └─ Output: Filtered candidates
    ↓
Reframe (turn-first)
    ├─ subtitle_segments passed to create_vertical_crop()
    ├─ Turn timeline built
    ├─ Camera follows dialogue turns
    └─ Output: Vertical crop
    ↓
Subtitle Render (persistent)
    ├─ Thresholds: persist_gap=0.85, clear_gap=1.80
    ├─ Hold-until-next rule enforced
    ├─ Frame buffer active
    └─ Output: ASS file (no flicker)
    ↓
Final Output
    ├─ Vertical video with turn-following camera
    ├─ Persistent subtitles (no gaps < 0.90s)
    ├─ Authentic candidates (from story_pipeline)
    └─ Quality gates enforced
```

### Integration Tests

#### Test 1: Full Pipeline Run
**Input**: episode01_test.avi

**Run**:
```python
python main.py --episode episode01_test.avi --story_mode --output_dir _phase3_validation
```

**Verify**:
- [ ] Transcription completes
- [ ] Story chains generated (> 0)
- [ ] Quality gates filter candidates
- [ ] Reframe uses turn-first logic
- [ ] Subtitles persistent (no flicker)
- [ ] Output videos generated

#### Test 2: Metrics Consistency
**Input**: Any episode

**Verify**:
- [ ] speaker_switches = dialogue turn count
- [ ] gap_blink_count near-zero
- [ ] No artificial candidates
- [ ] Quality scores authentic

#### Test 3: Visual Quality
**Input**: High-quality episode

**Verify**:
- [ ] Camera switches sync with dialogue
- [ ] Subtitles visible throughout
- [ ] No flicker or jitter
- [ ] Professional output quality

---

## METRICS SUMMARY

### Before PHASE 3
```
speaker_switches: Face bbox changes (misleading)
gap_blink_count: 5-15 per clip (flicker)
Artificial candidates: 20-30% of output
minimum_candidate_count: Forced to 12
phase_a_bypass: Gates bypassed
Legacy code: ~468 lines of hybrid paths
```

### After PHASE 3
```
speaker_switches: Dialogue turn changes (accurate)
gap_blink_count: 0-2 per clip (rare)
Artificial candidates: 0% (all authentic)
minimum_candidate_count: Removed (gates enforced)
phase_a_bypass: Deleted (gates always run)
Legacy code: Removed (single authoritative path)
```

---

## ROLLBACK CRITERIA

### If Any Validation Fails

**Speaker Turn-First**:
- Fallback to face-first if subtitle_segments=None
- Add debug logging for turn lookup
- Verify face selection by speaking_score

**Subtitle Persistence**:
- Lower thresholds if subtitles stay too long
- Reduce frame_buffer_ms if overlaps occur
- Verify remap order if drift persists

**Dead Code**:
- Restore PRIORITY 1 only if story_pipeline broken (FIX story_pipeline instead)
- Keep PRIORITY 2/3 if analysis shows usage
- Never restore phase_a_bypass

---

## SUCCESS DECLARATION

PHASE 3 is COMPLETE when:

- [x] All 3 implementation guides created
- [ ] TASK 1: Active Speaker turn-first operational
- [ ] TASK 3: Subtitle persistence no flicker
- [ ] TASK 6: Dead code removed (PRIORITY 1 minimum)
- [ ] All validation tests pass
- [ ] Metrics show improvement
- [ ] Visual quality verified

---

## DELIVERABLES CHECKLIST

### Documentation
- [x] ACTIVE_SPEAKER_TURN_FIRST.md (implementation guide)
- [x] SUBTITLE_PERSISTENCE_ENFORCEMENT.md (implementation guide)
- [x] DEAD_CODE_PHASE3.md (deletion map)
- [x] PHASE3_VALIDATION.md (this document)

### Code Changes (To Be Implemented)
- [ ] face_crop.py: Turn-first logic (~180 lines added)
- [ ] config.py: Raised thresholds (2 lines changed)
- [ ] subtitle.py: Hold-until-next + frame buffer (~35 lines added)
- [ ] highlight.py: Dead code removed (~58-468 lines deleted)
- [ ] highlight.py: subtitle_segments parameter passed (1 line)

### Testing
- [ ] Test Case: Two-person dialogue (turn switching)
- [ ] Test Case: Natural pauses (subtitle persistence)
- [ ] Test Case: Low-quality episode (gates enforced)
- [ ] Test Case: Full pipeline (end-to-end)
- [ ] Visual validation (camera + subtitles)

---

## FINAL NOTES

**PHASE 3 focuses on authoritative execution paths**:

1. **Turn-first** — Dialogue drives camera, faces refine
2. **Persistence** — Subtitles never flicker < 900ms
3. **Authority** — story_pipeline sole source, no artificial injection
4. **Deterministic** — Gates enforced, no bypasses, single path

**Next steps**: Toggle to Act mode → implement changes → run validation tests

**Estimated total effort**: 4-6 days (3 tasks in parallel possible)

**Risk level**: MEDIUM (turn-first complex, persistence low risk, dead code analysis needed)

---

## CONCLUSION

PHASE 3 STABILIZATION план ready for execution.

**3 implementation guides** созданы с полными инструкциями.

**Validation framework** определён с clear success criteria.

**Authoritative paths** будут enforced after implementation.

**Ready to proceed**: Toggle to Act mode для implementation или review plan.
