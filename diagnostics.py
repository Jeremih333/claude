from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd, cwd=None, timeout=300):
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:
        return 1, "", str(exc)


def _section(title: str, body: str) -> str:
    body = body.rstrip() or "(no output)"
    return f"== {title} ==\n{body}\n"


def run_diagnostics_text(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    desktop_toolkit = root.parent / "toolkit"
    python_exe = root / "venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    parts = [
        _section("Project Root", str(root)),
        _section("Python", str(python_exe)),
        _section("ffmpeg", shutil.which("ffmpeg") or "not found"),
        _section("ffprobe", shutil.which("ffprobe") or "not found"),
    ]

    env_script = desktop_toolkit / "env_snapshot.ps1"
    audit_script = desktop_toolkit / "run_audit.ps1"
    if env_script.exists():
        rc, out, err = _run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(env_script), "-ProjectRoot", str(root)],
            cwd=str(root.parent),
            timeout=180,
        )
        parts.append(_section("Environment Snapshot", out if rc == 0 else err))
    if audit_script.exists():
        rc, out, err = _run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(audit_script), "-ProjectRoot", str(root)],
            cwd=str(root.parent),
            timeout=300,
        )
        parts.append(_section("Toolkit Audit", out if rc == 0 else err))
    else:
        rc, out, err = _run([str(python_exe), "-m", "compileall", "main.py", "gui.py", "pipeline"], cwd=str(root), timeout=120)
        parts.append(_section("Compileall", out if rc == 0 else err))
    return "\n".join(parts).strip() + "\n"


def run_diagnostics_summary(project_root: str | Path) -> dict:
    text = run_diagnostics_text(project_root)
    summary = {
        "project_root": str(Path(project_root).resolve()),
        "ffmpeg_ok": "== ffmpeg ==\nnot found" not in text,
        "ffprobe_ok": "== ffprobe ==\nnot found" not in text,
        "pipeline_import_ok": "pipeline import ok" in text,
        "gui_import_ok": "gui import ok" in text,
        "main_import_ok": "main import ok" in text,
        "toolkit_audit_present": "== Toolkit Audit ==" in text,
    }
    issues = []
    if not summary["ffmpeg_ok"]:
        issues.append("ffmpeg_not_found")
    if not summary["ffprobe_ok"]:
        issues.append("ffprobe_not_found")
    if not summary["pipeline_import_ok"]:
        issues.append("pipeline_import_failed")
    if not summary["gui_import_ok"]:
        issues.append("gui_import_failed")
    if not summary["main_import_ok"]:
        issues.append("main_import_failed")
    summary["issues"] = issues
    return summary
