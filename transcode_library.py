#!/usr/bin/env python3
"""
transcode_library.py

Parcourt récursivement un dossier source, transcode les fichiers vidéo selon des critères
spécifiques (bitrate, codec, résolution), et maintient un fichier done.txt pour éviter
de re-transcoder les fichiers déjà traités.

NOUVEAUTÉ: Copie sélective vidéo/audio selon les besoins
- Si seul l'audio doit être traité → -c:v copy
- Si seule la vidéo doit être traitée → -c:a copy
- Si les deux → transcode complet

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

# Hiérarchie des codecs vidéo (du plus lourd au plus léger pour le décodage)
VIDEO_CODEC_HIERARCHY = [
    'av1',                                       # Très lourd
    'hevc', 'h265',                              # Lourd
    'vp9',                                       # Lourd
    'h264', 'avc',                               # Moyen (standard actuel)
    'vp8',                                       # Léger
    'vc1', 'wmv3',                               # Léger
    'mpeg2video',                                # Très léger
    'h263', 'h263p',                             # Très léger
    'mpeg4', 'msmpeg4', 'msmpeg4v2', 'msmpeg4v3' # Très léger
]

# Hiérarchie des codecs audio (du plus lourd au plus léger pour le décodage)
AUDIO_CODEC_HIERARCHY = [
    'truehd', 'dts', 'dts-hd',                  # Très lourd
    'flac',                                     # Lourd (lossless)
    'opus',                                     # Moyen-léger
    'eac3',                                     # Moyen
    'ac3',                                      # Moyen-léger
    'aac',                                      # Léger (standard actuel)
    'mp3',                                      # Très léger
    'vorbis', 'ogg',                            # Léger
    'wmav2',                                    # Très léger
    'mp2'                                       # Très léger
]


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
    audio_tracks: Optional[list] = None

    def __post_init__(self):
        if self.audio_tracks is None:
            self.audio_tracks = []

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
            print(f"   [INFO] {message}")
        self._write_file(self.log_file, f"INFO:    {message}")

    def error(self, message: str):
        print(f"  [ERROR] {message}", file=sys.stderr)
        self._write_file(self.error_log_file, f"ERROR:   {message}")

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
    logger.info(f"Analyzing: {path_name(filepath.name)}")
    
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
        # Normalisation du codec
        if info.video_codec:
            codec_lower = info.video_codec.lower()
            if codec_lower in ('hevc', 'h265'):
                info.video_codec = 'h265'
            elif codec_lower in ('h264', 'avc'):
                info.video_codec = 'h264'
        
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
    # AUDIO STREAMS (toutes les pistes)
    # ========================================================================
    audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
    info.audio_tracks = []

    for audio in audio_streams:
        track = {
            'stream_index': audio.get('index'),
            'codec': audio.get('codec_name'),
            'bitrate': None,
            'channels': audio.get('channels', 2),
            'language': audio.get('tags', {}).get('language', 'und'),
            'default': audio.get('disposition', {}).get('default', 0) == 1
        }

        if 'bit_rate' in audio:
            track['bitrate'] = round(_to_int(audio['bit_rate']) / 1000)

        # Estimation si pas de bitrate
        if not track['bitrate']:
            codec = track['codec'].lower() if track['codec'] else ''
            if codec in ('aac', 'mp3'):
                track['bitrate'] = 128 if track['channels'] <= 2 else 256
            elif codec in ('ac3', 'eac3'):
                track['bitrate'] = 192 if track['channels'] <= 2 else 448
            else:
                track['bitrate'] = 192

        info.audio_tracks.append(track)

    # Garder les anciennes propriétés pour compatibilité (première piste)
    if info.audio_tracks:
        info.audio_codec = info.audio_tracks[0]['codec']
        info.audio_bitrate = info.audio_tracks[0]['bitrate']
    # ========================================================================
    # DURATION
    # ========================================================================
    info.duration = _to_float(format_data.get('duration', '0'))
    
    # ========================================================================
    # VBR/CBR detection
    # ========================================================================
    info.is_vbr = detect_cbr_vbr(filepath)

    logger.info(f"→ {info}")

    return info


# ============================================================================
# DECISION LOGIC
# ============================================================================

def codec_rank(codec: str, hierarchy: list) -> int:
    """
    Retourne le rang d'un codec dans la hiérarchie.
    Moins le rang est élevé, meilleur est le codec.
    Retourne -1 si codec inconnu.
    """
    if not codec:
        return -1

    codec_lower = codec.lower()

    # Normalisation des alias
    if codec_lower in ('h264', 'avc', 'h264_cuvid'):
        codec_lower = 'h264'
    elif codec_lower in ('hevc', 'h265', 'hevc_cuvid'):
        codec_lower = 'hevc'
    elif codec_lower in ('vp9', 'vp09'):
        codec_lower = 'vp9'

    try:
        return hierarchy.index(codec_lower)
    except ValueError:
        return -1


def should_transcode_codec(source_codec: str, target_codec: str, hierarchy: list) -> bool:
    """
    Retourne True si le codec source est inférieur au codec cible dans la hiérarchie.
    Si le codec source est supérieur ou égal, retourne False (pas besoin de transcoder).
    """
    source_rank = codec_rank(source_codec, hierarchy)
    target_rank = codec_rank(target_codec, hierarchy)

    # Si l'un des codecs est inconnu, on transcode par sécurité
    if source_rank == -1:
        return True  # Codec source inconnu → transcoder
    if target_rank == -1:
        return False  # Codec cible inconnu → ne pas transcoder

    # Transcoder uniquement si source < target
    return source_rank < target_rank


def calculate_ideal_bitrate(source_codec: str, source_bitrate: int, target_codec: str) -> int:
    """
    Calcule le bitrate idéal pour maintenir la qualité lors d'un changement de codec.

    Retourne le bitrate équivalent dans le codec cible.
    """
    # Coefficients d'efficacité relatifs (h264 = baseline 1.0)
    codec_efficiency = {
        'h264': 1.0,
        'avc': 1.0,
        'hevc': 0.65,          # Gain de 35% seulement
        'h265': 0.65,
        'av1': 0.55,           # Gain de 45% seulement
        'vp9': 0.70,           # Gain de 30% seulement
        'mpeg2video': 1.5,
        'mpeg4': 1.2,
        'vc1': 1.3,
        'wmv3': 1.3
    }

    src_codec = source_codec.lower() if source_codec else 'h264'
    tgt_codec = target_codec.lower()

    src_efficiency = codec_efficiency.get(src_codec, 1.0)
    tgt_efficiency = codec_efficiency.get(tgt_codec, 1.0)

    # Formule: bitrate_ideal = bitrate_source × (efficiency_target / efficiency_source)
    return int(source_bitrate * (tgt_efficiency / src_efficiency))


def compute_target_resolution(src_w, src_h, max_w, max_h):
    """
    Retourne la résolution finale après application de max_width / max_height
    en conservant le ratio original.
    Si max_w ou max_h vaut None, uniquement l'autre limite est utilisée.
    """
    # Si aucune contrainte : retour source
    if max_w is None and max_h is None:
        return src_w, src_h

    # Ratio d’origine
    src_ratio = src_w / src_h

    # On choisit le dimensionnement selon la contrainte la plus stricte
    if max_w is not None and max_h is not None:
        # Calcul des résolutions possibles
        w_based_h = int(max_w / src_ratio)
        h_based_w = int(max_h * src_ratio)

        if w_based_h <= max_h:
            # Limite définie par la largeur
            return max_w, w_based_h
        else:
            # Limite définie par la hauteur
            return h_based_w, max_h

    elif max_w is not None:
        # Seulement limite largeur
        new_h = int(max_w / src_ratio)
        return max_w, new_h

    else:
        # Seulement limite hauteur
        new_w = int(max_h * src_ratio)
        return new_w, max_h


def scale_bitrate_for_resolution(bitrate: int, source_w: int, source_h: int, target_w: int, target_h: int) -> int:
    """
    Ajuste un bitrate en fonction du changement de résolution.
    Formule : bitrate × (pixels_target / pixels_source)
    """
    if not (source_w and source_h and target_w and target_h):
        return bitrate

    src_pixels = source_w * source_h
    dst_pixels = target_w * target_h

    if src_pixels <= 0:
        return bitrate

    ratio = dst_pixels / src_pixels
    return int(bitrate * ratio)


def select_audio_tracks(info: MediaInfo, args: argparse.Namespace) -> list:
    """
    Sélectionne les pistes audio à garder selon les critères.
    Retourne les indices des pistes sélectionnées.
    """
    if not info.audio_tracks:
        return []

    tracks = info.audio_tracks.copy()

    # Si une seule piste, toujours la garder
    if len(tracks) == 1:
        return [0]

    # Plusieurs pistes : appliquer les filtres

    # --one-audio-track : garder une seule piste
    if args.one_audio_track:
        # Si langue spécifiée, chercher cette langue
        if args.audio_lang:
            preferred = [t for t in tracks if t['language'] == args.audio_lang]
            if preferred:
                return [info.audio_tracks.index(preferred[0])]

        # Sinon, garder la piste par défaut
        default = [t for t in tracks if t.get('default')]
        if default:
            return [info.audio_tracks.index(default[0])]

        # Sinon, première piste
        return [0]

    # --audio-lang : filtrer par langue
    if args.audio_lang:
        preferred = [t for t in tracks if t['language'] == args.audio_lang]
        if preferred:
            tracks = preferred
        else:
            # Langue demandée non trouvée : garder la piste par défaut
            default = [t for t in tracks if t.get('default')]
            if default:
                tracks = default
            else:
                tracks = [tracks[0]]

    # Retourner les indices originaux
    return [info.audio_tracks.index(t) for t in tracks]


class ShouldTranscodeError(Exception):
    pass


def should_transcode(info: MediaInfo, args: argparse.Namespace, logger: Logger) -> Tuple[bool, bool, str]:
    """
    Détermine si le fichier doit être transcodé.

    Retourne (should_transcode_video: bool, should_transcode_audio: bool, reason: str)

    - should_transcode_video: True si la vidéo doit être réencodée
    - should_transcode_audio: True si l'audio doit être réencodé
    - reason: Description des raisons
    """
    should_transcode_video_reasons = []
    should_transcode_audio_reasons = []

    # ========================================================================
    # EXCEPTION: Skip certain codecs
    # ========================================================================
    if args.skip_codec and info.video_codec in args.skip_codec:
        # Whichever reasons, don't transcode the file, because the codec is present in skipped codecs list (--skip-codecs).
        raise ShouldTranscodeError(f"File encoded with {info.video_codec}")
    if args.only_codecs and info.video_codec not in args.only_codecs:
        # Whichever reasons, don't transcode the file, because the codec is absent from allowed codecs list (--only-codecs).
        raise ShouldTranscodeError(f"[--only-codecs] File encoded with {info.video_codec} (not {', neither'.join(args.only_codecs)}). Skipping.")

    # ========================================================================
    # VIDEO CHECKS
    # ========================================================================
    if info.video_bitrate and info.video_codec:
        # Calculer le bitrate idéal pour le changement de codec
        ideal_bitrate = calculate_ideal_bitrate(info.video_codec, info.video_bitrate, args.vc)
        # Scale ideal bitrate for resolution change
        target_width, target_height = compute_target_resolution(info.width, info.height, args.max_width, args.max_height)
        ideal_bitrate = scale_bitrate_for_resolution(
            ideal_bitrate,
            info.width, info.height,
            target_width, target_height
        )

        if args.adaptive_vb:
            # Mode intelligent : utiliser min(ideal, --vb)
            effective_vb = min(ideal_bitrate, args.vb)

            if info.video_bitrate > effective_vb:
                should_transcode_video_reasons.append(
                    f"video bitrate {info.video_bitrate} > {effective_vb} kb/s "
                    f"(adaptive: ideal={ideal_bitrate}, requested={args.vb})"
                )
        else:
            # Mode classique : utiliser --vb mais avertir si gonflement
            if info.video_bitrate > args.vb:
                should_transcode_video_reasons.append(f"video bitrate {info.video_bitrate} > {args.vb} kb/s")

    elif info.video_bitrate:
        # Pas d'info codec : comportement classique
        if info.video_bitrate > args.vb:
            should_transcode_video_reasons.append(
                f"video bitrate {info.video_bitrate} > {args.vb} kb/s"
            )

    if info.video_codec:
        if args.force_codec_video:
            if info.video_codec.lower() not in (
                    args.vc.lower(), 'avc' if args.vc == 'h264' else '', 'h265' if args.vc == 'hevc' else ''):
                should_transcode_video_reasons.append(f"video codec {info.video_codec} != {args.vc} (forced)")
        else:
            if should_transcode_codec(info.video_codec, args.vc, VIDEO_CODEC_HIERARCHY):
                src_rank = codec_rank(info.video_codec, VIDEO_CODEC_HIERARCHY)
                tgt_rank = codec_rank(args.vc, VIDEO_CODEC_HIERARCHY)
                should_transcode_video_reasons.append(
                    f"video codec {info.video_codec} (rank {src_rank}) heavier than {args.vc} (rank {tgt_rank})")

    if args.force_cbr and info.is_vbr:
        should_transcode_video_reasons.append("VBR → CBR conversion requested")

    if info.width and info.width > args.max_width:
        should_transcode_video_reasons.append(f"width {info.width} > {args.max_width}")

    if info.height and info.height > args.max_height:
        should_transcode_video_reasons.append(f"height {info.height} > {args.max_height}")

    # ========================================================================
    # AUDIO CHECKS
    # ========================================================================

    # Déterminer si on doit vérifier l'audio
    # On vérifie l'audio SI :
    # 1. Il y a déjà des raisons vidéo (on transcode de toute façon)
    # 2. OU --one-audio-track est activé (réduction du nombre de pistes)
    # 3. OU --force-audio-on-language est activé
    # 4. OU --force-audio-on-channels est activé
    should_check_audio = (
            len(should_transcode_video_reasons) > 0 or
            args.one_audio_track or
            args.force_audio_on_language or
            args.force_audio_on_channels
    )

    if should_check_audio and info.audio_tracks:

        # Sélectionner les pistes qui seront gardées
        selected_tracks = select_audio_tracks(info, args)

        # ====================================================================
        # VÉRIFICATION : Réduction du nombre de pistes
        # ====================================================================
        if len(info.audio_tracks) > 1 and len(selected_tracks) < len(info.audio_tracks):
            should_transcode_audio_reasons.append(f"reducing audio tracks from {len(info.audio_tracks)} to {len(selected_tracks)}")

        # ====================================================================
        # VÉRIFICATION : --one-audio-track avec plusieurs pistes
        # ====================================================================
        if args.one_audio_track and len(info.audio_tracks) > 1:
            should_transcode_audio_reasons.append(f"multiple audio tracks ({len(info.audio_tracks)}) with --one-audio-track")

        # ====================================================================
        # VÉRIFICATION : Langue demandée n'existe pas + force
        # ====================================================================
        if args.audio_lang and args.force_audio_on_language:
            # Vérifier si la langue demandée existe
            lang_exists = any(t['language'] == args.audio_lang for t in info.audio_tracks)
            if not lang_exists and len(info.audio_tracks) > 0:
                # Langue demandée n'existe pas ET on force → transcoder
                should_transcode_audio_reasons.append(f"audio language '{args.audio_lang}' not found, keeping default track (forced)")

        # ====================================================================
        # VÉRIFICATION : Langue existe et on filtre
        # ====================================================================
        if args.audio_lang and len(info.audio_tracks) > 1:
            lang_exists = any(t['language'] == args.audio_lang for t in info.audio_tracks)
            if lang_exists and len(selected_tracks) < len(info.audio_tracks):
                should_transcode_audio_reasons.append(f"keeping only '{args.audio_lang}' audio track(s)")

        # ====================================================================
        # VÉRIFICATIONS sur la piste principale qui sera gardée
        # ====================================================================
        if selected_tracks:
            main_track_idx = selected_tracks[0]
            main_track = info.audio_tracks[main_track_idx]

            # ----------------------------------------------------------------
            # Bitrate
            # ----------------------------------------------------------------
            if main_track['bitrate'] and main_track['bitrate'] > args.ab:
                should_transcode_audio_reasons.append(f"audio bitrate {main_track['bitrate']} > {args.ab} kb/s")

            # ----------------------------------------------------------------
            # Codec
            # ----------------------------------------------------------------
            if main_track['codec']:
                if args.force_codec_audio:
                    # Forcer le codec explicitement demandé
                    if main_track['codec'].lower() != args.ac.lower():
                        should_transcode_audio_reasons.append(f"audio codec {main_track['codec']} != {args.ac} (forced)")
                elif len(should_transcode_video_reasons) > 0 or len(should_transcode_audio_reasons):
                    # Seulement comparer les "ranks" si on transcode déjà la vidéo ou l'audio
                    if should_transcode_codec(main_track['codec'], args.ac, AUDIO_CODEC_HIERARCHY):
                        src_rank = codec_rank(main_track['codec'], AUDIO_CODEC_HIERARCHY)
                        tgt_rank = codec_rank(args.ac, AUDIO_CODEC_HIERARCHY)
                        should_transcode_audio_reasons.append(f"audio codec {main_track['codec']} (rank {src_rank}) heavier than {args.ac} (rank {tgt_rank})")

            # ----------------------------------------------------------------
            # Channels (downmix nécessaire ?)
            # ----------------------------------------------------------------
            if args.audio_channels and main_track['channels'] > args.audio_channels:
                # On ajoute une raison SEULEMENT si :
                # - On transcode déjà la vidéo (len(reasons) > 0 avant cette vérif)
                # - OU --force-audio-on-channels est activé
                if len(should_transcode_audio_reasons) > 0 or args.force_audio_on_channels:
                    should_transcode_audio_reasons.append(f"audio channels {main_track['channels']} > {args.audio_channels}")

    # ========================================================================
    # RÉSULTAT: 3 valeurs
    # ========================================================================
    if should_transcode_video_reasons or should_transcode_audio_reasons:
        reason_str = '; '.join(should_transcode_video_reasons + should_transcode_audio_reasons)

        if should_transcode_video_reasons and should_transcode_audio_reasons:
            logger.info(f"→ Transcode needed (VIDEO + AUDIO): {reason_str}")
        elif should_transcode_video_reasons:
            logger.info(f"→ Transcode needed (VIDEO ONLY): {reason_str}")
        else:
            logger.info(f"→ Transcode needed (AUDIO ONLY): {reason_str}")

        return len(should_transcode_video_reasons) > 0, len(should_transcode_audio_reasons) > 0, reason_str

    logger.info(f"→ No transcode needed")
    return False, False, "already optimal"


# ============================================================================
# TRANSCODING
# ============================================================================

def build_ffmpeg_command(src: Path, dst: Path, info: MediaInfo, args: argparse.Namespace, transcode_video: bool, transcode_audio: bool, effective_vb: int = None) -> list:
    """
    Construit la commande ffmpeg selon les paramètres.

    Args:
        transcode_video: Si False, la vidéo sera copiée (-c:v copy)
        transcode_audio: Si False, l'audio sera copié (-c:a copy)
    """

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", str(src)]
    
    # ========================================================================
    # VIDEO ENCODING
    # ========================================================================

    if not transcode_video:
        # COPIE DIRECTE de la vidéo
        cmd.extend(["-c:v", "copy"])
        cmd.extend(["-map", "0:v:0"])
    else:
        # Transcodage vidéo
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
        vb = effective_vb if effective_vb is not None else args.vb

        if args.force_cbr or not info.is_vbr:
            # CBR mode
            cmd.extend([
                "-b:v", f"{vb}k",
                "-maxrate", f"{vb}k",
                "-bufsize", f"{vb * 6}k"
            ])
        else:
            # VBR mode
            cmd.extend(["-b:v", f"{vb}k", "-maxrate", f"{int(vb * 1.2)}k"])

        # Resolution scaling
        scale_filter = None
        if info.width and info.height:
            target_w, target_h = compute_target_resolution(
                info.width, info.height,
                args.max_width, args.max_height
            )
            # On ne scale que si nécessaire
            if target_w != info.width or target_h != info.height:
                scale_filter = f"scale={target_w}:{target_h}"

        if scale_filter:
            cmd.extend(["-vf", scale_filter])

        # Pixel format
        cmd.extend(["-pix_fmt", "yuv420p"])
        # Mapper le flux vidéo explicitement
        cmd.extend(["-map", "0:v:0"])

    # ========================================================================
    # AUDIO ENCODING
    # ========================================================================

    # Sélectionner les pistes à garder
    selected_tracks = select_audio_tracks(info, args)

    if not selected_tracks:
        # Pas de piste audio
        cmd.extend(["-an"])
    elif not transcode_audio:
        # COPIE DIRECTE de l'audio (toutes les pistes sélectionnées)
        for idx in selected_tracks:
            cmd.extend(["-map", f"0:a:{idx}"])
        cmd.extend(["-c:a", "copy"])
    else:
        # Remuxing/Transcodage audio
        for idx in selected_tracks:
            cmd.extend(["-map", f"0:a:{idx}"])

        # Vérifier si downmix nécessaire
        needs_downmix = False
        if args.audio_channels:
            for idx in selected_tracks:
                track = info.audio_tracks[idx]
                if track['channels'] > args.audio_channels:
                    needs_downmix = True
                    break

        # Vérifier si encodage nécessaire
        # Si une piste a besoin de réencoder, on réencode toutes les pistes
        needs_audio_encode = False
        for idx in selected_tracks:
            track = info.audio_tracks[idx]
            if track['codec'] != args.ac or (track['bitrate'] and track['bitrate'] > args.ab) or needs_downmix:
                needs_audio_encode = True
                break

        if needs_audio_encode:
            audio_encoder = 'aac' if args.ac == 'aac' else f"lib{args.ac}"
            if args.ac == 'opus':
                audio_encoder = 'libopus'

            cmd.extend(["-c:a", audio_encoder, "-b:a", f"{args.ab}k"])

            # Appliquer le downmix si nécessaire
            if needs_downmix:
                if args.audio_channels == 2:
                    cmd.extend(["-ac", "2"])  # Stéréo
                elif args.audio_channels == 1:
                    cmd.extend(["-ac", "1"])  # Mono
                else:
                    cmd.extend(["-ac", str(args.audio_channels)])
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


def path_name(path, min=10, max=50, part1=2, part2=1):
    _path = str(path)  # Convert to string if of type Path
    if len(_path) > max:
        result = f"{_path[:((part1*max//(part1+part2))-3)]}...{_path[(len(_path)-(part2*max//(part1+part2))+3):]}"
        return result if len(result) > min else _path
    return _path


def transcode_file(src: Path, dst: Path, info: MediaInfo, args: argparse.Namespace, logger: Logger, transcode_video: bool, transcode_audio: bool) -> bool:
    """Transcode un fichier. Retourne True si succès, False sinon."""

    # Message descriptif
    if transcode_video and transcode_audio:
        logger.info(f"Transcoding (video+audio): {path_name(src.name)} → {path_name(dst.name)}")
    elif transcode_video:
        logger.info(f"Transcoding (video only, audio copy): {path_name(src.name)} → {path_name(dst.name)}")
    elif transcode_audio:
        logger.info(f"Remuxing (audio only, video copy): {path_name(src.name)} → {path_name(dst.name)}")
    else:
        logger.info(f"Copying: {path_name(src.name)} → {path_name(dst.name)}")

    if args.dry_run:
        logger.info("  → DRY RUN: skipping actual transcode")
        return True
    
    # Create destination directory
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # Build command
    effective_vb = args.vb
    # Only calculate adaptive bitrate if we're actually transcoding video
    if transcode_video and info.video_codec and info.video_bitrate:
        ideal_bitrate = calculate_ideal_bitrate(info.video_codec, info.video_bitrate, args.vc)

        # IMPORTANT: Scale ideal bitrate for resolution change (same as in should_transcode)
        target_width, target_height = compute_target_resolution(
            info.width, info.height,
            args.max_width, args.max_height
        )
        ideal_bitrate = scale_bitrate_for_resolution(
            ideal_bitrate,
            info.width, info.height,
            target_width, target_height
        )

        if ideal_bitrate < args.vb:
            if args.adaptive_vb:
                effective_vb = ideal_bitrate
                logger.info(f'[--adaptive-vb]: Using bitrate {effective_vb} kb/s instead of user defined {args.vb} kb/s')
            else:
                logger.warning(f'User defined bitrate ({args.vb} kb/s) higher than ideal ({ideal_bitrate} kb/s). Process might result in a file larger than necessary. Consider using --adaptive-vb')

    cmd = build_ffmpeg_command(src, dst, info, args, transcode_video, transcode_audio, effective_vb)

    if args.verbose:
        logger.info(f"→ Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=False, text=True)
        
        if result.returncode == 0:
            logger.info(f"→ Success")
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
        'skipped_codec': 0,
        'transcoded_both': 0,
        'transcoded_video': 0,
        'transcoded_audio': 0,
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

        print(f"\n{'=' * 80}")
        print(f"• Processing file {all_videos.index(src_file) + 1}/{len(all_videos)}...")
        print(f"  {path_name(rel_path, 20, 80)}")
        print(f"{'=' * 80}")

        # Check if already in done.txt
        if rel_path_str in done_set:
            if dst_file.exists():
                logger.info(f"→ Already in done.txt. Skipping.")
                stats['skipped_done'] += 1
                continue
            else:
                logger.warning(f"→ In done.txt but destination missing. Skipping.")
                # if in done.txt, always pass!
                # ToDo: Some --param to override this?
                continue

        # If destination exists but not in done.txt, will re-transcode
        if dst_file.exists() and rel_path_str not in done_set:
            logger.warning(f"→ Destination exists but not in done.txt, will overwrite")

        # Get media info
        info = get_info(src_file, logger)
        if not info:
            logger.error(f"→ Cannot extract info, skipping")
            stats['failed'] += 1
            continue
        
        # Decide if transcode needed
        try:
            transcode_video, transcode_audio, reason = should_transcode(info, args, logger)
        except ShouldTranscodeError as e:
            logger.info(f"→ Skipped: {e}")
            stats['skipped_codec'] = stats.get('skipped_codec', 0) + 1
            continue
        except Exception as e:
            logger.error(f"→ Skipped: {e}")
            stats["failed"] += 1
            continue

        if not transcode_video and not transcode_audio:
            # Copy file if not exists
            if not dst_file.exists():
                logger.info(f"→ Copying (no transcode needed)")
                if not args.dry_run:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        import shutil
                        shutil.copy2(src_file, dst_file)
                    except Exception as e:
                        logger.error(f"→ Copy failed: {e}")
                        stats['failed'] += 1
                        continue
            
            stats['skipped_optimal'] += 1
            append_to_done_file(done_file, rel_path_str)
            continue
        
        # Transcode
        success = transcode_file(src_file, dst_file, info, args, logger, transcode_video, transcode_audio)

        if success:
            if transcode_video and transcode_audio:
                stats['transcoded_both'] += 1
            elif transcode_video:
                stats['transcoded_video'] += 1
            else:
                stats['transcoded_audio'] += 1
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
                    logger.warning(f"→ Removing {rel_path} (not in source)")
                    if not args.dry_run:
                        try:
                            dst_file.unlink()
                        except Exception as e:
                            logger.error(f"→ Cannot remove {dst_file}: {e}")

    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"Total files:              {stats['total']}")
    print(f"Skipped (done):           {stats['skipped_done']}")
    print(f"Skipped (optimal):        {stats['skipped_optimal']}")
    print(f"Skipped (codec):          {stats['skipped_codec']}")
    print(f"Transcoded (video+audio): {stats['transcoded_both']}")
    print(f"Transcoded (video only):  {stats['transcoded_video']}")
    print(f"Transcoded (audio only):  {stats['transcoded_audio']}")
    print(f"Failed:                   {stats['failed']}")
    print(f"{'=' * 80}")


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
    parser.add_argument("--adaptive-vb", action="store_true", help="Adjust target bitrate based on codec efficiency to avoid unnecessary file size increase")

    parser.add_argument("--audio-channels", type=int, help="Max number of channels (e.g., 2 for stereo, 6 for 5.1)")
    parser.add_argument("--audio-lang", help="Preferred audio language (e.g., 'fr', 'en')")
    parser.add_argument("--one-audio-track", action="store_true", help="Keep only one audio track (triggers transcode if multiple tracks exist)")
    parser.add_argument("--force-audio-on-language", action="store_true", help="Force audio check even if video doesn't need transcode (language filtering)")
    parser.add_argument("--force-audio-on-channels", action="store_true", help="Force audio check even if video doesn't need transcode (channel downmix)")
    # Resolution
    parser.add_argument("--max-width", type=int, default=1920, help="Max width (default: 1920)")
    parser.add_argument("--max-height", type=int, default=1080, help="Max height (default: 1080)")
    
    # Encoding options
    parser.add_argument("--force-cbr", action="store_true", help="Force CBR encoding")
    parser.add_argument("--force-codec-video", action="store_true", help="Force video codec conversion even if source codec is lighter")
    parser.add_argument("--force-codec-audio", action="store_true", help="Force audio codec conversion even if source codec is lighter")
    parser.add_argument("--quality", choices=['low', 'medium', 'high', 'very_high'], default='very_high', help="Encoding quality preset")
    parser.add_argument("--gpu", choices=['none', 'nvidia', 'amd'], default='none', help="GPU acceleration")
    parser.add_argument("--skip-codec", choices=['h264', 'av1','h265','vp9'], action="append", default=[], help="Skip file when encoded with these codecs.")
    parser.add_argument("--only-codecs", choices=['av1','h265','vp9'], action="append", default=[], help="If present, only process files encoded with these codecs.")

    # Behavior
    parser.add_argument("--dry-run", action="store_true", help="Simulate without actual transcoding")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--propagate", action="store_true", help="Remove files in destination that don't exist in source")

    # Files
    parser.add_argument("--log", help="Log file path")
    parser.add_argument("--error-log", help="Error log file path")
    parser.add_argument("--done-file", help="Done file path (default: source/done.txt)")
    
    args = parser.parse_args()

    # Validation
    if args.vb <= 0 or args.ab <= 0:
        print("Error: Bitrates must be positive", file=sys.stderr)
        sys.exit(1)

    if args.max_width <= 0 or args.max_height <= 0:
        print("Error: Resolution must be positive", file=sys.stderr)
        sys.exit(1)
    
    # Create logger
    logger = Logger(log_file=args.log, error_log_file=args.error_log, verbose=args.verbose)
    
    logger.info(f"Starting transcode job")
    logger.info(f"Source: {args.source}")
    logger.info(f"Destination: {args.destination}")
    logger.info(f"Video: {args.vc} @ {args.vb} kb/s, max {args.max_width}x{args.max_height}")
    logger.info(f"Audio: {args.ac} @ {args.ab} kb/s")
    logger.info(f"Quality: {args.quality}, GPU: {args.gpu}, Force CBR: {args.force_cbr}")
    if args.skip_codec:
        logger.info(f"Skip files with: {', '.join(args.skip_codec)}")
    if args.only_codecs:
        logger.info(f"Processing files encoded with: {', '.join(args.only_codecs)}")
    logger.info(f"Dry run: {args.dry_run}, Propagate: {args.propagate}")

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
