# inject_thumbs

Embed thumbnails into video files using ffmpeg. Built for bulk-fixing yt-dlp libraries downloaded without `--embed-thumbnail`.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) in PATH

## Usage

```bash
python inject_thumbs.py --source <video_dir> [options]
```

### Options

| Flag | Description |
|---|---|
| `--source` | Video source directory (**required**) |
| `--thumbnail-source` | Thumbnail directory (default: same as `--source`) |
| `--destination` | Output directory (default: same as `--source`) |
| `--delete-source` | Delete source video and thumbnail after successful processing |
| `--log` | Path to log file |
| `--match` | Matching strategy: `exact`, `youtubeid`, `fuzzy` (default: cascade) |
| `--threshold` | Similarity threshold for fuzzy matching, 0–1 (default: `0.9`) |

### Matching strategies

By default the script tries all three strategies in order until a match is found:

1. **exact** — filename stem must match exactly
2. **youtubeid** — matches on the 11-character YouTube ID found in brackets, e.g. `[dQw4w9WgXcQ]`
3. **fuzzy** — similarity ratio via `difflib.SequenceMatcher`, configurable with `--threshold`

The YouTube ID strategy handles cases where the video and thumbnail titles diverge after download (e.g. a title was edited on YouTube):

```
20260503 - Han Solo & Chewbacca： Shadows of Nar Shaddaa [41XTMAcy-HM].mkv
20260503 - Han Solo： Shadows of Nar Shaddaa [41XTMAcy-HM].webp
```

### Output format

Videos are always output as `.mp4`. Thumbnails are re-encoded as JPEG (required by the MP4 container). WebVTT subtitles, if present in the source MKV, are converted to `mov_text`.

Processing is staged through a `.temp.mp4` file that is renamed to `.mp4` only after success — and source files are only deleted after the output is confirmed in place.

## Examples

```bash
# Basic usage — output replaces source files
python inject_thumbs.py \
  --source "\\truenas\Media\YouTube\My Channel"

# Separate thumbnail folder, delete originals after processing
python inject_thumbs.py \
  --source "\\truenas\Media\YouTube\My Channel" \
  --thumbnail-source "D:\downloads\My Channel" \
  --delete-source \
  --log "D:\downloads\My Channel.log"

# Force YouTube ID matching only
python inject_thumbs.py \
  --source "\\truenas\Media\YouTube\My Channel" \
  --thumbnail-source "D:\downloads\My Channel" \
  --match youtubeid

# Fuzzy matching at 80% threshold
python inject_thumbs.py \
  --source "\\truenas\Media\YouTube\My Channel" \
  --thumbnail-source "D:\downloads\My Channel" \
  --match fuzzy --threshold 0.8
```

### PowerShell tip

```powershell
$nom = "My Channel"

python D:\scripts\inject_thumbs.py `
  --source "\\truenas\Media\YouTube\$nom" `
  --thumbnail-source "D:\downloads\$nom" `
  --delete-source `
  --log "D:\downloads\$nom.log"
```

## Supported formats

| Input video | Input thumbnail | Output |
|---|---|---|
| `.mkv`, `.mp4` | `.webp`, `.jpg`, `.jpeg`, `.png` | `.mp4` |

## Retroactively downloading missing thumbnails with yt-dlp

If you already have a library downloaded without `--embed-thumbnail`, you can fetch the missing thumbnails without re-downloading the videos:

```bash
yt-dlp --skip-download --write-thumbnail -o "%(upload_date>%Y%m%d)s - %(title)s (%(channel)s) [%(id)s].%(ext)s" <playlist_url>
```

- `--skip-download` — fetches metadata and thumbnails only, skips the video
- `--write-thumbnail` — saves the thumbnail as a separate file (usually `.webp`)
- `-o` — output template; **must match the naming scheme used when the videos were downloaded** for exact matching to work

If you used the same `-o` template for both downloads, filenames will align perfectly and `inject_thumbs` will match them without any `--match` flag. If the video titles were edited on YouTube between the two downloads, the YouTube ID strategy will still find the correct pairs.

## Why not just use `--embed-thumbnail` in yt-dlp?

You should — going forward. This script exists for retroactively fixing libraries already downloaded without it.
