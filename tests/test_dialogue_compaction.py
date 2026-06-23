from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from pipeline.highlight import Pipeline


class DialogueCompactionTests(unittest.TestCase):
    def test_single_dialogue_block_still_triggers_compaction_when_silence_was_removed(self):
        pipeline = Pipeline({})
        subtitle_info = {
            "segments": [
                {"start": 7.60, "end": 8.10, "text": "Первый"},
                {"start": 8.20, "end": 8.80, "text": "ответ"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            trimmed = os.path.join(tmpdir, "trimmed.mp4")
            compacted = os.path.join(tmpdir, "compacted.mp4")

            with open(trimmed, "wb") as handle:
                handle.write(b"placeholder")

            with (
                patch("pipeline.highlight.probe_video", return_value=(True, 12.0)),
                patch("pipeline.highlight._concat_video_segments", return_value=(True, 4.0)) as concat_mock,
                patch("pipeline.highlight._sanitize_compacted_video", return_value=(True, 4.0)),
                patch("pipeline.highlight._validate_compacted_video_integrity", return_value=(True, 4.0)),
            ):
                output_path, changed = pipeline._maybe_compact_dialogue_after_subtitles(
                    trimmed,
                    subtitle_info,
                    tmpdir,
                    1,
                )

        self.assertFalse(changed)
        self.assertEqual(output_path, trimmed)
        self.assertFalse(concat_mock.called)


if __name__ == "__main__":
    unittest.main()
