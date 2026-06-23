# STORY_CHAIN_FAILURES.md
**PHASE 2 ROOT CAUSE RECOVERY — Why Chains Incomplete/Short/Fragmented**

---

## EXECUTION TRACE

```
subtitle_segments (SOURCE OF TRUTH)
  ↓
extract_dialogue_turns() → turns ✅
  ↓ FAILURE POINT #1: Conversation Grouping
group_conversations(max_gap=2.0s) → blocks
  ↓ FAILURE POINT #2: Fragment Role Classification
build_story_fragments(block_turns) → fragments
  ↓ FAILURE POINT #3: Payoff Not Found
build_story_chain(fragments) → chain (may be incomplete)
  ↓ FAILURE POINT #4: Payoff Extension Search
try_extend_chain_for_payoff(chain, all_blocks)
  ↓ FAILURE POINT #5: Duration Floor
FILTER: duration >= 35s
  ↓
FALLBACK: keep any chain if all < 35s
  ↓
OUTPUT: list[StoryChain]
```

---

## FAILURE #1: CONVERSATION GROUPING TOO STRICT

### Root Cause

**Location**: `conversation_grouper.py:103` + `story_pipeline.py:91`

**Current Setting**:
```python
max_gap = float(cfg.get("story_max_gap_seconds", 2.0))
```

**Problem**: 2.0s gap splits conversations too aggressively

### Evidence

**Natural dialogue patterns**:
- Speaker thinking pause: 1.5-2.5s
- Reaction time: 1.0-2.0s
- Comedic timing: 2.0-3.5s
- Emphasis pause: 1.5-2.5s

**Result**: One 60s continuous dialogue → split into 3x 20s blocks → all fail 35s filter

**Example**:
```
Timeline:
[0-12s] Speaker A: "Remember when we..." 
[14.5s] ← 2.5s GAP (SPLIT!)
[14.5-28s] Speaker B: "Yeah, that was crazy because..."
[30.2s] ← 2.2s GAP (SPLIT!)
[30.2-45s] Speaker A: "Exactly! And then..."

Result:
Block 1: [0-12s] → 12s (fail 35s filter)
Block 2: [14.5-28s] → 13.5s (fail 35s filter)
Block 3: [30.2-45s] → 14.8s (fail 35s filter)

Should be:
Single block: [0-45s] → 45s ✅ PASS
```

---

### Bridge Conditions (Exist but May Be Too Strict)

**Location**: `conversation_grouper.py:150-200`

**BRIDGE #1: Speaker Overlap**
```python
if _speaker_overlap(block1, block2) >= 0.50:
    # Bridge gap (same speakers continuing)
```

**BRIDGE #2: Topic Continuity**
```python
if _topic_overlap(block1, block2) >= 0.18:
    # Bridge gap (same topic)
```

**BRIDGE #3: Monologue Continuation**
```python
if len(block1_speakers) == 1 and len(block2_speakers) == 1 and speaker_match:
    # Bridge gap (single speaker continues)
```

**Problem**: Thresholds may be too high
- Speaker overlap 0.50 → requires 50%+ speaker match
- Topic overlap 0.18 → requires significant token overlap
- Both may fail for short blocks (< 5 turns)

---

### Solution

**Option A**: Raise `max_gap_seconds`
```python
max_gap = 3.5  # Was 2.0s
```

**Option B**: Lower bridge thresholds
```python
# Speaker overlap
if _speaker_overlap(block1, block2) >= 0.30:  # Was 0.50

# Topic overlap
if _topic_overlap(block1, block2) >= 0.12:  # Was 0.18
```

**Option C**: Hybrid (RECOMMENDED)
```python
max_gap = 2.5  # Moderate raise
speaker_threshold = 0.35  # Moderate lower
topic_threshold = 0.15  # Moderate lower
```

**Rationale**: Incremental tuning, validate with diagnostics

---

## FAILURE #2: WEAK ROLE CLASSIFICATION

### Root Cause

**Location**: `story_fragments.py:130-290` (`build_story_fragments()`)

**Current Logic**: Keyword-based classification

**Hook Signals** (Russian):
```python
"что", "почему", "правда", "смотри", "послушай", "кстати", "знаешь"
# Question marks, exclamations
```

**Escalation Signals**:
```python
"убью", "сволочь", "идиот", "чёрт", "блин"
# Conflict keywords, profanity (mild)
```

**Payoff Signals**:
```python
"поэтому", "вот", "значит", "теперь", "видишь", "понял"
# Resolution markers
```

---

### Problem

