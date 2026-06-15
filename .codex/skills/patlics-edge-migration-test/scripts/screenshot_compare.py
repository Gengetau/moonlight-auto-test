from pathlib import Path
from typing import Any, Dict

from PIL import Image, ImageChops


def compare_visual_screenshot(
    legacy_path: str,
    new_path: str,
    diff_path: str,
    *,
    threshold_percent: float = 0.1,
    ignore_top_px: int = 0,
) -> Dict[str, Any]:
    legacy_file = Path(legacy_path)
    new_file = Path(new_path)
    output_file = Path(diff_path)

    if not legacy_file.exists():
        return {
            "status": "BLOCKED",
            "reason": f"Legacy screenshot not found: {legacy_file}",
            "diff_percent": None,
            "diff_screenshot": None,
        }
    if not new_file.exists():
        return {
            "status": "BLOCKED",
            "reason": f"New screenshot not found: {new_file}",
            "diff_percent": None,
            "diff_screenshot": None,
        }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(legacy_file) as legacy_img, Image.open(new_file) as new_img:
        legacy = legacy_img.convert("RGBA")
        new = new_img.convert("RGBA")
        if ignore_top_px > 0:
            crop_y = min(ignore_top_px, legacy.height, new.height)
            legacy = legacy.crop((0, crop_y, legacy.width, legacy.height))
            new = new.crop((0, crop_y, new.width, new.height))

        width = max(legacy.width, new.width)
        height = max(legacy.height, new.height)
        normalized_legacy = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        normalized_new = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        normalized_legacy.paste(legacy, (0, 0))
        normalized_new.paste(new, (0, 0))

        diff = ImageChops.difference(normalized_legacy, normalized_new)
        alpha = diff.convert("L")
        changed_pixels = sum(alpha.histogram()[1:])
        total_pixels = width * height
        diff_percent = (changed_pixels / total_pixels * 100) if total_pixels else 0.0

        highlight = Image.new("RGBA", (width, height), (255, 0, 0, 150))
        result = normalized_new.copy()
        result.paste(highlight, (0, 0), alpha)
        result.convert("RGB").save(output_file)

    return {
        "status": "PASS" if diff_percent <= threshold_percent else "DIFF",
        "diff_percent": round(diff_percent, 4),
        "threshold_percent": threshold_percent,
        "ignored_top_px": ignore_top_px,
        "diff_screenshot": str(output_file),
        "legacy_size": [legacy.width, legacy.height],
        "new_size": [new.width, new.height],
    }
