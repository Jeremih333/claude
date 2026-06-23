"""
story_builder.py
----------------
Build a StoryPlan from a single ConversationBlock using semantic StoryChains.

The old temporal window approach (build_story_window_plan / 14%/32%/78% cuts)
has been removed. story_window_segments are now derived from ACTUAL fragment
boundaries, not from fixed time percentages.

Public API
----------
build_story_plan(block, *, min_seconds, max_seconds) -> dict
"""

from __future__ import annotations

from .conversation_grouper import conversation_id_for_turns
from .story_chain_builder import (
    StoryChain,
    build_story_chain,
    build_story_summary,
    try_extend_chain_for_payoff,
)
from .story_fragments import build_story_fragments, fragments_to_dicts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _empty_plan(
    conversation_id: str,
    min_seconds: float,
    max_seconds: float,
) -> dict:
    return {
        "conversation_id": conversation_id,
        "story_arc_shape": "incomplete_story_chain",
        "story_fragments": [],
        "story_chain": {},
        "story_summary": {},
        "story_window_plan": {
            "start": 0.0,
            "end": 0.0,
            "duration": 0.0,
            "min_seconds": float(min_seconds),
            "max_seconds": float(max_seconds),
            "turn_count": 0,
            "speaker_count": 0,
        },
        "story_window_segments": [],
        "clarity_score": 0.0,
        "duration_penalty": 1.0,
        "merge_reason": "story_chain_assembly",
        "story_window_assembly_used": True,
        "is_complete": False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_story_plan(
    block: dict,
    *,
    min_seconds: float = 25.0,  # PHASE 4: Reduced from 35.0 to allow quality short stories
    max_seconds: float = 60.0,
) -> dict:
    """Build a semantic StoryPlan from a single ConversationBlock.

    Steps
    -----
    1. Extract dialogue turns from block["turns"].
    2. Build StoryFragments (semantic segmentation, not temporal windows).
    3. Build StoryChain from the fragments.
    4. Build StorySummary and attach it to the chain.
    5. If the chain is missing its payoff, call try_extend_chain_for_payoff
       with an empty all_blocks list (no cross-block extension at this stage).
    6. Derive story_window_segments from ACTUAL fragment boundaries.
    7. Compute clarity_score and duration_penalty.
    8. Return the assembled plan dict.

    Parameters
    ----------
    block:
        ConversationBlock dict with at least a "turns" key.
    min_seconds:
        Minimum desired story duration.  Used for duration_penalty calculation.
    max_seconds:
        Maximum desired story duration.  Used for clarity_score bonus.

    Returns
    -------
    dict
        Keys: conversation_id, story_arc_shape, story_fragments, story_chain,
        story_summary, story_window_plan, story_window_segments, clarity_score,
        duration_penalty, merge_reason, story_window_assembly_used, is_complete.
    """
    block = block or {}
    turns = list(block.get("turns", None) or [])

    # Determine conversation_id
    raw_conv_id = str(block.get("conversation_id", "") or "")
    conversation_id = raw_conv_id or conversation_id_for_turns(
        turns, source_id=raw_conv_id or "block"
    )

    if not turns:
        return _empty_plan(conversation_id, min_seconds, max_seconds)

    # 1. Build StoryFragments (semantic)
    fragments = build_story_fragments(turns)

    # 2. Build StoryChain
    chain: StoryChain = build_story_chain(fragments, conversation_id=conversation_id)

    # 3. Build StorySummary
    source_text = " ".join(
        str(item.get("text", "") or item.get("caption_text", "") or "")
        for item in turns
    )
    story_summary = build_story_summary(
        fragments,
        conversation_id=conversation_id,
        source_text=source_text,
        language=str(block.get("language", "auto") or "auto"),
    )
    chain.summary = story_summary

    # 4. Attempt payoff extension (no cross-block candidates at this stage)
    if not chain.is_complete:
        chain = try_extend_chain_for_payoff(chain, [], max_extension_seconds=120.0)

    # 5. Timing from actual turn boundaries
    start = _as_float(turns[0].get("start", 0.0))
    end = _as_float(turns[-1].get("end", start))
    duration = max(0.0, end - start)

    # 6. story_window_segments from ACTUAL fragment boundaries (not percentages)
    story_window_segments = [
        {
            "role": str(getattr(frag, "role", "context") or "context"),
            "start": round(_as_float(getattr(frag, "start", 0.0)), 3),
            "end": round(_as_float(getattr(frag, "end", 0.0)), 3),
        }
        for frag in chain.fragments
    ]

    # 7a. Clarity score: 0.25 per filled arc element + four small bonuses
    arc_elements_filled = sum(
        1 for part in (chain.hook, chain.setup, chain.escalation, chain.payoff) if part
    )
    speaker_count = len(
        {
            str(item.get("speaker", "") or "")
            for item in turns
            if str(item.get("speaker", "") or "").strip()
        }
    )
    clarity_score = min(
        1.0,
        round(
            0.25 * arc_elements_filled
            + (0.05 if len(turns) >= 2 else 0.0)
            + (0.05 if duration >= min_seconds else 0.0)
            + (0.05 if duration <= max_seconds else 0.0)
            + (0.05 if speaker_count >= 2 else 0.0),
            4,
        ),
    )

    # 7b. Duration penalty: positive when duration is shorter than min_seconds
    duration_penalty = round(
        max(0.0, (min_seconds - duration) / max(1.0, min_seconds)), 4
    )

    return {
        "conversation_id": conversation_id,
        "story_arc_shape": chain.story_arc_shape,
        "story_fragments": fragments_to_dicts(chain.fragments),
        "story_chain": chain.to_dict(),
        "story_summary": story_summary.to_dict(),
        "story_window_plan": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "min_seconds": float(min_seconds),
            "max_seconds": float(max_seconds),
            "turn_count": len(turns),
            "speaker_count": speaker_count,
        },
        "story_window_segments": story_window_segments,
        "clarity_score": clarity_score,
        "duration_penalty": duration_penalty,
        "merge_reason": "story_chain_assembly",
        "story_window_assembly_used": True,
        "is_complete": chain.is_complete,
    }
