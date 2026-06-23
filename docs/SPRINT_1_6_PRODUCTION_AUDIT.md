# Sprint 1.6 Production Audit

**Date:** 2026-06-15  
**Goal:** Identify and fix downstream blockers preventing Short generation  
**Status:** 🔴 IN PROGRESS

---

## Current State

### What Works ✅
- Episode transcription: 599 segments
- Story chain generation: 12 candidates
- Semantic window boundaries detected
- No legacy fallback triggered

### What Blocks ❌
- **0 MP4 outputs generated**
- Story chains too long (3-8 minutes)
- All candidates rejected at visual stage
- Ranking timeouts
- Export crash in legacy mode

---

## Priority A: Story Chain Compression

### Current Problem
Story chains are excessively long for Shorts format:
- 219s (3.6 min)
- 160s (2.7 min)
- 129s (2.2 min)
- **479s (8 min)** ← Longest

### Investigation Plan

1. **Analyze chain building logic**
   - Where does `build_story_chains_for_episode()` stop expansion?
   - What triggers chain boundary detection?
   - Why does it continue past natural payoffs?

2. **Map narrative structure in chains**
   For each generated chain, identify:
   - Hook position (timestamp)
   - Conflict position
   - Escalation position
   - Payoff position
   - Filler dialogue after payoff

3. **Implement payoff detection**
   - Add story arc completion signals
   - Stop expansion after detected payoff
   - Limit max duration to 90s

4. **Test compression**
   - Re-run validation with fixes
   - Target: 30-90s chains
   - Verify narrative completeness

### Success Criteria
- [ ] Chain building logic documented
- [ ] Narrative positions mapped for 3+ chains
- [ ] Payoff detection implemented
- [ ] Average chain length < 90s
- [ ] Chains still semantically complete

---

## Priority B: Visual Subject Rejections

### Current Problem
67% of story candidates rejected with `no_visual_subject`:
- 4 out of 6 top candidates
- Longer segments = harder to track faces
- Rejection thresholds unknown

### Investigation Plan

1. **Collect rejection data**
   For each `no_visual_subject` rejection, extract:
   ```python
   {
       "candidate_id": "...",
       "duration": 219.0,
       "face_coverage_percent": 0.45,
       "tracked_speaker_coverage_percent": 0.32,
       "num_detected_faces": 2.3,  # average
       "avg_face_size_pixels": 180,
       "active_speaker_confidence": 0.68,
       "rejection_threshold": {
           "min_face_coverage": 0.70,
           "min_speaker_coverage": 0.60,
           "min_speaker_confidence": 0.75
       },
       "failed_checks": ["face_coverage", "speaker_coverage"]
   }
   ```

2. **Profile visual tracking per chain**
   - Sample frames every 5 seconds
   - Measure face detection continuity
   - Identify dropouts (no face detected)
   - Compare short chains (54s) vs long chains (479s)

3. **Scene change analysis**
   - Do long chains span multiple scenes?
   - Should chains break at scene boundaries?
   - Would scene-aware splitting help?

4. **Generate visual rejection report**
   - Summary statistics for all rejections
   - Comparison: story mode vs legacy mode
   - Threshold recommendations (not blind lowering)

### Success Criteria
- [ ] All rejections profiled with metrics
- [ ] Visual tracking continuity analyzed
- [ ] Scene change correlation documented
- [ ] Threshold recommendations with justification
- [ ] At least 1 candidate passes visual gates

---

## Priority C: Ranking Performance

### Current Problem
Multiple timeout warnings:
```
[warning] Ranking timeout for story 2.92-113.32
[ranking] Still scoring story... 30s
[ranking] Semantic preview... 60s
[warning] slow_stage_detected stage=ranking elapsed=60s
```

### Investigation Plan

1. **Profile ranking components**
   Add timing instrumentation:
   ```python
   profiling_results = {
       "embeddings_ms": 1234,
       "topic_extraction_ms": 5678,
       "llm_scoring_ms": 45000,  # Suspect
       "semantic_preview_ms": 30000,  # Suspect
       "story_summarization_ms": 890
   }
   ```

2. **Identify bottlenecks**
   - Which component causes 30s+ delays?
   - Is it LLM API calls?
   - Is it embedding generation?
   - Is it redundant processing?

3. **Measure optimization opportunities**
   - Can we batch LLM requests?
   - Can we cache semantic previews?
   - Can we parallelize ranking?
   - Can we use faster models for ranking?

4. **Document findings**
   - Bottleneck root causes
   - Estimated speedup from each optimization
   - Priority order for fixes

### Success Criteria
- [ ] All ranking components timed
- [ ] Bottlenecks identified with evidence
- [ ] Optimization opportunities quantified
- [ ] Performance report generated
- [ ] Ranking completes in < 10s per candidate

---

## Priority D: Export Stability

### Current Problem
Legacy mode crashes during export:
```
[failed] cannot access local variable 'meta' where it is not associated with a value
```

### Investigation Plan

1. **Locate crash site**
   - Find exact line in export code
   - Understand what `meta` should contain
   - Identify why it's uninitialized

2. **Reproduce crash**
   - Run minimal test case
   - Capture full stack trace
   - Determine if story mode has same issue

3. **Fix initialization**
   - Ensure `meta` is defined before use
   - Add defensive checks
   - Test both legacy and story modes

4. **Verify end-to-end**
   - Generate at least 1 MP4 output
   - Verify video plays correctly
   - Check subtitle rendering

### Success Criteria
- [ ] Crash site located with line number
- [ ] Root cause understood
- [ ] Fix applied and tested
- [ ] At least 1 MP4 generated successfully
- [ ] Both modes can export without crashes

---

## Execution Order

1. **Priority D (Export Stability)** - FIRST
   - Must be able to generate outputs
   - Required for testing A, B, C fixes
   - Estimated time: 1-2 hours

2. **Priority A (Story Chain Compression)** - SECOND
   - Reduces load on visual tracking
   - Makes chains more suitable for Shorts
   - Estimated time: 3-4 hours

3. **Priority B (Visual Subject Rejections)** - THIRD
   - Analyze compressed chains
   - More relevant data with 30-90s chains
   - Estimated time: 2-3 hours

4. **Priority C (Ranking Performance)** - FOURTH
   - Profile with compressed chains
   - Lower priority than generation issues
   - Estimated time: 2-3 hours

**Total estimated time: 8-12 hours**

---

## Success Metrics

Before Sprint 1.7 can begin, ALL must be TRUE:

- [ ] At least 1 story candidate reaches export
- [ ] At least 1 MP4 file generated
- [ ] Average story chain duration: 30-90s
- [ ] Visual rejection causes fully documented
- [ ] Ranking stage profiled and documented
- [ ] All 4 priorities completed

---

## Next Steps

1. Start with Priority D (export crash fix)
2. Run minimal test to reproduce
3. Apply fix
4. Verify MP4 generation
5. Move to Priority A

---

**Audit Owner:** Kiro AI  
**Target Completion:** Before Sprint 1.7  
**Current Phase:** Priority D - Export Stability