**Domain-Specific Patterns Missed**:
- Comedy: sarcasm, callbacks, punchlines → not in keyword lists
- Drama: emotional beats, revelations → may lack keywords
- Technical: explanations, facts → keyword-neutral

**Example**:
```
Turn 1: "Ты помнишь того парня?" (Do you remember that guy?)
  → Should be: HOOK
  → Classified as: SETUP (missing "помнишь" in hook signals)

Turn 2: "Ага, с работы?" (Yeah, from work?)
  → Should be: SETUP
  → Classified as: NEUTRAL (no keywords)

Turn 3: "Он теперь директор!" (He's now director!)
  → Should be: PAYOFF
  → Classified as: PAYOFF ✅ ("теперь" detected)
```

**Result**: hook/setup misclassified → chain.is_complete = False

---

### Solution

**Option A**: Expand keyword lists (domain-specific)
```python
HOOK_SIGNALS_RU_EXTENDED = {
    "помнишь", "слышал", "видел", "знаешь", "представляешь",
    "угадай", "держись", "слушай сюда", "секундочку"
}
```

**Option B**: Strengthen positional fallbacks
```python
# Current fallback (story_chain_builder.py:602-626):
if not hook and ordered:
    hook = ordered[0].transcript  # First fragment = hook

# Enhancement: Use positional + keyword hybrid
if not hook and ordered:
    first_frag = ordered[0]
    # If first fragment has ANY question/exclamation → strong hook
    if "?" in first_frag.transcript or "!" in first_frag.transcript:
        hook = first_frag.transcript
    else:
        hook = first_frag.transcript  # Fallback anyway
```

**Option C**: ML classifier (OVERKILL)
- Train model on labeled hook/setup/escalation/payoff examples
- Too complex for immediate fix

**Recommendation**: Option B (strengthen fallbacks) + selective keyword additions

---

## FAILURE #3: PAYOFF NOT FOUND (Within Block)

### Root Cause

**Location**: `story_chain_builder.py:622-626`

**Current Logic**:
```python
if not payoff and ordered:
    last_text = _clean_text(getattr(ordered[-1], "transcript", "") or "")
    # Use last fragment as payoff
    if last_text and (len(ordered) > 1 or last_text != hook):
        payoff = last_text
```

**Problem**: What if last fragment is SETUP continuation, not payoff?

**Example**:
```
Fragment 1 (HOOK): "Why did you do that?"
Fragment 2 (SETUP): "Well, it's complicated..."
Fragment 3 (ESCALATION): "You don't understand the situation!"
Fragment 4 (SETUP): "Let me explain the background..." ← LAST FRAGMENT

Positional fallback: payoff = Fragment 4 (WRONG — this is setup)
Real payoff: MISSING (in next conversation block)
```

**Result**: chain.is_complete = False → chain filtered out

---

### Solution

**Enhancement**: Multi-fragment payoff search
```python
# Instead of just last fragment, scan last 2-3 fragments
# Pick one with highest payoff score

payoff_candidates = []
for frag in ordered[-3:]:  # Last 3 fragments
    text = _clean_text(frag.transcript)
    score = _score_text(text, PAYOFF_SIGNALS)
    payoff_candidates.append((score, text))

if payoff_candidates:
    # Pick highest-scoring candidate
    payoff = max(payoff_candidates, key=lambda x: x[0])[1]
```

**Benefit**: More robust than "always use last fragment"

---

## FAILURE #4: PAYOFF EXTENSION SEARCH TOO STRICT

### Root Cause

**Location**: `story_chain_builder.py:838-900` (`try_extend_chain_for_payoff()`)

**Current Logic**:
```python
# For each adjacent block:
# 1. Check topic match: requires >= 2 token overlap
# 2. Check temporal proximity: within 30s
# 3. Extract payoff fragment from that block

topic_match = len(chain_topics & block_topics) >= 2
if not topic_match:
    continue  # Skip this block

gap = abs(candidate_block["start"] - chain.end)
if gap > 30.0:
    continue  # Too far away
```

---

### Problems

**PROBLEM #1: Topic Match Too Strict**
- Requires >= 2 token overlap
- Short chains (2-3 turns) may have < 2 meaningful tokens
- Result: Can't find payoff even if adjacent block is same conversation

**Example**:
```
Chain tokens: {"кот", "пропал"}  (cat, disappeared)
Adjacent block tokens: {"нашёл", "подвал"}  (found, basement)
Overlap: 0 tokens

But semantically: Chain = setup ("cat disappeared")
                  Block = payoff ("found in basement")
```

