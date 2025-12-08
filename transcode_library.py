#!/usr/bin/env python3
"""
transcode_library.py

Parcourt récursivement un dossier source, transcode les fichiers vidéo selon des critères
spécifiques (bitrate, codec, résolution), et maintient un fichier done.txt pour éviter
de re-transcoder les fichiers déjà traités.

Dépendances: python3, ffmpeg, ffprobe
"""

import argparse
import subprocess
import json
import sys
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple
from datetime import datetime

# Extensions vidéo supportées
VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v'}

# Profils de qualité
QUALITY_PRESETS = {
    'low': 'veryfast',
    'medium': 'faster',
    'high': 'slow',
    'very_high': 'slower'
}

# Encodeurs GPU
GPU_ENCODERS = {
    'nvidia': {
        'h264': 'h264_nvenc',
        'hevc': 'hevc_nvenc',
        'av1': 'av1_nvenc'
    },
    'amd': {
        'h264': 'h264_amf',
        'hevc': 'hevc_amf',
        'av1': 'av1_amf'
    }
}


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class MediaInfo:
    """Informations extraites d'un fichier média"""
    video_bitrate: Optional[int] = None  # kb/s
    audio_bitrate: Optional[int] = None  # kb/s
    is_vbr: bool = False
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    
    def __str__(self):
        return (f"Video: {self.video_codec or 'N/A'} {self.video_bitrate or 'N/A'}kb/s "
                f"{'VBR' if self.is_vbr else 'CBR'} {self.width or '?'}x{self.height or '?'} | "
                f"Audio: {self.audio_codec or 'N/A'} {self.audio_bitrate or 'N/A'}kb/s")


# ============================================================================
# LOGGING
# ============================================================================

