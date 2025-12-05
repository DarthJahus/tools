#!/usr/bin/env python3
"""
convert_av1.py

Exploration non-récursive des sous-dossiers directs de RootFolder.
Pour chaque sous-dossier:
 - cherche les .mkv
 - extrait un ID YouTube (dernier motif de 11 chars [A-Za-z0-9_-]{11} ou dernier token trouvé)
 - vérifie (optionnel) présence dans archive.txt (lu à chaque fichier)
 - vérifie done.txt (par dossier) pour ne pas re-traiter
 - si output existe et ID/nom absent de done.txt -> supprime output et reconvertit
 - vérifie via ffprobe si vidéo est déjà en AV1 (copie alors)
 - lance ffmpeg avec les bons arguments pour av1_amf (AMD) ou av1_nvenc (NVIDIA)
 - calcule target bitrate = 70% du bitrate vidéo source (kb/s) et l'utilise via -b:v / -maxrate / -bufsize
 - écrit l'ID ou le nom complet dans done.txt après succès
 - log des erreurs dans convert_errors.log

Dépendances: python3, ffmpeg, ffprobe. colorama optionnel (couleurs).
"""

import argparse
import subprocess
import shutil
import os
import re
import sys
import json
from pathlib import Path
from datetime import datetime

# Optional color support
try:
    from colorama import init as colorama_init, Fore, Style

    colorama_init()
except Exception:
    class _C:
        RESET_ALL = ""
        RED = ""
        GREEN = ""
        YELLOW = ""
        CYAN = ""
        MAGENTA = ""
        BLUE = ""
        WHITE = ""


    Fore = _C()
    Style = _C()

# NVENC mapping (CQ)
NVIDIA_CQ_MAP = {
    "high_quality": 16,
    "quality": 18,
    "balanced": 22,
    "speed": 28,
}


# -------------------------
# Utilitaires
# -------------------------
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_cmd(cmd, capture_output=True, text=True):
    try:
        return subprocess.run(cmd, capture_output=capture_output, text=text)
    except FileNotFoundError as e:
        raise RuntimeError(f"Commande introuvable : {cmd[0]}") from e


def log_error(error_log_path, message):
    header = f"\n=== {now_str()} ===\n"
    try:
        with open(error_log_path, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(message)
            f.write("\n")
    except Exception as e:
        print(f"{Fore.YELLOW}[{now_str()}] WARNING: impossible d'écrire le log d'erreur: {e}{Style.RESET_ALL}")

# -------------------------
# Extraction ID YouTube
# -------------------------
YT_TOKEN_RE = re.compile(r'[A-Za-z0-9_-]{11}')


def extract_youtube_id(filename: str):
    base = Path(filename).stem
    # prefer an ID at end of base
    m = re.search(r'([A-Za-z0-9_-]{11})$', base)
    if m:
        return m.group(1)
    # otherwise return last 11-char token found
    allm = YT_TOKEN_RE.findall(base)
    if allm:
        return allm[-1]
    return None


# -------------------------
# done.txt / archive
# -------------------------
def read_done(done_file: Path):
    if not done_file.exists():
        return set()
    try:
        with done_file.open("r", encoding="utf-8", errors="ignore") as f:
            return {line.strip() for line in f if line.strip()}
    except Exception:
        return set()


def append_done(done_file: Path, identifier: str):
    try:
        with done_file.open("a", encoding="utf-8") as f:
            f.write(identifier + "\n")
    except Exception as e:
        print(f"{Fore.YELLOW}[{now_str()}] WARNING: impossible d'écrire dans {done_file}: {e}{Style.RESET_ALL}")


def read_archive_ids(archive_file: Path):
    ids = set()
    if not archive_file.exists():
        return ids
    try:
        with archive_file.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # extract all 11-char tokens on the line
                matches = YT_TOKEN_RE.findall(line)
                for m in matches:
                    ids.add(m)
    except Exception:
        return set()
    return ids


# -------------------------
# ffprobe helpers
# -------------------------
def probe_codec(input_file: Path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_file)
    ]
    cp = run_cmd(cmd)
    if cp.returncode != 0:
        return None
    out = cp.stdout.strip().splitlines()
    return out[0].strip() if out else None