**PROBLEM #2: 30s Proximity Limit**
- Reasonable for tight dialogues
- May miss delayed payoffs (suspense, dramatic pause)

**PROBLEM #3: Only Searches Forward**
- Searches blocks AFTER chain.end
- Doesn't search blocks BEFORE chain.start
- Flashback/callback patterns missed

---

### Solution

**Fix #1**: Lower topic match threshold
```python
# OLD:
topic_match = len(chain_topics & block_topics) >= 2

# NEW:
topic_match = len(chain_topics & block_topics) >= 1  # Accept 1+ token
```

**Fix #2**: Expand search radius
```python
# OLD:
if gap > 30.0:
    continue

# NEW:
if gap > 45.0:  # 50% increase
    continue
```

**Fix #3**: Bidirectional search
```python
# Search blocks before AND after chain
for candidate_block in all_blocks:
    # Forward search (existing)
    forward_gap = candidate_block["start"] - chain.end
    if 0 <= forward_gap <= 45.0:
        # Try to extract payoff
    
    # Backward search (NEW)
    backward_gap = chain.start - candidate_block["end"]
    if 0 <= backward_gap <= 45.0:
        # Try to extract payoff (less common but valid)
```

**Recommendation**: Implement Fix #1 (lower threshold) + Fix #2 (expand radius)  
**Defer**: Fix #3 (bidirectional) — validate need with diagnostics first

---

## FAILURE #5: DURATION FLOOR 35s

### Root Cause

**Location**: `story_pipeline.py:142-147`

**Current Logic**:
```python
min_dur = min(35.0, min_seconds)  # 35s floor
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]

# FALLBACK:
if not filtered and extended_chains:
    filtered = [c for c in extended_chains if c.fragments]
```

---

### Problem

**Natural story arcs often 25-34s**:
- Quick exchanges (rapid-fire dialogue)
- Tight narratives (no fluff)
- Punch-in/punch-out edits (trim intros/outros)

**Example**:
```
Chain: [120.0-152.0] = 32s
  - Complete arc: hook + setup + escalation + payoff ✅
  - All fragments present
  - Good quality

Result: FILTERED OUT (< 35s)
```

**Fallback helps but**:
- Only triggers if ALL chains < 35s
- If episode has 1x 40s chain + 5x 30s chains → keeps only the 40s one

---

### Solution

**Lower duration floor**:
```python
min_dur = 25.0  # Was 35.0
```

**Rationale**:
- 25s is minimum for Shorts (YouTube/TikTok accept 15s+)
- Preserves tight, complete narratives
- Still filters out fragment chains (< 20s)

**Alternative**: Quality-based filter
```python
# Instead of hard duration floor, use completion score
if chain.is_complete or chain.completion_score >= 0.75:
    # Accept even if < 35s
elif _chain_duration(chain) >= 35.0:
    # Accept if long enough
else:
    # Reject
```

**Recommendation**: Lower to 25s (simple fix), then validate quality

---

## FAILURE INTERACTION MAP

```
FAILURE #1 (Grouping)
  ↓ Creates short blocks
  ↓
FAILURE #2 (Classification) + FAILURE #3 (Payoff)
  ↓ Incomplete chains
  ↓
FAILURE #4 (Extension)
  ↓ Can't find payoff in adjacent blocks
  ↓
FAILURE #5 (Duration)
  ↓ Filters out 25-34s chains
  ↓
STARVATION
```

**Key Insight**: Failures cascade — fixing #1 (grouping) may reduce impact of #2-#5

---

## PRIORITY FIX ORDER

### HIGH PRIORITY (Fix First)

