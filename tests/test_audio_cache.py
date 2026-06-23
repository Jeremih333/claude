from __future__ import annotations

import os
import shutil
import tempfile
import wave
import warnings
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.highlight import Pipeline


def _write_silent_wav(path: str, duration_seconds: float = 1.5, sample_rate: int = 16000) -> None:
    frames = b"\x00\x00" * int(duration_seconds * sample_rate)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


class AudioCacheTests(unittest.TestCase):
    def test_episode_audio_and_summary_are_cached(self):
        pipeline = Pipeline({})

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_video = os.path.join(tmpdir, "fake_episode_for_cache.mp4")
            Path(fake_video).touch()
            source_wav = os.path.join(tmpdir, "source.wav")
            _write_silent_wav(source_wav)

            def _fake_extract_audio(video_path: str, out_wav: str) -> str:
                shutil.copyfile(source_wav, out_wav)
                return out_wav

            with patch("pipeline.highlight.extract_audio_to_wav", side_effect=_fake_extract_audio) as extract_mock:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", ResourceWarning)
                    summary_first = pipeline._extract_audio_summary(fake_video, 0.0, 1.0)
                    summary_second = pipeline._extract_audio_summary(fake_video, 0.0, 1.0)

        self.assertIsInstance(summary_first, dict)
        self.assertEqual(summary_first["speech_density"], summary_second["speech_density"])
        self.assertEqual(extract_mock.call_count, 1)
        self.assertGreaterEqual(pipeline._audio_cache_stats["audio_summary_cache_hits"], 1)
        self.assertGreaterEqual(pipeline._audio_cache_stats["episode_audio_cache_misses"], 1)


if __name__ == "__main__":
    unittest.main()