def probe_video_bitrate_kb(input_file: Path, verbose: bool = False) -> int | None:
    """
    Retourne le bitrate vidéo principal en kb/s (int) si disponible, sinon None.

    Stratégie de fallback (du plus précis au plus approximatif):
    1. stream.bit_rate (rare en MKV mais le plus précis)
    2. tags STATISTICS_BPS / BPS (parfois présent en MKV)
    3. Calcul manuel: (stream.nb_read_bytes * 8) / duration
    4. format.bit_rate avec soustraction audio (inclut tout mais on estime)
    5. Calcul sur format complet en dernier recours

    Args:
        input_file: Chemin vers le fichier vidéo à analyser
        verbose: Si True, affiche quel fallback a été utilisé

    Returns:
        Bitrate vidéo en kb/s (arrondi) ou None si impossible à déterminer
    """

    def _to_int(value: str) -> int | None:
        """Convertit une chaîne en int, retourne None si échec."""
        if value and value.strip().replace('.', '', 1).replace('-', '', 1).isdigit():
            try:
                return int(float(value.strip()))
            except (ValueError, OverflowError):
                return None
        return None

    def _to_float(value: str) -> float | None:
        """Convertit une chaîne en float, retourne None si échec."""
        if value:
            try:
                return float(value.strip())
            except (ValueError, OverflowError):
                return None
        return None

    # ============================================================
    # Fallback 1: stream.bit_rate (le plus fiable quand disponible)
    # ============================================================
    cmd_stream = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_file)
    ]

    cp = run_cmd(cmd_stream)
    if cp.returncode == 0 and cp.stdout:
        bitrate_bps = _to_int(cp.stdout)
        if bitrate_bps and bitrate_bps > 0:
            if verbose:
                print(
                    Fore.BLUE + f"     [Bitrate] Méthode 1: stream.bit_rate → {round(bitrate_bps / 1000)} kb/s" + Style.RESET_ALL)
            return round(bitrate_bps / 1000)

    # ============================================================
    # Fallback 2: tags STATISTICS (souvent en MKV)
    # ============================================================
    cmd_tags = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream_tags=BPS,BPS-eng,STATISTICS_BPS",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_file)
    ]

    cp_tags = run_cmd(cmd_tags)
    if cp_tags.returncode == 0 and cp_tags.stdout:
        for line in cp_tags.stdout.strip().split('\n'):
            bitrate_bps = _to_int(line)
            if bitrate_bps and bitrate_bps > 0:
                if verbose:
                    print(
                        Fore.BLUE + f"     [Bitrate] Méthode 2: tags STATISTICS → {round(bitrate_bps / 1000)} kb/s" + Style.RESET_ALL)
                return round(bitrate_bps / 1000)

    # ============================================================
    # Fallback 3: Calcul manuel sur le stream vidéo
    # (nb_read_bytes * 8) / duration_seconds
    # ============================================================
    cmd_calc = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration,nb_read_bytes",
        "-of", "default=noprint_wrappers=1",
        str(input_file)
    ]

    cp_calc = run_cmd(cmd_calc)
    if cp_calc.returncode == 0 and cp_calc.stdout:
        duration = None
        nb_bytes = None

        for line in cp_calc.stdout.strip().split('\n'):
            if 'duration=' in line:
                duration = _to_float(line.split('duration=')[-1])
            elif 'nb_read_bytes=' in line:
                nb_bytes = _to_int(line.split('nb_read_bytes=')[-1])

        if duration and duration > 0 and nb_bytes and nb_bytes > 0:
            bitrate_bps = (nb_bytes * 8) / duration
            if verbose:
                print(
                    Fore.BLUE + f"     [Bitrate] Méthode 3: calcul stream (bytes/duration) → {round(bitrate_bps / 1000)} kb/s" + Style.RESET_ALL)
            return round(bitrate_bps / 1000)

    # ============================================================
    # Fallback 4: format.bit_rate avec soustraction de l'audio
    # ============================================================
    cmd_fmt = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_file)
    ]

    cp_fmt = run_cmd(cmd_fmt)
    if cp_fmt.returncode == 0 and cp_fmt.stdout:
        total_bitrate_bps = _to_int(cp_fmt.stdout)
        if total_bitrate_bps and total_bitrate_bps > 0:
            # Récupérer infos sur toutes les pistes audio
            cmd_audio_info = [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=bit_rate,codec_name,channels",
                "-of", "json",
                str(input_file)
            ]

            cp_audio = run_cmd(cmd_audio_info)
            audio_bitrate_total = 0

            if cp_audio.returncode == 0 and cp_audio.stdout:
                try:
                    data = json.loads(cp_audio.stdout)
                    streams = data.get('streams', [])

                    for stream in streams:
                        if 'bit_rate' in stream:
                            br = _to_int(stream['bit_rate'])
                            if br and br > 0:
                                audio_bitrate_total += br
                        else:
                            codec = stream.get('codec_name', '').lower()
                            channels = stream.get('channels', 2)

                            if codec in ('aac', 'mp3'):
                                audio_bitrate_total += 128_000 if channels <= 2 else 256_000
                            elif codec in ('ac3', 'eac3'):
                                audio_bitrate_total += 192_000 if channels <= 2 else 448_000
                            elif codec in ('dts', 'truehd', 'dts-hd'):
                                audio_bitrate_total += 768_000 if channels <= 6 else 1536_000
                            elif codec in ('opus', 'vorbis'):
                                audio_bitrate_total += 96_000 if channels <= 2 else 160_000
                            elif codec == 'flac':
                                audio_bitrate_total += 600_000 if channels <= 2 else 1200_000
                            else:
                                audio_bitrate_total += 192_000

                except (json.JSONDecodeError, KeyError):
                    pass

            if audio_bitrate_total == 0:
                audio_bitrate_total = 192_000

            overhead = int(total_bitrate_bps * 0.05)
            estimated_video_bps = total_bitrate_bps - audio_bitrate_total - overhead
            estimated_video_bps = max(estimated_video_bps, int(total_bitrate_bps * 0.6))

            if estimated_video_bps > 0:
                if verbose:
                    audio_kb = round(audio_bitrate_total / 1000)
                    overhead_kb = round(overhead / 1000)
                    print(
                        Fore.BLUE + f"     [Bitrate] Méthode 4: format.bit_rate - audio ({audio_kb} kb/s) - overhead ({overhead_kb} kb/s) → {round(estimated_video_bps / 1000)} kb/s" + Style.RESET_ALL)
                return round(estimated_video_bps / 1000)

    # ============================================================
    # Fallback 5: Calcul sur format complet (dernier recours)
    # ============================================================
    cmd_fmt_calc = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size",
        "-of", "default=noprint_wrappers=1",
        str(input_file)
    ]

    cp_fmt_calc = run_cmd(cmd_fmt_calc)
    if cp_fmt_calc.returncode == 0 and cp_fmt_calc.stdout:
        duration = None
        size_bytes = None

        for line in cp_fmt_calc.stdout.strip().split('\n'):
            if 'duration=' in line:
                duration = _to_float(line.split('duration=')[-1])
            elif 'size=' in line:
                size_bytes = _to_int(line.split('size=')[-1])

        if duration and duration > 0 and size_bytes and size_bytes > 0:
            bitrate_bps = (size_bytes * 8) / duration
            if verbose:
                print(
                    Fore.BLUE + f"     [Bitrate] Méthode 5: calcul format complet (size/duration) → {round(bitrate_bps / 1000)} kb/s (APPROXIMATIF)" + Style.RESET_ALL)
            return round(bitrate_bps / 1000)

    if verbose:
        print(Fore.RED + f"     [Bitrate] ✗ Impossible de déterminer le bitrate" + Style.RESET_ALL)

    return None