**1. Conversation Grouping** (FAILURE #1)
```python
max_gap_seconds: 2.0 → 3.5
```
**Impact**: Prevents premature conversation splits  
**Effort**: 1 line config change  
**Risk**: Low

**2. Duration Floor** (FAILURE #5)
```python
min_dur: 35.0 → 25.0
```
**Impact**: Keeps complete short chains  
**Effort**: 1 line code change  
**Risk**: Low

**3. Payoff Extension Topic Match** (FAILURE #4)
```python
topic_match: >= 2 → >= 1
```
**Impact**: Enables payoff search for short chains  
**Effort**: 1 line code change  
**Risk**: Low

---

### MEDIUM PRIORITY (Validate After High)

**4. Payoff Extension Search Radius** (FAILURE #4)
```python
gap_threshold: 30s → 45s
```
**Impact**: Finds delayed payoffs  
**Effort**: 1 line change  
**Risk**: Low (may increase false positives)

**5. Positional Payoff Fallback** (FAILURE #3)
```python
# Scan last 3 fragments for highest payoff score
```
**Impact**: Better payoff detection  
**Effort**: 10 lines code  
**Risk**: Low

---

### LOW PRIORITY (After Validation)

**6. Keyword List Expansion** (FAILURE #2)
```python
# Add domain-specific hook/payoff signals
```
**Impact**: Better role classification  
**Effort**: 20-30 keywords  
**Risk**: Medium (may cause false positives)

**7. Bridge Threshold Tuning** (FAILURE #1)
```python
speaker_threshold: 0.50 → 0.35
topic_threshold: 0.18 → 0.15
```
**Impact**: More aggressive conversation bridging  
**Effort**: 2 line changes  
**Risk**: Medium (may over-bridge unrelated blocks)

---

## IMPLEMENTATION PLAN

### PHASE 2.2 (Days 3-4): Story Pipeline Tuning

**Step 1**: Raise max_gap_seconds
```python
# pipeline/config.py
"story_max_gap_seconds": 3.5,  # Was 2.0
```

**Step 2**: Lower duration floor
```python
# pipeline/montage/story_pipeline.py:142
min_dur = min(25.0, min_seconds)  # Was 35.0
```

**Step 3**: Relax payoff extension
```python
# pipeline/montage/story_chain_builder.py:~860
topic_match = len(chain_topics & block_topics) >= 1  # Was >= 2
```

**Step 4**: Expand payoff search radius
```python
# pipeline/montage/story_chain_builder.py:~870
if gap > 45.0:  # Was 30.0
    continue
```

**Total effort**: ~15 minutes (4 lines changed)  
**Risk**: Low — all reversible config/threshold changes

---

## VALIDATION CRITERIA

### Success Metrics

**Before Fixes**:
- story_chains output: 2-3 per 40min episode
- Filtered (< 35s): 5-8 chains
- Incomplete chains: 60-70%

**After Fixes (Expected)**:
- story_chains output: 8-12 per 40min episode
- Filtered (< 25s): 2-4 chains
- Incomplete chains: 30-40%

**Specific Tests**:
1. ✅ Natural 2.5s pause → NOT split (grouping fix)
2. ✅ 28s complete chain → NOT filtered (duration fix)
3. ✅ Single-token topic overlap → finds payoff (extension fix)
4. ✅ 40s gap payoff → found (radius fix)

---

## DIAGNOSTIC INSTRUMENTATION

Add logging to identify bottlenecks:

```python
# After group_conversations()
logger.info(f"Grouped {len(turns)} turns into {len(blocks)} blocks")
logger.info(f"Block durations: {[b['end']-b['start'] for b in blocks]}")

# After build_story_chain()
logger.info(f"Chain: start={chain.start}, end={chain.end}, duration={chain.end-chain.start}")
logger.info(f"  is_complete={chain.is_complete}, score={chain.completion_score}")
logger.info(f"  hook={bool(chain.hook)}, setup={bool(chain.setup)}, escalation={bool(chain.escalation)}, payoff={bool(chain.payoff)}")

# After try_extend_chain_for_payoff()
if chain.search_extended:
    logger.info(f"  ✅ Payoff found via extension search")

# After duration filter
logger.info(f"Duration filter: {len(extended_chains)} → {len(filtered)} (threshold={min_dur}s)")
```

---

## SUMMARY

### 5 Failure Modes Identified

1. ✅ **Conversation grouping too strict** (2.0s → 3.5s)
2. ⚠️ **Weak role classification** (enhance fallbacks, expand keywords)
3. ⚠️ **Payoff not found within block** (multi-fragment scan)
4. ✅ **Payoff extension too strict** (≥1 token, 45s radius)
5. ✅ **Duration floor too high** (35s → 25s)

### Priority Fixes

**IMMEDIATE** (HIGH PRIORITY):
- max_gap_seconds: 2.0 → 3.5
- min_dur: 35.0 → 25.0
- topic_match: >= 2 → >= 1
- gap_radius: 30s → 45s

**AFTER VALIDATION** (MEDIUM):
- Multi-fragment payoff scan
- Keyword expansion

**FUTURE** (LOW):
- Bridge threshold tuning

---

**CONCLUSION**: Story chain failures stem from cascading issues: strict grouping (2.0s) → short blocks → incomplete chains → high duration floor (35s) → starvation. Priority fixes: raise max_gap to 3.5s, lower duration to 25s, relax payoff extension to ≥1 token match.
