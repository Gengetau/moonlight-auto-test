from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

from workspace import workspace_root


SCRIPTS = {
    "page": "edge_popup_compare.py",
    "pdf": "edge_popup_pdf_compare.py",
    "download": "edge_popup_download_compare.py",
    "download-matrix": "edge_popup_download_matrix.py",
}


def verify_cdp(cdp: str) -> None:
    with urlopen(cdp.rstrip("/") + "/json/version", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not str(payload.get("Browser") or "").startswith("Edg/"):
        raise RuntimeError(f"Expected Microsoft Edge at {cdp}, got {payload.get('Browser')!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PATLICS Edge migration comparisons.")
    parser.add_argument("mode", choices=sorted(SCRIPTS))
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    args, forwarded = parser.parse_known_args()

    verify_cdp(args.cdp)
    script = Path(__file__).with_name(SCRIPTS[args.mode])
    command = [sys.executable, str(script), "--cdp", args.cdp, *forwarded]
    return subprocess.run(command, cwd=workspace_root(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