# -------------------------
# Build ffmpeg args
# -------------------------
def build_ffmpeg_args(encoder: str, quality: str, target_kb: int, input_file: Path, output_file: Path):
    """
    Retourne la liste d'arguments pour ffmpeg (inclut mapping, audio, subs et output).
    target_kb is integer kb/s or None
    """
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", str(input_file),
        "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
        "-map_metadata", "0",
        "-map_metadata:s:v", "-1"
    ]

    if encoder == "amd":
        args += [
            "-c:v", "av1_amf",
            "-usage", "transcoding",
            "-quality", quality,
            "-pix_fmt", "yuv420p"
        ]
        if target_kb:
            maxrate = int(round(target_kb * 1.1))
            bufsize = int(round(target_kb * 2))
            args += ["-b:v", f"{target_kb}k", "-maxrate", f"{maxrate}k", "-bufsize", f"{bufsize}k"]
    else:
        # nvidia
        cq = NVIDIA_CQ_MAP.get(quality, 18)
        args += [
            "-c:v", "av1_nvenc",
            "-preset", "p7",
            "-tune", "hq",
            "-rc", "vbr",
        ]
        if target_kb:
            maxrate = int(round(target_kb * 1.1))
            bufsize = int(round(target_kb * 2))
            # we still provide a cq as guidance but constrain bitrate
            args += ["-cq", str(cq), "-b:v", f"{target_kb}k", "-maxrate", f"{maxrate}k", "-bufsize", f"{bufsize}k"]
        else:
            args += ["-cq", str(cq), "-b:v", "0"]

    # audio + subtitles + output
    args += [
        # "-c:a", "aac", "-b:a", "128k",
        "-c:a", "copy",
        "-c:s", "copy",
        str(output_file)
    ]

    return args


