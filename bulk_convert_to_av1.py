#!/usr/bin/env python3
"""
convert_av1.py

Exploration non-récursive des sous-dossiers directs de RootFolder.
Pour chaque sous-dossier:
 - cherche les .mkv
 - extrait un ID YouTube (dernier motif de 11 chars [A-Za-z0-9_-]{11} ou dernier token trouvé)
 - vérifie (optionnel) présence dans archive.txt (lu à chaque fichier)
 - vérifie done.txt (par dossier) pour ne pas re-traiter
 - si output existe et ID absent de done.txt -> supprime output et reconvertit
 - vérifie via ffprobe si vidéo est déjà en AV1 (copie alors)
 - lance ffmpeg avec les bons arguments pour av1_amf (AMD) ou av1_nvenc (NVIDIA)
 - calcule target bitrate = 70% du bitrate vidéo source (kb/s) et l'utilise via -b:v / -maxrate / -bufsize
 - écrit l'ID dans done.txt après succès
 - log des erreurs dans convert_errors.log

Dépendances: python3, ffmpeg, ffprobe. colorama optionnel (couleurs).
"""

import argparse
import subprocess
import shutil
import os
import re
import sys
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


def append_done(done_file: Path, yt_id: str):
    try:
        with done_file.open("a", encoding="utf-8") as f:
            f.write(yt_id + "\n")
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


def probe_video_bitrate_kb(input_file: Path):
    """
    Retourne le bitrate vidéo principal en kb/s (int) si disponible, sinon None.
    On essaie stream bit_rate puis format bit_rate.
    """
    # stream bit_rate
    cmd_stream = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_file)
    ]
    cp = run_cmd(cmd_stream)
    if cp.returncode == 0 and cp.stdout:
        s = cp.stdout.strip()
        if s.isdigit():
            try:
                return int(round(int(s) / 1000.0))
            except Exception:
                pass

    # fallback format bit_rate
    cmd_fmt = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_file)
    ]
    cp2 = run_cmd(cmd_fmt)
    if cp2.returncode == 0 and cp2.stdout:
        s = cp2.stdout.strip()
        if s.isdigit():
            try:
                return int(round(int(s) / 1000.0))
            except Exception:
                pass

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
        "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?"
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
        "-c:a", "aac", "-b:a", "128k",
        "-c:s", "copy",
        str(output_file)
    ]

    return args


# -------------------------
# Process a single file
# -------------------------
def process_file(file_path: Path, out_dir: Path, done_path: Path, done_set: set,
                 archive_file: Path, check_archive: bool, encoder: str, quality: str, error_log: str):
    fname = file_path.name

    yt_id = extract_youtube_id(fname)
    if not yt_id:
        print(Fore.WHITE + f"  ⊘ {fname} (ID YouTube introuvable)" + Style.RESET_ALL)
        log_error(error_log, f"IGNORÉ (ID YouTube introuvable) : {fname}\nSource : {file_path}\nDate : {now_str()}")
        return "skipped"

    # If output exists and ID not in done -> remove output to force reconversion
    output_file = out_dir / fname
    if output_file.exists() and yt_id not in done_set:
        try:
            output_file.unlink()
            print(
                Fore.YELLOW + f"  ! {fname} (output existant supprimé, ID absent de done.txt) -> reconversion" + Style.RESET_ALL)
        except Exception as e:
            print(Fore.RED + f"  ✗ {fname} (impossible de supprimer output: {e})" + Style.RESET_ALL)
            log_error(error_log,
                      f"ERREUR (suppression output) : {fname}\nSource: {file_path}\nOutput: {output_file}\nException: {e}")
            return "failed"

    # re-check done
    if yt_id in done_set:
        print(Fore.WHITE + f"  ⊘ {fname} (déjà dans done.txt)" + Style.RESET_ALL)
        return "skipped"

    # check archive live
    if check_archive:
        archive_ids = read_archive_ids(Path(archive_file))
        if yt_id not in archive_ids:
            print(Fore.WHITE + f"  ⊘ {fname} (absent archive)" + Style.RESET_ALL)
            log_error(error_log,
                      f"IGNORÉ (ABSENT ARCHIVE) : {fname}\nSource : {file_path}\nDate : {now_str()}\nRaison : ID YouTube non trouvé dans l'archive : {archive_file}")
            return "skipped"

    # check codec and get bitrate info
    codec = probe_codec(file_path)
    src_kb = probe_video_bitrate_kb(file_path)

    # display file info
    if codec == "av1":
        bitrate_info = f"{src_kb} kb/s" if src_kb else "bitrate inconnu"
        print(Fore.CYAN + f"  📄 {fname}" + Style.RESET_ALL)
        print(Fore.WHITE + f"     Codec: AV1 | Bitrate: {bitrate_info}" + Style.RESET_ALL)
        try:
            shutil.copy2(str(file_path), str(output_file))
            print(Fore.GREEN + f"  ✓ Copie vers av1/" + Style.RESET_ALL)
            append_done(done_path, yt_id)
            done_set.add(yt_id)
            return "copied"
        except Exception as e:
            print(Fore.RED + f"  ✗ Échec copie: {e}" + Style.RESET_ALL)
            log_error(error_log,
                      f"ERREUR (copie AV1) : {fname}\nSource : {file_path}\nDestination : {output_file}\nDate : {now_str()}\nException : {e}")
            return "failed"
    else:
        # calculate target bitrate
        if src_kb:
            target_kb = int(round(src_kb * 0.70))
            if target_kb < 64:
                target_kb = 64
        else:
            target_kb = None

        # display conversion info
        codec_display = codec or "inconnu"
        src_display = f"{src_kb} kb/s" if src_kb else "inconnu"
        target_display = f"{target_kb} kb/s" if target_kb else "auto"

        print(Fore.CYAN + f"  📄 {fname}" + Style.RESET_ALL)
        print(
            Fore.WHITE + f"     Codec: {codec_display} | Source: {src_display} → Cible: {target_display} (70%)" + Style.RESET_ALL)
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
            append_done(done_path, yt_id)
            done_set.add(yt_id)
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
    args = parser.parse_args()

    root = Path(args.root).resolve()
    error_log = os.path.abspath(args.error_log)
    quality = args.quality
    encoder = args.encoder
    check_archive = args.check_archive
    archive_file = Path(args.archive_file)
    target_percent = args.target_percent / 100.0

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
    print("")

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

        mkv_files = [f for f in source_path.iterdir() if f.is_file() and f.suffix.lower() == ".mkv"]
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
                error_log=error_log
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