import json
import tempfile
from pathlib import Path
import unittest

from pipeline.config import normalize_config
from pipeline.feedback_store import append_feedback_event, rank_assisted_candidates


class TestAssistedRanking(unittest.TestCase):
    def test_rank_assisted_candidates_orders_by_quality(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            meta_a = root / "short_a.json"
            meta_b = root / "short_b.json"
            meta_a.write_text(
                json.dumps(
                    {
                        "generated_title": "A",
                        "recommendation_readiness_score": 0.58,
                        "watchability_score": 0.60,
                        "packaging_quality_score": 0.59,
                        "first_second_hook_score": 0.57,
                        "story_interest_score": 0.55,
                        "visible_stakes_score": 0.52,
                        "first_frame_clarity_score": 0.53,
                    }
                ),
                encoding="utf-8",
            )
            meta_b.write_text(
                json.dumps(
                    {
                        "generated_title": "B",
                        "recommendation_readiness_score": 0.72,
                        "watchability_score": 0.68,
                        "packaging_quality_score": 0.64,
                        "first_second_hook_score": 0.71,
                        "story_interest_score": 0.69,
                        "visible_stakes_score": 0.63,
                        "first_frame_clarity_score": 0.65,
                    }
                ),
                encoding="utf-8",
            )

            ranked = rank_assisted_candidates(
                [
                    {"video": str(root / "a.mp4"), "metadata": str(meta_a)},
                    {"video": str(root / "b.mp4"), "metadata": str(meta_b)},
                ]
            )

            self.assertEqual(ranked[0]["generated_title"], "B")
            self.assertEqual(ranked[0]["rank"], 1)
            self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_append_feedback_event_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = append_feedback_event(
                {"mode": "assisted_ranking", "rating": "excellent", "source_file": "episode.mp4"},
                base_dir=tmpdir,
            )
            self.assertTrue(path.exists())
            payload = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(payload), 1)
            record = json.loads(payload[0])
            self.assertEqual(record["rating"], "excellent")
            self.assertEqual(record["source_file"], "episode.mp4")
            self.assertIn("timestamp_utc", record)

    def test_viral_soft_style_normalizes(self):
        cfg = normalize_config({"title_style": "viral_soft"})
        self.assertEqual(cfg["title_style"], "retention_soft")


if __name__ == "__main__":
    unittest.main()