# -------------------------
# Process a single file
# -------------------------
def process_file(file_path: Path, out_dir: Path, done_path: Path, done_set: set,
                 archive_file: Path, check_archive: bool, encoder: str, quality: str, target_percent: float, error_log: str, verbose: bool = False, min_bitrate_kb: int = None, force_av1: bool = False):
    fname = file_path.name

    # Extraire l'ID YouTube s'il existe
    yt_id = extract_youtube_id(fname)

    # Déterminer l'identifiant à utiliser dans done.txt
    # Si ID YouTube existe: utiliser l'ID, sinon: utiliser le nom complet du fichier
    identifier = yt_id if yt_id else fname

    # If output exists and identifier not in done -> remove output to force reconversion
    output_file = out_dir / fname
    if output_file.exists() and identifier not in done_set:
        try:
            output_file.unlink()
            print(
                Fore.YELLOW + f"  ! {fname} (output existant supprimé, absent de done.txt) -> reconversion" + Style.RESET_ALL)
        except Exception as e:
            print(Fore.RED + f"  ✗ {fname} (impossible de supprimer output: {e})" + Style.RESET_ALL)
            log_error(error_log,
                      f"ERREUR (suppression output) : {fname}\nSource: {file_path}\nOutput: {output_file}\nException: {e}")
            return "failed"

    # re-check done
    if identifier in done_set:
        print(Fore.WHITE + f"  ⊘ {fname} (déjà dans done.txt)" + Style.RESET_ALL)
        return "skipped"

    # check archive live UNIQUEMENT si --check-archive est activé ET qu'un ID YouTube existe
    if check_archive:
        if not yt_id:
            # Pas d'ID YouTube et --check-archive activé: on skip
            print(Fore.WHITE + f"  ⊘ {fname} (ID YouTube introuvable, requis pour --check-archive)" + Style.RESET_ALL)
            log_error(error_log,
                      f"IGNORÉ (ID YouTube introuvable avec --check-archive) : {fname}\nSource : {file_path}\nDate : {now_str()}")
            return "skipped"

        archive_ids = read_archive_ids(Path(archive_file))
        if yt_id not in archive_ids:
            print(Fore.WHITE + f"  ⊘ {fname} (absent archive)" + Style.RESET_ALL)
            log_error(error_log,
                      f"IGNORÉ (ABSENT ARCHIVE) : {fname}\nSource : {file_path}\nDate : {now_str()}\nRaison : ID YouTube non trouvé dans l'archive : {archive_file}")
            return "skipped"

    # check codec and get bitrate info
    codec = probe_codec(file_path)
    src_kb = probe_video_bitrate_kb(file_path, verbose=verbose)
    # Vérifier le bitrate minimum si spécifié
    if min_bitrate_kb and src_kb:
        if src_kb < min_bitrate_kb:
            print(
                Fore.YELLOW + f"  ⊘ {fname} (bitrate {src_kb} kb/s < minimum {min_bitrate_kb} kb/s)" + Style.RESET_ALL)
            log_error(error_log,
                      f"IGNORÉ (BITRATE TROP BAS) : {fname}\nSource : {file_path}\nDate : {now_str()}\nBitrate détecté : {src_kb} kb/s\nBitrate minimum requis : {min_bitrate_kb} kb/s")
            return "skipped"

    # display file info
    if codec == "av1" and not force_av1:
        bitrate_info = f"{src_kb} kb/s" if src_kb else "bitrate inconnu"
        id_info = f"ID: {yt_id}" if yt_id else "sans ID YouTube"
        print(Fore.CYAN + f"  📄 {fname} ({id_info})" + Style.RESET_ALL)
        print(Fore.WHITE + f"     Codec: AV1 | Bitrate: {bitrate_info}" + Style.RESET_ALL)
        try:
            shutil.copy2(str(file_path), str(output_file))
            print(Fore.GREEN + f"  ✓ Copie vers av1/" + Style.RESET_ALL)
            append_done(done_path, identifier)
            done_set.add(identifier)
            return "copied"
        except Exception as e:
            print(Fore.RED + f"  ✗ Échec copie: {e}" + Style.RESET_ALL)
            log_error(error_log,
                      f"ERREUR (copie AV1) : {fname}\nSource : {file_path}\nDestination : {output_file}\nDate : {now_str()}\nException : {e}")
            return "failed"
    else:
        # Si codec AV1 et force_av1 activé, afficher un message
        if codec == "av1" and force_av1:
            print(Fore.MAGENTA + f"  ⚠ {fname} déjà en AV1, reconversion forcée" + Style.RESET_ALL)
        # calculate target bitrate
        if src_kb:
            target_kb = int(round(src_kb * target_percent))
            # Appliquer le minimum absolu (64 kb/s ou min_bitrate_kb si spécifié)
            effective_min = max(64, min_bitrate_kb) if min_bitrate_kb else 64
            if target_kb < effective_min:
                target_kb = effective_min
        else:
            target_kb = None

        # display conversion info
        codec_display = codec or "inconnu"
        src_display = f"{src_kb} kb/s" if src_kb else "inconnu"
        target_display = f"{target_kb} kb/s" if target_kb else "auto"
        id_info = f"ID: {yt_id}" if yt_id else "sans ID YouTube"

        print(Fore.CYAN + f"  📄 {fname} ({id_info})" + Style.RESET_ALL)
        print(
            Fore.WHITE + f"     Codec: {codec_display} | Source: {src_display} → Cible: {target_display} ({target_percent*100}%)" + Style.RESET_ALL)
        print(Fore.YELLOW + f"  → Conversion en cours..." + Style.RESET_ALL, end="")

    # build args and run ffmpeg
    ff_args = build_ffmpeg_args(encoder=encoder, quality=quality, target_kb=target_kb,
                                input_file=file_path, output_file=output_file)
    try:
        # Affiche la commande complète pour le debug
        print()  # nouvelle ligne après le message de source

        # Lance ffmpeg avec sortie visible (on enlève -loglevel error et on laisse stats)
        cp = subprocess.run(ff_args, capture_output=False, text=True)

        if cp.returncode == 0:
            print(Fore.GREEN + " ✓ Conversion réussie" + Style.RESET_ALL)
            append_done(done_path, identifier)
            done_set.add(identifier)
            return "converted"
        else:
            print(Fore.RED + " ✗ Échec de la conversion" + Style.RESET_ALL)
            log_error(error_log,
                      f"ERREUR : {fname}\nSource : {file_path}\nDestination : {output_file}\nDate : {now_str()}\nEncodeur : {encoder}\nQualité : {quality}\nCode de sortie : {cp.returncode}\n")
            # remove partial file
            try:
                if output_file.exists():
                    output_file.unlink()
            except Exception:
                pass
            return "failed"
    except Exception as e:
        print(Fore.RED + f" ✗ Exception: {e}" + Style.RESET_ALL)
        log_error(error_log,
                  f"ERREUR (exception) : {fname}\nSource : {file_path}\nDestination : {output_file}\nDate : {now_str()}\nException : {e}\n")
        try:
            if output_file.exists():
                output_file.unlink()
        except Exception:
            pass
        return "failed"


