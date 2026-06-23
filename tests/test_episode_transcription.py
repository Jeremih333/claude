"""
Test suite for episode-level transcription (Sprint 1.6)

Tests the new _transcribe_full_episode() method and its integration
with pick_candidates() in story-centric mode.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.highlight import Pipeline


class TestEpisodeTranscription:
    """Test episode-level transcription functionality."""
    
    def test_transcribe_full_episode_cache_miss(self):
        """Test that _transcribe_full_episode extracts and transcribes audio."""
        cfg = {"use_story_centric_pipeline": True, "subtitle_language": "ru"}
        pipeline = Pipeline(cfg)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock video file
            video_path = Path(tmpdir) / "test_episode.mp4"
            video_path.write_text("mock video")
            
            # Mock the extraction and transcription
            with patch('pipeline.highlight.extract_audio_to_wav') as mock_extract:
                with patch('pipeline.highlight.transcribe_segment') as mock_transcribe:
                    mock_transcribe.return_value = {
                        'segments': [
                            {'start': 0.0, 'end': 2.0, 'text': 'Test segment'},
                        ],
                        'line_count': 1,
                        'confidence': 0.85,
                    }
                    
                    result = pipeline._transcribe_full_episode(str(video_path))
                    
                    # Verify extraction was called
                    assert mock_extract.called
                    
                    # Verify transcription was called
                    assert mock_transcribe.called
                    
                    # Verify result structure
                    assert 'segments' in result
                    assert len(result['segments']) == 1
                    assert result['line_count'] == 1
                    assert result['confidence'] == 0.85
    
    def test_transcribe_full_episode_cache_hit(self):
        """Test that _transcribe_full_episode uses disk cache on second call."""
        cfg = {"use_story_centric_pipeline": True}
        pipeline = Pipeline(cfg)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test_episode.mp4"
            video_path.write_text("mock video")
            
            # Create mock cache file
            cache_path = video_path.with_suffix('.subtitle_cache.json')
            cache_data = {
                'segments': [{'start': 0.0, 'end': 1.0, 'text': 'Cached'}],
                'line_count': 1,
                'confidence': 0.9,
            }
            cache_path.write_text(json.dumps(cache_data), encoding='utf-8')
            
            with patch('pipeline.highlight.extract_audio_to_wav') as mock_extract:
                with patch('pipeline.highlight.transcribe_segment') as mock_transcribe:
                    result = pipeline._transcribe_full_episode(str(video_path))
                    
                    # Verify extraction was NOT called (cache hit)
                    assert not mock_extract.called
                    assert not mock_transcribe.called
                    
                    # Verify cached data was returned
                    assert result['segments'][0]['text'] == 'Cached'
                    assert result['line_count'] == 1
    
    def test_pick_candidates_calls_transcribe_in_story_mode(self):
        """Test that pick_candidates() calls _transcribe_full_episode when flag is enabled."""
        cfg = {"use_story_centric_pipeline": True}
        pipeline = Pipeline(cfg)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test_episode.mp4"
            video_path.write_text("mock video")
            
            # Mock all the methods pick_candidates needs
            with patch.object(pipeline, '_transcribe_full_episode') as mock_transcribe:
                with patch.object(pipeline, '_candidate_windows') as mock_windows:
                    with patch('pipeline.highlight.probe_video') as mock_probe:
                        mock_transcribe.return_value = {
                            'segments': [{'start': 0.0, 'end': 2.0, 'text': 'Test'}],
                            'line_count': 1,
                        }
                        mock_windows.return_value = []  # No windows to avoid full pipeline
                        mock_probe.return_value = (True, 60.0)
                        
                        pipeline.pick_candidates(str(video_path))
                        
                        # Verify _transcribe_full_episode was called
                        assert mock_transcribe.called
                        assert mock_transcribe.call_count == 1
    
    def test_pick_candidates_skips_transcribe_in_legacy_mode(self):
        """Test that pick_candidates() skips _transcribe_full_episode when flag is disabled."""
        cfg = {"use_story_centric_pipeline": False}
        pipeline = Pipeline(cfg)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test_episode.mp4"
            video_path.write_text("mock video")
            
            with patch.object(pipeline, '_transcribe_full_episode') as mock_transcribe:
                with patch.object(pipeline, '_candidate_windows') as mock_windows:
                    with patch('pipeline.highlight.probe_video') as mock_probe:
                        mock_windows.return_value = []
                        mock_probe.return_value = (True, 60.0)
                        
                        pipeline.pick_candidates(str(video_path))
                        
                        # Verify _transcribe_full_episode was NOT called
                        assert not mock_transcribe.called
    
    def test_transcribe_full_episode_fallback_on_failure(self):
        """Test that _transcribe_full_episode returns empty dict on failure."""
        cfg = {"use_story_centric_pipeline": True}
        pipeline = Pipeline(cfg)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test_episode.mp4"
            video_path.write_text("mock video")
            
            # Mock extraction to fail
            with patch('pipeline.highlight.extract_audio_to_wav') as mock_extract:
                mock_extract.side_effect = Exception("Extraction failed")
                
                result = pipeline._transcribe_full_episode(str(video_path))
                
                # Verify fallback to empty structure
                assert result == {'segments': [], 'line_count': 0, 'confidence': 0.0}
    
    def test_transcribe_full_episode_creates_cache_directory(self):
        """Test that cache directory is created if it doesn't exist."""
        cfg = {"use_story_centric_pipeline": True}
        pipeline = Pipeline(cfg)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "subdir" / "test_episode.mp4"
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_text("mock video")
            
            with patch('pipeline.highlight.extract_audio_to_wav'):
                with patch('pipeline.highlight.transcribe_segment') as mock_transcribe:
                    mock_transcribe.return_value = {
                        'segments': [{'start': 0.0, 'end': 1.0, 'text': 'Test'}],
                        'line_count': 1,
                        'confidence': 0.8,
                    }
                    
                    result = pipeline._transcribe_full_episode(str(video_path))
                    
                    # Verify cache file was created
                    cache_path = video_path.with_suffix('.subtitle_cache.json')
                    assert cache_path.exists()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
