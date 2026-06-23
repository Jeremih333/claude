from __future__ import annotations

import unittest

from pipeline.highlight import Pipeline


class DurationPolicyTests(unittest.TestCase):
    def test_resolve_candidate_duration_policy_overrides_stale_standard_policy(self):
        pipeline = Pipeline({"story_mode": "tension"})
        candidate = {
            "start": 0.0,
            "end": 19.0,
            "duration": 19.0,
            "score_breakdown": {
                "duration": 19.0,
                "story_interest_score": 0.66,
                "story_completeness_score": 0.62,
                "watchability_score": 0.68,
                "recommendation_readiness_score": 0.65,
                "hook_strength": 0.61,
                "payoff_strength": 0.58,
            },
            "duration_policy": {
                "story_mode": "standard",
                "min_publishable_seconds": 35.0,
            },
        }
        subtitle_info = {
            "signals": {
                "interestingness_score": 0.71,
                "closure_score": 0.66,
                "story_boundary_confidence": 0.63,
                "dialogue_flow_score": 0.61,
                "subtitle_quality_score": 0.70,
            }
        }

        duration_policy = pipeline._resolve_candidate_duration_policy(candidate, subtitle_info)

        self.assertEqual(duration_policy["story_mode"], "tension")
        self.assertEqual(duration_policy["target_seconds"], 45.0)
        self.assertEqual(duration_policy["soft_max_seconds"], 60.0)
        self.assertEqual(duration_policy["hard_max_seconds"], 60.0)
        self.assertEqual(duration_policy["min_publishable_seconds"], 35.0)
        self.assertEqual(candidate["duration_policy"]["story_mode"], "tension")
        self.assertEqual(candidate["duration_policy"]["min_publishable_seconds"], 35.0)


if __name__ == "__main__":
    unittest.main()