# -------------------------
# Main + argparse
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="Convert .mkv in subfolders to AV1 (av1_amf / av1_nvenc).")
    parser.add_argument("--root", "-r", default=".", help="Dossier racine contenant des sous-dossiers (par défaut .)")
    parser.add_argument("--error-log", "-e", default="./convert_errors.log", help="Fichier de log des erreurs")
    parser.add_argument("--quality", "-q", choices=["high_quality", "quality", "balanced", "speed"], default="quality",
                        help="Qualité/preset")
    parser.add_argument("--encoder", choices=["amd", "nvidia"], default="amd",
                        help="Encodeur: amd (av1_amf) ou nvidia (av1_nvenc)")
    parser.add_argument("--check-archive", action="store_true",
                        help="Activer vérification archive.txt (ligne par ligne)")
    parser.add_argument("--archive-file", default="./archive.txt",
                        help="Fichier archive (lu à chaque fichier si --check-archive)")
    parser.add_argument("--target-percent", type=float, default=70.0,
                        help="Pourcentage du bitrate source à utiliser (par défaut 70)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Afficher les détails de détection du bitrate")
    parser.add_argument("--min-bitrate", type=int, default=None,
                        help="Bitrate minimum en kb/s (fichiers en-dessous ignorés, défaut: aucun minimum)")
    parser.add_argument("--force-av1", action="store_true",
                        help="Forcer la reconversion des fichiers déjà en AV1")

    args = parser.parse_args()

    root = Path(args.root).resolve()
    error_log = os.path.abspath(args.error_log)
    quality = args.quality
    encoder = args.encoder
    check_archive = args.check_archive
    archive_file = Path(args.archive_file)
    target_percent = args.target_percent / 100.0
    verbose = args.verbose
    min_bitrate_kb = args.min_bitrate
    force_av1 = args.force_av1

    if not root.exists() or not root.is_dir():
        print(Fore.RED + f"Root folder introuvable: {root}" + Style.RESET_ALL)
        sys.exit(1)

    # lister uniquement les sous-dossiers directs
    entries = [entry for entry in root.iterdir() if entry.is_dir()]
    # filter out nothing now since av1 is inside folders
    folders = entries

    print(Fore.CYAN + "=== Conversion en AV1 ===" + Style.RESET_ALL)
    print(Fore.YELLOW + f"Dossier racine : {root}" + Style.RESET_ALL)
    print(Fore.YELLOW + f"Encodeur       : {encoder}" + Style.RESET_ALL)
    print(Fore.YELLOW + f"Qualité        : {quality}" + Style.RESET_ALL)
    print(Fore.YELLOW + f"Target percent : {args.target_percent}%" + Style.RESET_ALL)
    print(Fore.YELLOW + f"Archive check  : {check_archive}" + Style.RESET_ALL)
    print(Fore.YELLOW + f"Dossiers       : {len(folders)}" + Style.RESET_ALL)
    if min_bitrate_kb:
        print(Fore.YELLOW + f"Bitrate minimum: {min_bitrate_kb} kb/s" + Style.RESET_ALL)
    print("")
    print(Fore.YELLOW + f"Forcer AV1     : {force_av1}" + Style.RESET_ALL)

    if not folders:
        print(Fore.YELLOW + "Aucun dossier à traiter." + Style.RESET_ALL)
        return

    total_converted = 0
    total_failed = 0
    total_skipped = 0

    for folder in folders:
        source_path = folder
        # create out dir inside folder as "av1/"
        out_dir = folder / "av1"
        out_dir.mkdir(parents=True, exist_ok=True)

        print(Fore.CYAN + f"\n=== Traitement : {folder.name} ===" + Style.RESET_ALL)

        # done.txt saved in the source folder itself
        done_path = folder / "done.txt"
        if not done_path.exists():
            try:
                done_path.touch()
            except Exception:
                pass
        done_set = read_done(done_path)

        archive_ids_cached = set()
        if check_archive and archive_file.exists():
            archive_ids_cached = read_archive_ids(archive_file)

        mkv_files = [f for f in source_path.iterdir() if f.is_file() and f.suffix.lower() in [".mkv",".mp4"]]
        if not mkv_files:
            print(Fore.YELLOW + "  Aucun fichier MKV trouvé" + Style.RESET_ALL)
            continue

        print(Fore.YELLOW + f"  Fichiers à convertir : {len(mkv_files)}" + Style.RESET_ALL)

        for f in mkv_files:
            # if check_archive is true we will re-read live inside process_file (function handles it)
            status = process_file(
                file_path=f,
                out_dir=out_dir,
                done_path=done_path,
                done_set=done_set,
                archive_file=archive_file,
                check_archive=check_archive,
                encoder=encoder,
                quality=quality,
                target_percent=target_percent,
                error_log=error_log,
                verbose=verbose,
                min_bitrate_kb=min_bitrate_kb,
                force_av1=force_av1
            )

            if status in ("converted", "copied"):
                total_converted += 1
            elif status == "skipped":
                total_skipped += 1
            elif status == "failed":
                total_failed += 1

    # résumé
    print(Fore.CYAN + "\n=== RÉSUMÉ ===" + Style.RESET_ALL)
    print(Fore.GREEN + f"Convertis : {total_converted}" + Style.RESET_ALL)
    print(Fore.WHITE + f"Ignorés   : {total_skipped}" + Style.RESET_ALL)
    print(Fore.RED + f"Échoués   : {total_failed}" + Style.RESET_ALL)
    if total_failed > 0:
        print(Fore.YELLOW + f"\nLes erreurs ont été enregistrées dans : {error_log}" + Style.RESET_ALL)


if __name__ == "__main__":
    main()

#EOF