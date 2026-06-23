"""
story_pipeline.py
-----------------
Full Episode -> StoryChains pipeline orchestrator.

This module is the primary entry point for story-centric short-video
candidate generation, replacing candidate-building in highlight.py.

Pipeline
--------
subtitle_segments
  -> DialogueTurns          (dialogue_parser.extract_dialogue_turns)
  -> ConversationBlocks     (conversation_grouper.group_conversations)
  -> StoryFragments         (story_fragments.build_story_fragments)
  -> StoryChains            (story_chain_builder.build_story_chain)
  -> [payoff extension]     (story_chain_builder.try_extend_chain_for_payoff)
  -> [filter + rank]
  -> list[StoryChain]

Public API
----------
build_story_chains_for_episode(subtitle_info, *, cfg, source_id) -> list[StoryChain]
story_chain_to_candidate(chain, *, source) -> dict
"""

from __future__ import annotations

from .conversation_grouper import group_conversations
from .dialogue_parser import extract_dialogue_turns
from .story_chain_builder import (
    StoryChain,
    build_story_chain,
    build_story_summary,
    try_extend_chain_for_payoff,
)
from .story_fragments import StoryFragment, build_story_fragments

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _chain_duration(chain: StoryChain) -> float:
    return max(0.0, float(chain.end) - float(chain.start))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_story_chains_for_episode(
    subtitle_info: dict,
    *,
    cfg: dict | None = None,
    source_id: str = "",
) -> list[StoryChain]:
    """Full pipeline: subtitle_segments -> StoryChains.

    Parameters
    ----------
    subtitle_info:
        Dict with a "segments" key containing a list of subtitle segment dicts.
        Each segment must have at minimum: start (float), end (float), text (str).
        An optional "speaker" key is used for speaker attribution.
    cfg:
        Optional pipeline config dict.  Recognised keys:
        - target_story_min_seconds (float, default 35.0)
        - story_max_gap_seconds    (float, default 2.0)
    source_id:
        Optional identifier for the source episode, forwarded to the
        conversation-ID generator.

    Returns
    -------
    list[StoryChain]
        Ranked list of StoryChain objects.  Complete chains appear first,
        then chains are ordered by completion_score descending.
    """
    subtitle_info = subtitle_info or {}
    cfg = cfg or {}

    min_seconds: float = float(cfg.get("target_story_min_seconds", 35.0))
    # PHASE 4: Increased from 2.0 to 3.5 to prevent natural pause fragmentation
    max_gap: float = float(cfg.get("story_max_gap_seconds", 3.5))

    # 1. Extract dialogue turns
    raw_segments = list(subtitle_info.get("segments", None) or [])
    if not raw_segments:
        return []

    turns = extract_dialogue_turns(raw_segments)
    if not turns:
        return []

    # 2. Group turns into ConversationBlocks
    all_blocks = group_conversations(
        turns, max_gap_seconds=max_gap, source_id=source_id
    )
    if not all_blocks:
        return []

    # 3. Build a StoryChain for each block
    chains: list[StoryChain] = []
    for block in all_blocks:
        block_turns = list((block or {}).get("turns", None) or [])
        if not block_turns:
            continue
        conversation_id = str((block or {}).get("conversation_id", "") or "")
        fragments = build_story_fragments(block_turns)
        if not fragments:
            continue
        chain = build_story_chain(fragments, conversation_id=conversation_id)

        # Attach summary
        source_text = " ".join(
            str(t.get("text", "") or t.get("caption_text", "") or "")
            for t in block_turns
        )
        summary = build_story_summary(
            fragments,
            conversation_id=conversation_id,
            source_text=source_text,
        )
        chain.summary = summary
        chains.append(chain)

    # 4. For each incomplete chain, attempt payoff extension from sibling blocks
    extended_chains: list[StoryChain] = []
    for chain in chains:
        if not chain.is_complete:
            chain = try_extend_chain_for_payoff(chain, all_blocks)
        extended_chains.append(chain)

    # PHASE 4: Replace hard duration floor with quality-aware rescue
    # Minimum absolute floor: 6s (micro-fragments rejected)
    # Range 6-35s: apply weighted penalty, don't hard-reject
    min_dur = min(35.0, min_seconds)
    
    filtered: list[StoryChain] = []
    rescued_short_chains: list[StoryChain] = []
    
    for c in extended_chains:
        duration = _chain_duration(c)
        
        # Hard reject only micro-fragments (< 6s)
        if duration < 6.0:
            continue
        
        # Accept chains >= min_dur normally
        if duration >= min_dur:
            filtered.append(c)
            continue
        
        # Rescue short chains (6-35s) with high completion
        # Require completion_score >= 0.75 OR is_complete=True
        if c.completion_score >= 0.75 or c.is_complete:
            rescued_short_chains.append(c)
    
    # Merge rescued chains into filtered list
    filtered.extend(rescued_short_chains)
    
    # Emergency fallback: if still empty, keep any non-empty chain >= 10s
    if not filtered and extended_chains:
        filtered = [c for c in extended_chains if c.fragments and _chain_duration(c) >= 10.0]

    # PHASE 4: Enhanced ranking with continuation priority
    # 1. Complete chains (is_complete=True)
    # 2. High-completion chains (score >= 0.75)
    # 3. Extended chains (search_extended=True) — continuation wins
    # 4. Then by completion_score descending
    filtered.sort(
        key=lambda c: (
            1 if c.is_complete else 0,
            1 if float(c.completion_score) >= 0.75 else 0,
            1 if c.search_extended else 0,
            float(c.completion_score),
            _chain_duration(c),  # Tie-breaker: prefer longer
        ),
        reverse=True,
    )

    return filtered


def story_chain_to_candidate(
    chain: StoryChain,
    *,
    source: str = "story_pipeline",
) -> dict:
    """Convert a StoryChain to a candidate dict compatible with highlight.py rendering.

    Parameters
    ----------
    chain:
        A StoryChain object produced by build_story_chains_for_episode.
    source:
        Label for the originating pipeline step, forwarded to the candidate dict.

    Returns
    -------
    dict
        Candidate dict with all fields expected by the highlight rendering layer.
    """
    start = round(float(chain.start), 3)
    end = round(float(chain.end), 3)
    duration = round(max(0.0, end - start), 3)

    return {
        # Timing
        "start": start,
        "end": end,
        "duration": duration,
        # Provenance
        "source": source,
        "story_unit_type": "story_chain",
        # Scores
        "story_clarity_score": round(float(chain.completion_score), 4),
        "score": round(float(chain.completion_score), 4),
        "score_breakdown": {
            "completion_score": round(float(chain.completion_score), 4),
            "is_complete": bool(chain.is_complete),
            "search_extended": bool(chain.search_extended),
            "arc_shape": chain.story_arc_shape,
            "hook_filled": bool(chain.hook),
            "setup_filled": bool(chain.setup),
            "escalation_filled": bool(chain.escalation),
            "payoff_filled": bool(chain.payoff),
        },
        # Narrative content
        "story_summary": chain.summary.to_dict() if chain.summary else {},
        "story_chain": chain.to_dict(),
        "story_fragments": [f.to_dict() for f in (chain.fragments or [])],
        # Metadata
        "estimated_turns": len(chain.fragments),
        "hook_gap": 0.0,
        "tail_gap": 0.0,
        "story_completion_score": round(float(chain.completion_score), 4),
        "is_complete": bool(chain.is_complete),
        "speakers": list(chain.speakers),
        "search_extended": bool(chain.search_extended),
    }
