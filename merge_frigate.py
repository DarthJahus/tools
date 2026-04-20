#!/usr/bin/env python3
"""
Frigate recording merger
Merge segmented MP4 clips into a single monolithic file.

Structure assumed:
    <root>/<YYYY-MM-DD>/<HH>/<camera>/<MM.SS>.mp4

Usage:
    python merge_frigate.py --root "D:\\recordings" --camera imou \
        --start "2026-04-17 19:28" --end "2026-04-17 21:30" \
        --speed 4 --output merged.mp4
"""

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Merge Frigate MP4 segments into one monolithic recording."
    )
    p.add_argument("--root",   required=True,
                   help="Root recordings directory (e.g. D:\\recordings)")
    p.add_argument("--camera", required=True,
                   help="Camera subfolder name (e.g. imou, xiaomi)")
    p.add_argument("--start",  required=True,
                   help="Start datetime: 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD HH:MM:SS'")
    p.add_argument("--end",    required=True,
                   help="End datetime:   'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD HH:MM:SS'")
    p.add_argument("--speed",  type=float, default=1.0,
                   help="Playback speed multiplier (default: 1.0 = real-time). "
                        "Speed=1 uses stream copy (fast, lossless). "
                        "Any other value re-encodes.")
    p.add_argument("--output", default="merged.mp4",
                   help="Output file path (default: merged.mp4)")
    p.add_argument("--ffmpeg", default="ffmpeg",
                   help="Path to ffmpeg binary (default: ffmpeg from PATH)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_dt(s: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"Cannot parse datetime: {s!r}. Use 'YYYY-MM-DD HH:MM[:SS]'")


def segment_datetime(root: Path, segment: Path) -> datetime:
    """
    Derive the wall-clock datetime of a segment from its path.
    Expects: root / YYYY-MM-DD / HH / camera / MM.SS.mp4
    """
    rel = segment.relative_to(root)
    parts = rel.parts                   # ('2026-04-17', '19', 'imou', '28.43.mp4')
    date_str  = parts[0]
    hour_str  = parts[1]
    stem      = Path(parts[3]).stem     # '28.43'
    mm, ss    = stem.split(".")
    return datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=int(hour_str), minute=int(mm), second=int(ss)
    )


def collect_segments(root: Path, camera: str,
                     start: datetime, end: datetime) -> list[Path]:
    """Walk hour-directories that overlap [start, end] and collect matching files."""
    segments: list[Path] = []

    # Iterate over each whole-hour bucket that could overlap the range
    bucket = start.replace(minute=0, second=0, microsecond=0)
    end_bucket = end.replace(minute=0, second=0, microsecond=0)

    while bucket <= end_bucket:
        cam_dir = (
            root
            / bucket.strftime("%Y-%m-%d")
            / f"{bucket.hour:02d}"
            / camera
        )
        if cam_dir.is_dir():
            for f in sorted(cam_dir.iterdir()):
                if f.suffix.lower() != ".mp4":
                    continue
                try:
                    fdt = segment_datetime(root, f)
                except Exception:
                    continue
                if start <= fdt <= end:
                    segments.append(f)

        bucket += timedelta(hours=1)

    return sorted(segments)


def write_concat_list(segments: list[Path], tmpdir: str) -> str:
    """Write an ffmpeg concat demuxer list and return its path."""
    list_path = os.path.join(tmpdir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for seg in segments:
            # ffmpeg needs forward slashes (also works on Windows)
            safe = str(seg.resolve()).replace("\\", "/")
            fh.write(f"file '{safe}'\n")
    return list_path


def build_atempo_chain(speed: float) -> str:
    """
    ffmpeg's atempo filter only accepts [0.5, 2.0].
    Chain multiple atempo filters for values outside that range.
    """
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


# ---------------------------------------------------------------------------
# ffmpeg runner
# ---------------------------------------------------------------------------

def run_merge(ffmpeg: str, concat_list: str, speed: float, output: str) -> None:
    if speed == 1.0:
        # Stream copy — instant, lossless
        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            output,
        ]
    else:
        vf = f"setpts={1.0 / speed:.6f}*PTS"
        af = build_atempo_chain(speed)
        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac",
            output,
        ]

    print("\n[ffmpeg command]")
    print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
    print()

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("ffmpeg exited with an error.", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    root   = Path(args.root)
    start  = parse_dt(args.start)
    end    = parse_dt(args.end)

    if not root.is_dir():
        print(f"Error: root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    if start >= end:
        print("Error: --start must be strictly before --end.", file=sys.stderr)
        sys.exit(1)

    print(f"Camera  : {args.camera}")
    print(f"Range   : {start}  →  {end}")
    print(f"Speed   : {args.speed}x")
    print(f"Output  : {args.output}")
    print()

    segments = collect_segments(root, args.camera, start, end)

    if not segments:
        print("No segments found in the specified range.", file=sys.stderr)
        sys.exit(1)

    total_duration_s = len(segments) * 12   # each clip ≈ 12 s
    print(f"Segments found : {len(segments)}")
    print(f"Raw duration   : ~{total_duration_s // 60} min {total_duration_s % 60} s")
    print(f"Output duration: ~{int(total_duration_s / args.speed) // 60} min "
          f"{int(total_duration_s / args.speed) % 60} s  (at {args.speed}x)")

    # Preview first / last
    print(f"\nFirst : {segments[0].relative_to(root)}")
    print(f"Last  : {segments[-1].relative_to(root)}")

    with tempfile.TemporaryDirectory() as tmpdir:
        concat_list = write_concat_list(segments, tmpdir)
        run_merge(args.ffmpeg, concat_list, args.speed, args.output)

    print(f"\nDone → {args.output}")


if __name__ == "__main__":
    main()
