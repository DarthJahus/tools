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

    # Custom encoding params (forces re-encode even at speed=1.0):
    python merge_frigate.py ... --encode-params "-c:v libx264 -b:v 1000k -c:a aac"
"""

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


WARN_DURATION_S = 3600  # alert threshold for source duration


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
                        "Speed=1 with no --encode-params uses stream copy (lossless).")
    p.add_argument("--encode-params", default=None, metavar="PARAMS",
                   help="Arbitrary ffmpeg output params as a quoted string, e.g. "
                        '"-c:v libx264 -b:v 1000k -c:a aac". '
                        "Forces re-encode. Replaces default codec options entirely. "
                        "Speed filters (setpts/atempo) are still applied when --speed != 1.")
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
    rel   = segment.relative_to(root)
    parts = rel.parts           # ('2026-04-17', '19', 'imou', '28.43.mp4')
    date_str = parts[0]
    hour_str = parts[1]
    stem     = Path(parts[3]).stem   # '28.43'
    mm, ss   = stem.split(".")
    return datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=int(hour_str), minute=int(mm), second=int(ss)
    )


def collect_segments(root: Path, camera: str,
                     start: datetime, end: datetime) -> list[Path]:
    segments: list[Path] = []
    bucket     = start.replace(minute=0, second=0, microsecond=0)
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
    list_path = os.path.join(tmpdir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for seg in segments:
            safe = str(seg.resolve()).replace("\\", "/")
            fh.write(f"file '{safe}'\n")
    return list_path


def build_atempo_chain(speed: float) -> str:
    """Chain atempo filters; each instance limited to [0.5, 2.0]."""
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


def fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def confirm(prompt: str) -> bool:
    """Prompt user; default is No."""
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# ffmpeg runner
# ---------------------------------------------------------------------------

def build_command(ffmpeg: str, concat_list: str,
                  speed: float, encode_params: str | None,
                  output: str) -> list[str]:

    base = [ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list]

    need_reencode = (encode_params is not None) or (speed != 1.0)

    if not need_reencode:
        # Stream copy — fastest, lossless
        return base + ["-c", "copy", output]

    # Build filter options only when speed differs from 1.0
    filter_opts: list[str] = []
    if speed != 1.0:
        vf = f"setpts={1.0 / speed:.6f}*PTS"
        af = build_atempo_chain(speed)
        filter_opts = ["-vf", vf, "-af", af]

    # Codec options: user-supplied or sensible defaults
    if encode_params is not None:
        codec_opts = shlex.split(encode_params)
    else:
        codec_opts = ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                      "-c:a", "aac"]

    return base + filter_opts + codec_opts + [output]


def run_merge(ffmpeg: str, concat_list: str,
              speed: float, encode_params: str | None,
              output: str) -> None:

    cmd = build_command(ffmpeg, concat_list, speed, encode_params, output)

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
    root  = Path(args.root)
    start = parse_dt(args.start)
    end   = parse_dt(args.end)

    if not root.is_dir():
        print(f"Error: root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    if start >= end:
        print("Error: --start must be strictly before --end.", file=sys.stderr)
        sys.exit(1)

    print(f"Camera       : {args.camera}")
    print(f"Range        : {start}  →  {end}")
    print(f"Speed        : {args.speed}x")
    print(f"Encode params: {args.encode_params or '(default / stream copy)'}")
    print(f"Output       : {args.output}")
    print()

    segments = collect_segments(root, args.camera, start, end)

    if not segments:
        print("No segments found in the specified range.", file=sys.stderr)
        sys.exit(1)

    raw_s = len(segments) * 12   # each Frigate clip ≈ 12 s
    out_s = int(raw_s / args.speed)

    print(f"Segments found   : {len(segments)}")
    print(f"Source duration  : ~{fmt_duration(raw_s)}")
    print(f"Output duration  : ~{fmt_duration(out_s)}  (at {args.speed}x)")
    print(f"First : {segments[0].relative_to(root)}")
    print(f"Last  : {segments[-1].relative_to(root)}")

    # --- Duration warning --------------------------------------------------
    if out_s > WARN_DURATION_S:
        print(f"\n⚠  Source duration exceeds {fmt_duration(WARN_DURATION_S)} "
              f"({fmt_duration(out_s)} total).")
        if not confirm("Continue anyway? [y/N] "):
            print("Aborted.")
            sys.exit(0)

    with tempfile.TemporaryDirectory() as tmpdir:
        concat_list = write_concat_list(segments, tmpdir)
        run_merge(args.ffmpeg, concat_list, args.speed, args.encode_params, args.output)

    print(f"\nDone → {args.output}")


if __name__ == "__main__":
    main()