class Logger:
    """Gère les logs vers fichier et console"""
    
    def __init__(self, log_file: Optional[str] = None, error_log_file: Optional[str] = None, verbose: bool = False):
        self.log_file = log_file
        self.error_log_file = error_log_file
        self.verbose = verbose
    
    def _write_file(self, filepath: Optional[str], message: str):
        if filepath:
            try:
                with open(filepath, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
            except Exception as e:
                print(f"Warning: Cannot write to {filepath}: {e}")
    
    def info(self, message: str):
        if self.verbose:
            print(f"[INFO] {message}")
        self._write_file(self.log_file, f"INFO: {message}")
    
    def error(self, message: str):
        print(f"[ERROR] {message}", file=sys.stderr)
        self._write_file(self.error_log_file, f"ERROR: {message}")
    
    def warning(self, message: str):
        print(f"[WARNING] {message}")
        self._write_file(self.log_file, f"WARNING: {message}")


# ============================================================================
# FFPROBE HELPERS
# ============================================================================

def run_ffprobe(args: list) -> Optional[str]:
    """Execute ffprobe command and return stdout"""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None


def _to_int(value: str) -> Optional[int]:
    """Convert string to int safely"""
    if value and value.strip():
        try:
            return int(float(value.strip()))
        except (ValueError, OverflowError):
            return None
    return None


def _to_float(value: str) -> Optional[float]:
    """Convert string to float safely"""
    if value:
        try:
            return float(value.strip())
        except (ValueError, OverflowError):
            return None
    return None


def detect_cbr_vbr(filepath: Path) -> bool:
    """
    Détecte si le fichier est en VBR (Variable Bitrate) ou CBR (Constant Bitrate).
    Retourne True si VBR, False si CBR.
    Méthode: compare bitrate min/max du stream. Si différence > 10%, c'est du VBR.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=bit_rate",
        "-show_entries", "packet=size",
        "-read_intervals", "%+#100",  # Lire 100 premiers packets
        "-of", "json",
        str(filepath)
    ]
    
    output = run_ffprobe(cmd)
    if not output:
        return False  # Par défaut CBR si détection impossible
    
    try:
        data = json.loads(output)
        packets = data.get('packets', [])
        if len(packets) < 10:
            return False
        
        sizes = [int(p.get('size', 0)) for p in packets if 'size' in p]
        if not sizes:
            return False
        
        min_size = min(sizes)
        max_size = max(sizes)
        avg_size = sum(sizes) / len(sizes)
        
        # Si variation > 10%, considérer comme VBR
        variation = (max_size - min_size) / avg_size if avg_size > 0 else 0
        return variation > 0.1
        
    except (json.JSONDecodeError, KeyError, ValueError):
        return False


def get_info(filepath: Path, logger: Logger) -> Optional[MediaInfo]:
    """
    Extrait toutes les informations d'un fichier média avec fallbacks robustes.
    Retourne MediaInfo ou None si échec total.
    """
    logger.info(f"Analyzing: {filepath.name}")
    
    info = MediaInfo()
    
    # ========================================================================
    # Get complete info in JSON format
    # ========================================================================
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams", "-show_format",
        "-of", "json",
        str(filepath)
    ]
    
    output = run_ffprobe(cmd)
    if not output:
        logger.error(f"ffprobe failed for {filepath}")
        return None
    
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON from ffprobe for {filepath}")
        return None
    
    streams = data.get('streams', [])
    format_data = data.get('format', {})
    
    # ========================================================================
    # VIDEO STREAM
    # ========================================================================
    video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    
    if video_stream:
        # Codec
        info.video_codec = video_stream.get('codec_name')
        
        # Resolution
        info.width = video_stream.get('width')
        info.height = video_stream.get('height')
        
        # Bitrate (multiple fallbacks)
        # 1. Stream bit_rate
        if 'bit_rate' in video_stream:
            info.video_bitrate = _to_int(video_stream['bit_rate'])
            if info.video_bitrate:
                info.video_bitrate = round(info.video_bitrate / 1000)
        
        # 2. Tags STATISTICS
        if not info.video_bitrate:
            tags = video_stream.get('tags', {})
            for key in ['BPS', 'BPS-eng', 'STATISTICS_BPS']:
                if key in tags:
                    br = _to_int(tags[key])
                    if br:
                        info.video_bitrate = round(br / 1000)
                        break
        
        # 3. Calculate from duration and nb_read_bytes
        if not info.video_bitrate:
            duration = _to_float(video_stream.get('duration', '0'))
            nb_bytes = _to_int(video_stream.get('tags', {}).get('NUMBER_OF_BYTES', '0'))
            if duration and duration > 0 and nb_bytes and nb_bytes > 0:
                info.video_bitrate = round((nb_bytes * 8) / (duration * 1000))
        
        # 4. Fallback: format bitrate - audio estimation
        if not info.video_bitrate and 'bit_rate' in format_data:
            total_br = _to_int(format_data['bit_rate'])
            if total_br:
                total_br = round(total_br / 1000)
                # Estimate audio bitrate
                audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
                audio_estimate = 0
                for audio in audio_streams:
                    codec = audio.get('codec_name', '').lower()
                    channels = audio.get('channels', 2)
                    if 'bit_rate' in audio:
                        audio_estimate += round(_to_int(audio['bit_rate']) / 1000)
                    elif codec in ('aac', 'mp3'):
                        audio_estimate += 128 if channels <= 2 else 256
                    elif codec in ('ac3', 'eac3'):
                        audio_estimate += 192 if channels <= 2 else 448
                    else:
                        audio_estimate += 192
                
                if audio_estimate == 0:
                    audio_estimate = 192
                
                overhead = int(total_br * 0.05)
                info.video_bitrate = max(total_br - audio_estimate - overhead, int(total_br * 0.6))
        
        # 5. Last resort: file size / duration
        if not info.video_bitrate:
            size = _to_int(format_data.get('size', '0'))
            duration = _to_float(format_data.get('duration', '0'))
            if size and duration and duration > 0:
                info.video_bitrate = round((size * 8) / (duration * 1000))
    
    # ========================================================================
    # AUDIO STREAM
    # ========================================================================
    audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    
    if audio_stream:
        info.audio_codec = audio_stream.get('codec_name')
        
        if 'bit_rate' in audio_stream:
            info.audio_bitrate = _to_int(audio_stream['bit_rate'])
            if info.audio_bitrate:
                info.audio_bitrate = round(info.audio_bitrate / 1000)
        
        # Fallback: estimate by codec
        if not info.audio_bitrate:
            codec = info.audio_codec.lower() if info.audio_codec else ''
            channels = audio_stream.get('channels', 2)
            if codec in ('aac', 'mp3'):
                info.audio_bitrate = 128 if channels <= 2 else 256
            elif codec in ('ac3', 'eac3'):
                info.audio_bitrate = 192 if channels <= 2 else 448
            elif codec in ('opus', 'vorbis'):
                info.audio_bitrate = 96 if channels <= 2 else 160
            else:
                info.audio_bitrate = 192
    
    # ========================================================================
    # DURATION
    # ========================================================================
    info.duration = _to_float(format_data.get('duration', '0'))
    
    # ========================================================================
    # VBR/CBR detection
    # ========================================================================
    info.is_vbr = detect_cbr_vbr(filepath)
    
    logger.info(f"  → {info}")
    
    return info


# ============================================================================
# DECISION LOGIC
# ============================================================================

def should_transcode(info: MediaInfo, args: argparse.Namespace, logger: Logger) -> Tuple[bool, str]:
    """
    Détermine si le fichier doit être transcodé.
    Retourne (should_transcode: bool, reason: str)
    """
    reasons = []
    
    # VIDEO checks
    if info.video_bitrate and info.video_bitrate > args.vb:
        reasons.append(f"video bitrate {info.video_bitrate} > {args.vb} kb/s")
    
    if info.video_codec and info.video_codec != args.vc:
        reasons.append(f"video codec {info.video_codec} != {args.vc}")
    
    if args.force_cbr and info.is_vbr:
        reasons.append("VBR → CBR conversion requested")
    
    if info.width and info.width > args.max_width:
        reasons.append(f"width {info.width} > {args.max_width}")
    
    if info.height and info.height > args.max_height:
        reasons.append(f"height {info.height} > {args.max_height}")
    
    # AUDIO checks
    if info.audio_bitrate and info.audio_bitrate > args.ab:
        reasons.append(f"audio bitrate {info.audio_bitrate} > {args.ab} kb/s")
    
    if info.audio_codec and info.audio_codec != args.ac:
        reasons.append(f"audio codec {info.audio_codec} != {args.ac}")
    
    if reasons:
        reason_str = "; ".join(reasons)
        logger.info(f"  → Transcode needed: {reason_str}")
        return True, reason_str
    
    logger.info(f"  → No transcode needed")
    return False, "already optimal"


# ============================================================================
# TRANSCODING
# ============================================================================

def build_ffmpeg_command(src: Path, dst: Path, info: MediaInfo, args: argparse.Namespace) -> list:
    """Construit la commande ffmpeg selon les paramètres"""
    
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", str(src)]
    
    # ========================================================================
    # VIDEO ENCODING
    # ========================================================================
    
    # Determine encoder
    if args.gpu and args.gpu != 'none':
        encoder = GPU_ENCODERS.get(args.gpu, {}).get(args.vc)
        if not encoder:
            encoder = args.vc  # Fallback to software
    else:
        encoder = f"lib{args.vc}" if args.vc != 'h264' else 'libx264'
        if args.vc == 'hevc':
            encoder = 'libx265'
        elif args.vc == 'av1':
            encoder = 'libaom-av1'
    
    cmd.extend(["-c:v", encoder])
    
    # Preset (only for software encoders)
    if not args.gpu or args.gpu == 'none':
        preset = QUALITY_PRESETS.get(args.quality, 'faster')
        cmd.extend(["-preset", preset])
    
    # Bitrate control
    if args.force_cbr or not info.is_vbr:
        # CBR mode
        cmd.extend([
            "-b:v", f"{args.vb}k",
            "-maxrate", f"{args.vb}k",
            "-bufsize", f"{args.vb * 6}k"
        ])
    else:
        # VBR mode
        cmd.extend(["-b:v", f"{args.vb}k", "-maxrate", f"{int(args.vb * 1.2)}k"])
    
    # Resolution scaling
    scale_filter = None
    if info.width and info.height:
        if info.width > args.max_width or info.height > args.max_height:
            # Calculate scaling keeping aspect ratio
            scale_w = args.max_width if info.width > args.max_width else -2
            scale_h = args.max_height if info.height > args.max_height else -2
            scale_filter = f"scale={scale_w}:{scale_h}"
    
    if scale_filter:
        cmd.extend(["-vf", scale_filter])
    
    # Pixel format
    cmd.extend(["-pix_fmt", "yuv420p"])
    
    # ========================================================================
    # AUDIO ENCODING
    # ========================================================================
    
    needs_audio_encode = False
    if info.audio_codec != args.ac or (info.audio_bitrate and info.audio_bitrate > args.ab):
        needs_audio_encode = True
    
    if needs_audio_encode:
        audio_encoder = args.ac
        if args.ac == 'aac':
            audio_encoder = 'aac'
        elif args.ac == 'opus':
            audio_encoder = 'libopus'
        elif args.ac == 'ac3':
            audio_encoder = 'ac3'
        
        cmd.extend(["-c:a", audio_encoder, "-b:a", f"{args.ab}k"])
    else:
        cmd.extend(["-c:a", "copy"])
    
    # ========================================================================
    # SUBTITLES & OUTPUT
    # ========================================================================
    
    cmd.extend(["-c:s", "copy"])
    
    # Remove old metadata to avoid confusion
    cmd.extend(["-map_metadata", "0", "-map_metadata:s:v", "-1", "-map_chapters", "0"])
    
    cmd.append(str(dst))
    
    return cmd


def transcode_file(src: Path, dst: Path, info: MediaInfo, args: argparse.Namespace, logger: Logger) -> bool:
    """
    Transcode un fichier. Retourne True si succès, False sinon.
    """
    logger.info(f"Transcoding: {src.name} → {dst.name}")
    
    if args.dry_run:
        logger.info("  → DRY RUN: skipping actual transcode")
        return True
    
    # Create destination directory
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # Build command
    cmd = build_ffmpeg_command(src, dst, info, args)
    
    if args.verbose:
        logger.info(f"  → Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=False, text=True)
        
        if result.returncode == 0:
            logger.info(f"  → Success")
            return True
        else:
            logger.error(f"ffmpeg failed with code {result.returncode} for {src}")
            # Remove partial file
            if dst.exists():
                try:
                    dst.unlink()
                except Exception:
                    pass
            return False
            
    except Exception as e:
        logger.error(f"Exception during transcode of {src}: {e}")
        if dst.exists():
            try:
                dst.unlink()
            except Exception:
                pass
        return False


# ============================================================================
# DONE FILE MANAGEMENT
# ============================================================================

def load_done_file(done_file: Path) -> set:
    """Charge le fichier done.txt et retourne un set de chemins relatifs"""
    if not done_file.exists():
        return set()
    
    try:
        with done_file.open('r', encoding='utf-8') as f:
            return {line.strip() for line in f if line.strip()}
    except Exception:
        return set()


def append_to_done_file(done_file: Path, relative_path: str):
    """Ajoute un chemin au fichier done.txt"""
    try:
        with done_file.open('a', encoding='utf-8') as f:
            f.write(f"{relative_path}\n")
    except Exception as e:
        print(f"Warning: cannot write to {done_file}: {e}")


# ============================================================================
# DIRECTORY WALKING
# ============================================================================

def walk_source(args: argparse.Namespace, logger: Logger):
    """
    Parcourt récursivement le dossier source et traite chaque fichier vidéo.
    """
    source = Path(args.source).resolve()
    destination = Path(args.destination).resolve()
    done_file = Path(args.done_file).resolve() if args.done_file else source / "done.txt"
    
    if not source.exists() or not source.is_dir():
        logger.error(f"Source directory does not exist: {source}")
        sys.exit(1)
    
    # Load done.txt
    done_set = load_done_file(done_file)
    logger.info(f"Loaded {len(done_set)} entries from done.txt")
    
    # Collect all video files
    all_videos = []
    for root, dirs, files in os.walk(source):
        for file in files:
            if Path(file).suffix.lower() in VIDEO_EXTENSIONS:
                all_videos.append(Path(root) / file)
    
    logger.info(f"Found {len(all_videos)} video files in source")
    
    # Statistics
    stats = {
        'total': len(all_videos),
        'skipped_done': 0,
        'skipped_optimal': 0,
        'transcoded': 0,
        'failed': 0
    }
    
    # Process each file
    for src_file in all_videos:
        # Calculate relative path
        try:
            rel_path = src_file.relative_to(source)
        except ValueError:
            logger.error(f"Cannot compute relative path for {src_file}")
            continue
        
        rel_path_str = str(rel_path.as_posix())
        dst_file = destination / rel_path
        
        print(f"\n{'='*80}")
        print(f"Processing: {rel_path}")
        print(f"{'='*80}")
        
        # Check if already in done.txt
        if rel_path_str in done_set:
            if dst_file.exists():
                logger.info(f"  → Already in done.txt, skipping")
                stats['skipped_done'] += 1
                continue
            else:
                logger.warning(f"  → In done.txt but destination missing, will re-transcode")
        
        # If destination exists but not in done.txt, will re-transcode
        if dst_file.exists() and rel_path_str not in done_set:
            logger.warning(f"  → Destination exists but not in done.txt, will overwrite")
        
        # Get media info
        info = get_info(src_file, logger)
        if not info:
            logger.error(f"  → Cannot extract info, skipping")
            stats['failed'] += 1
            continue
        
        # Decide if transcode needed
        should_do, reason = should_transcode(info, args, logger)
        
        if not should_do:
            # Copy file if not exists
            if not dst_file.exists():
                logger.info(f"  → Copying (no transcode needed)")
                if not args.dry_run:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        import shutil
                        shutil.copy2(src_file, dst_file)
                    except Exception as e:
                        logger.error(f"  → Copy failed: {e}")
                        stats['failed'] += 1
                        continue
            
            stats['skipped_optimal'] += 1
            append_to_done_file(done_file, rel_path_str)
            continue
        
        # Transcode
        success = transcode_file(src_file, dst_file, info, args, logger)
        
        if success:
            stats['transcoded'] += 1
            append_to_done_file(done_file, rel_path_str)
        else:
            stats['failed'] += 1
    
    # ========================================================================
    # PROPAGATE: remove files in destination not in source
    # ========================================================================
    if args.propagate:
        logger.info("\nPropagating deletions...")
        for root, dirs, files in os.walk(destination):
            for file in files:
                dst_file = Path(root) / file
                try:
                    rel_path = dst_file.relative_to(destination)
                except ValueError:
                    continue
                
                src_file = source / rel_path
                
                if not src_file.exists():
                    logger.warning(f"  → Removing {rel_path} (not in source)")
                    if not args.dry_run:
                        try:
                            dst_file.unlink()
                        except Exception as e:
                            logger.error(f"  → Cannot remove {dst_file}: {e}")
    
    # ========================================================================
    # SUMMARY
    # ========================================================================
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Total files:        {stats['total']}")
    print(f"Skipped (done):     {stats['skipped_done']}")
    print(f"Skipped (optimal):  {stats['skipped_optimal']}")
    print(f"Transcoded:         {stats['transcoded']}")
    print(f"Failed:             {stats['failed']}")
    print(f"{'='*80}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Transcode video library with intelligent decision logic",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Required
    parser.add_argument("--source", required=True, help="Source directory")
    parser.add_argument("--destination", required=True, help="Destination directory")
    
    # Video/Audio settings
    parser.add_argument("--vc", required=True, choices=['h264', 'hevc', 'av1'], help="Video codec")
    parser.add_argument("--ac", required=True, choices=['aac', 'opus', 'ac3'], help="Audio codec")
    parser.add_argument("--vb", type=int, required=True, help="Max video bitrate (kb/s)")
    parser.add_argument("--ab", type=int, required=True, help="Max audio bitrate (kb/s)")
    
    # Resolution
    parser.add_argument("--max-width", type=int, default=1920, help="Max width (default: 1920)")
    parser.add_argument("--max-height", type=int, default=1080, help="Max height (default: 1080)")
    
    # Encoding options
    parser.add_argument("--force-cbr", action="store_true", help="Force CBR encoding")
    parser.add_argument("--quality", choices=['low', 'medium', 'high', 'very_high'], default='medium',
                        help="Encoding quality preset")
    parser.add_argument("--gpu", choices=['none', 'nvidia', 'amd'], default='none',
                        help="GPU acceleration")
    
    # Behavior
    parser.add_argument("--dry-run", action="store_true", help="Simulate without actual transcoding")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--propagate", action="store_true", 
                        help="Remove files in destination that don't exist in source")
    
    # Files
    parser.add_argument("--log", help="Log file path")
    parser.add_argument("--error-log", help="Error log file path")
    parser.add_argument("--done-file", help="Done file path (default: source/done.txt)")
    
    args = parser.parse_args()
    
    # Create logger
    logger = Logger(log_file=args.log, error_log_file=args.error_log, verbose=args.verbose)
    
    logger.info(f"Starting transcode job")
    logger.info(f"  Source: {args.source}")
    logger.info(f"  Destination: {args.destination}")
    logger.info(f"  Video: {args.vc} @ {args.vb} kb/s, max {args.max_width}x{args.max_height}")
    logger.info(f"  Audio: {args.ac} @ {args.ab} kb/s")
    logger.info(f"  Quality: {args.quality}, GPU: {args.gpu}, Force CBR: {args.force_cbr}")
    logger.info(f"  Dry run: {args.dry_run}, Propagate: {args.propagate}")
    
    # Execute
    try:
        walk_source(args, logger)
    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
