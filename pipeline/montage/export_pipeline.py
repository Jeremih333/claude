from __future__ import annotations

from pathlib import Path


def build_candidate_manifest(meta: dict, output_paths: dict[str, str] | None = None) -> dict:
    output_paths = dict(output_paths or {})
    manifest = dict(meta or {})
    if output_paths:
        manifest["paths"] = {key: str(value) for key, value in output_paths.items()}
    manifest.setdefault("candidate_id", manifest.get("candidate_id", "candidate_unknown"))
    manifest.setdefault("created_at", manifest.get("created_at", ""))
    manifest.setdefault("pipeline_version", manifest.get("pipeline_version", ""))
    manifest.setdefault("config_hash", manifest.get("config_hash", ""))
    manifest.setdefault("git_commit", manifest.get("git_commit", ""))
    if "story_summary" in manifest and isinstance(manifest.get("story_summary"), dict):
        manifest["story_summary"] = dict(manifest["story_summary"])
    if "story_chain" in manifest and isinstance(manifest.get("story_chain"), dict):
        manifest["story_chain"] = dict(manifest["story_chain"])
    if "story_fragments" in manifest and isinstance(manifest.get("story_fragments"), list):
        manifest["story_fragments"] = list(manifest["story_fragments"])
    return manifest


def build_story_manifest(meta: dict, output_paths: dict[str, str] | None = None) -> dict:
    manifest = build_candidate_manifest(meta, output_paths=output_paths)
    manifest.setdefault("manifest_kind", "story_snapshot")
    return manifest
