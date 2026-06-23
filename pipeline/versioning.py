from __future__ import annotations

import hashlib
import json
from pathlib import Path


PIPELINE_VERSION = "0.8.5-dev"
_CONFIG_HASH_EXCLUDE_KEYS = {"output_root", "ui_language"}


def config_hash(cfg: dict | None) -> str:
    data = dict(cfg or {})
    for key in _CONFIG_HASH_EXCLUDE_KEYS:
        data.pop(key, None)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:8]


def get_git_commit(start_path: str | Path | None = None) -> str | None:
    start = Path(start_path or Path(__file__).resolve()).resolve()
    for candidate in [start] + list(start.parents):
        git_dir = candidate / ".git"
        if not git_dir.exists():
            continue
        head_path = git_dir / "HEAD"
        if not head_path.exists():
            continue
        try:
            head = head_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return None
        if head.startswith("ref: "):
            ref_path = git_dir / head.split(" ", 1)[1]
            try:
                if ref_path.exists():
                    return ref_path.read_text(encoding="utf-8", errors="ignore").strip()[:12] or None
            except Exception:
                return None
            return None
        return head[:12] or None
    return None


def build_pipeline_identity(cfg: dict | None, start_path: str | Path | None = None) -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "config_hash": config_hash(cfg),
        "git_commit": get_git_commit(start_path),
    }
