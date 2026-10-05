#!/usr/bin/env python3
"""Optional 15 fps visual microscope for the opening hook."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from frames import create_contact_sheets, extract  # noqa: E402


HOOK_DURATION_SECONDS = 15.0
HOOK_FPS = 15.0
HOOK_SHEET_COLS = 5
HOOK_SHEET_ROWS = 9
HOOK_TILE_WIDTH = 200


def analyse_hook(video_path: str, out_dir: Path, full_video_duration: float = 0.0) -> dict:
    """Extract and pack up to the first 15 seconds for sub-second inspection."""
    hook_duration = (
        min(HOOK_DURATION_SECONDS, full_video_duration)
        if full_video_duration > 0 else HOOK_DURATION_SECONDS
    )
    frames = extract(
        video_path,
        out_dir / "hook_frames",
        fps=HOOK_FPS,
        resolution=HOOK_TILE_WIDTH,
        max_frames=max(1, int(round(hook_duration * HOOK_FPS))),
        start_seconds=0.0,
        end_seconds=hook_duration,
    )
    sheets = create_contact_sheets(
        frames,
        out_dir / "hook_sheets",
        cols=HOOK_SHEET_COLS,
        rows=HOOK_SHEET_ROWS,
        tile_width=HOOK_TILE_WIDTH,
        prefix="hook_sheet",
    )
    return {
        "frames": frames,
        "sheets": sheets,
        "duration_seconds": hook_duration,
        "fps": HOOK_FPS,
        "sheet_cols": HOOK_SHEET_COLS,
        "sheet_rows": HOOK_SHEET_ROWS,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: hook.py <video-path> <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(analyse_hook(sys.argv[1], Path(sys.argv[2])), indent=2))
