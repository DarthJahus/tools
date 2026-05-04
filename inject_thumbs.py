import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv"}
THUMB_EXTENSIONS = {".webp", ".jpg", ".jpeg", ".png"}
YOUTUBE_ID_RE    = re.compile(r'\[([A-Za-z0-9_-]{11})\]')


def log(message: str, log_path: Path | None = None):
    print(message)
    if log_path:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(message + "\n")


def extract_yt_id(stem: str) -> str | None:
    m = YOUTUBE_ID_RE.search(stem)
    return m.group(1) if m else None


def find_thumbnail(
    stem: str,
    thumb_dir: Path,
    strategy: str | None = None,
    threshold: float = 0.9,
) -> tuple[Path | None, str]:

    def exact() -> tuple[Path | None, str]:
        for ext in THUMB_EXTENSIONS:
            c = thumb_dir / (stem + ext)
            if c.exists():
                return c, "exact"
        return None, ""

    def youtubeid() -> tuple[Path | None, str]:
        yt_id = extract_yt_id(stem)
        if not yt_id:
            return None, ""
        for f in thumb_dir.iterdir():
            if f.suffix.lower() in THUMB_EXTENSIONS and yt_id in f.stem:
                return f, f"id:{yt_id}"
        return None, ""

    def fuzzy() -> tuple[Path | None, str]:
        best_score, best_file = 0.0, None
        for f in thumb_dir.iterdir():
            if f.suffix.lower() not in THUMB_EXTENSIONS:
                continue
            score = SequenceMatcher(None, stem.lower(), f.stem.lower()).ratio()
            if score > best_score:
                best_score, best_file = score, f
        if best_file and best_score >= threshold:
            return best_file, f"fuzzy:{best_score:.0%}"
        return None, ""

    if strategy == "exact":     return exact()
    if strategy == "youtubeid": return youtubeid()
    if strategy == "fuzzy":     return fuzzy()

    # Cascade par défaut : exact → youtubeid → fuzzy
    for fn in (exact, youtubeid, fuzzy):
        result, method = fn()
        if result:
            return result, method
    return None, ""


def run_ffmpeg(video: Path, thumbnail: Path, output: Path) -> tuple[bool, int]:
    cmd = [
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error", "-stats",
        "-i", str(video),
        "-i", str(thumbnail),
        "-map", "0:V",
        "-map", "0:a",
        "-map", "0:s?",
        "-map", "1",
        "-c", "copy",
        "-c:s", "mov_text",
        "-c:v:1", "mjpeg",
        "-disposition:v:1", "attached_pic",
        str(output),
    ]
    result = subprocess.run(cmd)
    return result.returncode == 0, result.returncode


def main():
    parser = argparse.ArgumentParser(description="Injecte les miniatures dans les fichiers vidéo via ffmpeg.")
    parser.add_argument("--source", required=True, help="Dossier source des vidéos")
    parser.add_argument("--thumbnail-source", help="Dossier des miniatures (défaut : source)")
    parser.add_argument("--destination", help="Dossier de sortie (défaut : source)")
    parser.add_argument("--delete-source", action="store_true", help="Supprime les fichiers source après traitement")
    parser.add_argument("--log", help="Fichier de log")
    parser.add_argument("--match", choices=["exact", "youtubeid", "fuzzy"], default=None, help="Stratégie de correspondance (défaut : cascade exact→youtubeid→fuzzy)")
    parser.add_argument("--threshold", type=float, default=0.9, help="Seuil de similarité pour --match fuzzy (0-1, défaut : 0.9)")
    args = parser.parse_args()

    source    = Path(args.source)
    thumb_dir = Path(args.thumbnail_source) if args.thumbnail_source else source
    log_path  = Path(args.log) if args.log else None
    use_temp  = args.destination is None or Path(args.destination) == source
    dest_dir  = source if use_temp else Path(args.destination)

    if not shutil.which("ffmpeg"):
        sys.exit("❌ ffmpeg introuvable dans PATH")
    if not source.exists():
        sys.exit(f"❌ Source introuvable : {source}")
    if not thumb_dir.exists():
        sys.exit(f"❌ Dossier miniatures introuvable : {thumb_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    ok, err, skipped = 0, 0, 0

    for video in sorted(source.iterdir()):
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        thumbnail, method = find_thumbnail(
            video.stem, thumb_dir,
            strategy=args.match,
            threshold=args.threshold,
        )
        if not thumbnail:
            log(f"⚠️  Miniature introuvable pour {video.name}, ignoré.", log_path)
            skipped += 1
            continue

        final_mp4  = dest_dir / (video.stem + ".mp4")
        staged_mp4 = dest_dir / (video.stem + ".temp.mp4")

        log(f"⏳ [{method}] {video.name}  ←  {thumbnail.name}", log_path)

        if use_temp:
            tmp = Path(tempfile.mktemp(suffix=".mp4"))
            try:
                success, code = run_ffmpeg(video, thumbnail, tmp)
                if success and tmp.exists():
                    shutil.move(str(tmp), str(staged_mp4))
                else:
                    log(f"❌ ffmpeg a échoué (code {code}) pour {video.name}", log_path)
                    err += 1
                    continue
            except Exception as e:
                log(f"❌ Exception pour {video.name} : {e}", log_path)
                err += 1
                continue
            finally:
                if tmp.exists():
                    tmp.unlink()
        else:
            success, code = run_ffmpeg(video, thumbnail, staged_mp4)
            if not success:
                log(f"❌ ffmpeg a échoué (code {code}) pour {video.name}", log_path)
                if staged_mp4.exists():
                    staged_mp4.unlink()
                err += 1
                continue

        # staged_mp4 est en place — suppression des sources
        if args.delete_source:
            deleted_video = False
            try:
                video.unlink()
                log(f"   Vidéo source supprimée.", log_path)
                deleted_video = True
            except Exception as e:
                log(f"   ⚠️  Impossible de supprimer la vidéo : {e}", log_path)

            if deleted_video:
                try:
                    thumbnail.unlink()
                    log(f"   Miniature supprimée.", log_path)
                except Exception as e:
                    log(f"   ⚠️  Impossible de supprimer la miniature : {e}", log_path)

        staged_mp4.rename(final_mp4)
        log(f"✅ {final_mp4.name}", log_path)
        ok += 1

    log(f"\n── Résumé ──────────────────────", log_path)
    log(f"  ✅ Succès  : {ok}",       log_path)
    log(f"  ❌ Échecs  : {err}",      log_path)
    log(f"  ⚠️ Ignorés : {skipped}",  log_path)


if __name__ == "__main__":
    main()
