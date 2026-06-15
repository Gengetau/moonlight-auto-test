from __future__ import annotations

from pathlib import Path


def workspace_root() -> Path:
    candidates = [Path.cwd().resolve(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "requirements.txt").is_file() and (candidate / "src").is_dir():
            return candidate
    raise RuntimeError(
        "Workspace root not found. Run the skill scripts from the moonlight-auto-test repository."
    )
