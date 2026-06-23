from __future__ import annotations

import unittest

from pipeline.montage import (
    build_candidate_manifest,
    build_montage_debug_snapshot,
    build_story_manifest,
    build_story_window_plan,
    build_story_fragments,
    build_story_chain,
    build_story_summary_from_turns,
    build_story_hashtags,
    conversation_id_for_turns,
    extract_dialogue_turns,
    extract_silence_spans,
    group_conversations,
    pause_timeline_stats,
    rank_story_candidates,
    select_publishable_candidates,
    summarize_reframe_debug,
)


class MontageModuleTests(unittest.TestCase):
    def test_dialogue_parser_extracts_turns_and_silence(self):
        turns = extract_dialogue_turns(
            [
                {"start": 0.0, "end": 1.0, "text": "Привет", "speaker": "A"},
                {"start": 2.2, "end": 3.0, "text": "Пока", "speaker": "B"},
            ]
        )
        spans = extract_silence_spans(turns, total_duration=4.0)

        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["speaker"], "A")
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0]["duration"], 1.2, places=1)

    def test_conversation_grouping_builds_traceable_id(self):
        turns = [
            {"start": 0.0, "end": 1.0, "speaker": "A"},
            {"start": 1.2, "end": 2.0, "speaker": "B"},
            {"start": 2.3, "end": 3.0, "speaker": "A"},
        ]
        blocks = group_conversations(turns, max_gap_seconds=0.5, source_id="episode_1")

        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0]["conversation_id"].startswith("conv_"))
        self.assertEqual(conversation_id_for_turns(turns[:2], "episode_1").startswith("conv_"), True)

    def test_story_builder_returns_window_plan(self):
        plan = build_story_window_plan(
            {
                "turns": [
                    {"start": 0.0, "end": 1.0, "speaker": "A"},
                    {"start": 1.4, "end": 2.4, "speaker": "B"},
                    {"start": 2.7, "end": 3.5, "speaker": "A"},
                ]
            },
            min_seconds=35.0,
            max_seconds=60.0,
        )

        self.assertIn("story_window_plan", plan)
        self.assertGreaterEqual(plan["clarity_score"], 0.0)
        self.assertIn(plan["story_arc_shape"], {"hook_setup", "hook_setup_escalation", "hook_setup_escalation_payoff"})

    def test_candidate_selector_prefers_longer_story_candidates(self):
        ranked = rank_story_candidates(
            [
                {"duration": 24.0, "story_completion_score": 0.8, "story_coherence_score": 0.8, "clarity_score": 0.7, "hook_score": 0.7, "score": 0.4},
                {"duration": 40.0, "story_completion_score": 0.6, "story_coherence_score": 0.6, "clarity_score": 0.5, "hook_score": 0.5, "score": 0.3},
            ]
        )
        selected = select_publishable_candidates(ranked, max_outputs=5, min_duration=35.0)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["duration"], 40.0)

    def test_debug_and_manifest_helpers_are_inspectable(self):
        manifest = build_candidate_manifest(
            {"candidate_id": "cand_1", "pipeline_version": "0.8.4"},
            {"preview": "preview.mp4"},
        )
        story_manifest = build_story_manifest({"candidate_id": "cand_1", "story_summary": {"hook": "A", "setup": "B"}})
        debug = build_montage_debug_snapshot({"candidate_id": "cand_1", "conversation_id": "conv_a"})
        reframe = summarize_reframe_debug({"speaker_switches": 2, "speaker_confidence_score": 0.8, "visual_conversation_score": 0.7})
        pause_stats = pause_timeline_stats([{"decision": "cut", "duration": 1.5, "silence_type": "dead_air"}])

        self.assertEqual(manifest["paths"]["preview"], "preview.mp4")
        self.assertEqual(story_manifest["manifest_kind"], "story_snapshot")
        self.assertEqual(debug["conversation_id"], "conv_a")
        self.assertEqual(reframe["speaker_switches"], 2)
        self.assertEqual(pause_stats["pause_cut_count"], 1)

    def test_story_fragment_chain_and_summary_are_built(self):
        turns = extract_dialogue_turns(
            [
                {"start": 0.0, "end": 1.0, "text": "Why are you here?", "speaker": "A"},
                {"start": 1.1, "end": 2.1, "text": "Because you lied to me.", "speaker": "B"},
                {"start": 2.2, "end": 3.1, "text": "This is getting worse.", "speaker": "A"},
                {"start": 3.2, "end": 4.2, "text": "Fine, here's the truth.", "speaker": "B"},
            ]
        )
        fragments = build_story_fragments(turns)
        chain = build_story_chain(fragments, conversation_id="conv_story")
        summary = build_story_summary_from_turns(turns, conversation_id="conv_story")
        tags = build_story_hashtags(summary.to_dict(), max_hashtags=3, language="en")

        self.assertGreaterEqual(len(fragments), 1)
        self.assertIn(fragments[0].role, {"hook", "hook_setup"})
        self.assertEqual(chain.conversation_id, "conv_story")
        self.assertTrue(summary.hook)
        self.assertTrue(summary.setup)
        self.assertTrue(summary.escalation)
        self.assertTrue(summary.payoff)
        self.assertTrue(tags[0].startswith("#"))


if __name__ == "__main__":
    unittest.main()
