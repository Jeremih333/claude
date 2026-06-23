from __future__ import annotations


def remap_subtitles_after_cuts(subtitle_info: dict, removed_segments: list[tuple[float, float]], out_dir: str, idx: int, cfg: dict | None = None):
    from pipeline.subtitle import remap_subtitle_info_after_cuts

    return remap_subtitle_info_after_cuts(subtitle_info, removed_segments, out_dir, idx, cfg=cfg)

