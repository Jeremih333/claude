"""Optional remote reranking stub.

The local CPU-first pipeline does not depend on this module.
"""


def analyze_segment(video_path, start, end, hf_model=None, hf_token=None):
    if not hf_model or not hf_token:
        return None
    return None
