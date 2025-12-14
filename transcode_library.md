# 📹 transcode_library.py - Documentation complète

## 📖 Vue d'ensemble

Script Python pour transcoder intelligemment une bibliothèque vidéo complète avec détection automatique des besoins de transcodage (vidéo/audio séparément) et gestion d'un fichier `done.txt` pour éviter les re-transcodages.

### ✨ Fonctionnalités principales

- **Transcodage sélectif** : Traite uniquement ce qui nécessite un changement (vidéo, audio, ou les deux)
- **Multi-pistes audio** : Gestion intelligente des fichiers avec plusieurs pistes audio
- **Hiérarchie de codecs** : Évite de transcoder si le codec source est déjà meilleur que la cible
- **Accélération GPU** : Support NVIDIA (NVENC) et AMD (AMF)
- **Mode dry-run** : Simulation sans transcodage réel
- **Logs détaillés** : Suivi précis de chaque opération

---

## 🚀 Installation

### Prérequis

```bash
# Installer Python 3.8+
python3 --version

# Installer FFmpeg avec support GPU (optionnel)
# Ubuntu/Debian
sudo apt install ffmpeg

# Vérifier les encodeurs disponibles
ffmpeg -encoders | grep -E "nvenc|amf|264|265"
```

### Fichier

Télécharger `transcode_library.py` et le rendre exécutable :

```bash
chmod +x transcode_library.py
```

---

## 📋 Arguments

### Arguments obligatoires

| Argument | Description | Exemple |
|----------|-------------|---------|
| `--source` | Dossier source à analyser | `/media/videos` |
| `--destination` | Dossier de destination | `/media/optimized` |
| `--vc` | Codec vidéo cible | `h264`, `hevc`, `av1` |
| `--ac` | Codec audio cible | `aac`, `opus`, `ac3` |
| `--vb` | Bitrate vidéo max (kb/s) | `6000` |
| `--ab` | Bitrate audio max (kb/s) | `192` |

### Arguments optionnels - Vidéo

| Argument | Description | Défaut |
|----------|-------------|--------|
| `--max-width` | Largeur maximale (px) | `1920` |
| `--max-height` | Hauteur maximale (px) | `1080` |
| `--force-cbr` | Forcer CBR au lieu de VBR | `False` |
| `--force-codec-video` | Forcer conversion même si codec source meilleur | `False` |
| `--quality` | Preset qualité (`low`, `medium`, `high`, `very_high`) | `medium` |
| `--gpu` | Accélération GPU (`none`, `nvidia`, `amd`) | `none` |
| `--skip-codec` | Ignorer fichiers avec codec spécifique | `av1` |

### Arguments optionnels - Audio

| Argument | Description | Défaut |
|----------|-------------|--------|
| `--audio-channels` | Nombre max de canaux (downmix) | Pas de limite |
| `--audio-lang` | Langue préférée (ISO 639-2) | Toutes |
| `--one-audio-track` | Garder une seule piste audio | `False` |
| `--force-audio-on-language` | Forcer traitement audio si langue absente | `False` |
| `--force-audio-on-channels` | Forcer traitement audio pour downmix | `False` |
| `--force-codec-audio` | Forcer conversion même si codec source meilleur | `False` |

### Arguments optionnels - Comportement

| Argument | Description | Défaut |
|----------|-------------|--------|
| `--dry-run` | Simuler sans transcoder | `False` |
| `--verbose` / `-v` | Mode verbeux | `False` |
| `--propagate` | Supprimer fichiers destination absents de source | `False` |
| `--log` | Fichier log général | Console uniquement |
| `--error-log` | Fichier log erreurs | Console uniquement |
| `--done-file` | Chemin fichier done.txt | `{source}/done.txt` |

---

## 🎯 Exemples d'utilisation

### 1. **Transcodage basique - Films 1080p**

```bash
./transcode_library.py \
  --source /media/Films \
  --destination /media/Films_optimized \
  --vc h264 \
  --ac aac \
  --vb 6000 \
  --ab 192
```

**Résultat :**
- Vidéos > 6000 kb/s → transcodées en H.264
- Audio non-AAC ou > 192 kb/s → transcodé en AAC
- Résolution max : 1920x1080 (défaut)

---

### 2. **Bibliothèque TV Shows - 1 piste audio FR**

```bash
./transcode_library.py \
  --source "/nas/TV Shows" \
  --destination "/nas/TV Shows Optimized" \
  --vc h264 \
  --ac aac \
  --vb 4000 \
  --ab 128 \
  --one-audio-track \
  --audio-lang fre \
  --max-width 1920 \
  --max-height 1080 \
  --gpu amd \
  --verbose
```

**Résultat :**
- Garde uniquement la piste française (si elle existe)
- Si pas de piste FR → garde la piste par défaut
- Utilise l'encodeur AMD AMF (h264_amf)
- Mode verbeux actif

---

### 3. **Compression agressive - Tablette**

```bash
./transcode_library.py \
  --source /media/Backup \
  --destination /media/Tablet \
  --vc h264 \
  --ac aac \
  --vb 2500 \
  --ab 96 \
  --max-width 1280 \
  --max-height 720 \
  --audio-channels 2 \
  --one-audio-track \
  --force-cbr \
  --quality low
```

**Résultat :**
- Vidéos limitées à 720p
- Audio downmix en stéréo (2 canaux max)
- CBR pour compatibilité maximale
- Encodage rapide (preset `low`)

---

### 4. **Archivage haute qualité - NVIDIA GPU**

```bash
./transcode_library.py \
  --source /media/Raw \
  --destination /media/Archive \
  --vc hevc \
  --ac opus \
  --vb 8000 \
  --ab 256 \
  --max-width 3840 \
  --max-height 2160 \
  --quality high \
  --gpu nvidia \
  --force-codec-video \
  --force-codec-audio
```

**Résultat :**
- Tout converti en HEVC/Opus (même si déjà H.264/AAC)
- Support 4K (3840x2160)
- Utilise NVENC (hevc_nvenc)
- Preset qualité élevée

---

### 5. **Mode dry-run - Test avant exécution**

```bash
./transcode_library.py \
  --source /media/Test \
  --destination /media/Test_out \
  --vc h264 \
  --ac aac \
  --vb 5000 \
  --ab 192 \
  --dry-run \
  --verbose
```

**Résultat :**
- Analyse tous les fichiers
- Affiche les décisions sans transcoder
- Parfait pour tester les paramètres

---

### 6. **Synchronisation avec propagation**

```bash
./transcode_library.py \
  --source /nas/Master \
  --destination /nas/Optimized \
  --vc h264 \
  --ac aac \
  --vb 6000 \
  --ab 192 \
  --propagate
```

**Résultat :**
- Transcode les nouveaux fichiers
- **Supprime** les fichiers destination qui n'existent plus dans source
- Maintient une copie miroir optimisée

---

## 🔍 Scénarios de transcodage

### Logique de décision

Le script utilise une **logique à 3 niveaux** :

```
Niveau 0: Faut-il traiter le fichier ?
    ↓
Niveau 1: Faut-il transcoder la VIDÉO ?
    ↓
Niveau 2: Faut-il transcoder l'AUDIO ?
    ↓
Niveau 3: Faut-il RÉENCODER l'audio ou juste remuxer ?
```

### Tableau des scénarios

| # | Source Vidéo | Source Audio | Args | Action | Commande FFmpeg |
|---|--------------|--------------|------|--------|-----------------|
| 1 | H.264 5000kb/s | AAC 192kb/s | `--vb 6000 --ab 192` | ✅ Copie simple | `cp` |
| 2 | H.264 8000kb/s | AAC 192kb/s | `--vb 6000 --ab 192` | 🔄 Vidéo seule | `-c:v h264_amf -c:a copy` |
| 3 | H.264 5000kb/s | AC3 384kb/s | `--vb 6000 --ab 192` | 🔄 Audio seul | `-c:v copy -c:a aac` |
| 4 | H.264 5000kb/s | 2 pistes AAC | `--vb 6000 --ab 192 --one-audio-track` | 🔄 Audio seul (remux) | `-c:v copy -c:a copy -map 0:a:0` |
| 5 | MPEG4 3000kb/s | MP3 128kb/s | `--vb 6000 --ab 192 --vc h264 --ac aac` | 🔄 Les deux | `-c:v libx264 -c:a aac` |
| 6 | AV1 4000kb/s | Opus 192kb/s | `--vb 6000 --skip-codec av1` | ⏭️ Skip | *Fichier ignoré* |
| 7 | H.264 1920x1080 | AAC 192kb/s | `--max-width 1280` | 🔄 Vidéo seule (scale) | `-c:v libx264 -vf scale=1280:-2 -c:a copy` |
| 8 | H.264 5000kb/s | AAC 5.1 | `--audio-channels 2` | 🔄 Audio seul (downmix) | `-c:v copy -c:a aac -ac 2` |
| 9 | HEVC 7000kb/s | AAC 192kb/s | `--vc h264 --vb 6000` (sans force) | ✅ Copie | *HEVC meilleur que H.264* |
| 10 | HEVC 7000kb/s | AAC 192kb/s | `--vc h264 --vb 6000 --force-codec-video` | 🔄 Vidéo seule | `-c:v libx264 -c:a copy` |

### Légende

- ✅ **Copie simple** : Fichier copié tel quel (`shutil.copy2`)
- 🔄 **Vidéo seule** : Transcodage vidéo, audio copié (`-c:a copy`)
- 🔄 **Audio seul** : Vidéo copiée, audio transcodé/remuxé (`-c:v copy`)
- 🔄 **Les deux** : Transcodage complet vidéo + audio
- ⏭️ **Skip** : Fichier ignoré (codec dans `--skip-codec`)

---

## 📊 Hiérarchie des codecs

### Vidéo (du plus lourd au plus léger)

```
AV1          ████████████ (Très lourd - meilleure compression)
HEVC/H.265   ██████████
VP9          ██████████
H.264/AVC    ███████      (Standard actuel)
VP8          █████
VC1/WMV3     ████
MPEG2        ███
MPEG4        ██           (Très léger - faible compression)
```

**Règle :** Le script transcode **uniquement** si le codec source est **plus haut** dans la hiérarchie que le codec cible (sauf avec `--force-codec-video`).

**Exemple :**
```bash
# Source: MPEG4 (rang 8) → Cible: H.264 (rang 3)
# 8 > 3 → MPEG4 moins bon → TRANSCODE ✅

# Source: HEVC (rang 1) → Cible: H.264 (rang 3)
# 1 < 3 → HEVC meilleur → PAS DE TRANSCODE ❌ (sauf --force-codec-video)
```

### Audio (du plus lourd au plus léger)

```
TrueHD/DTS   ████████████ (Très lourd - lossless)
FLAC         ███████████
Opus         ████████
EAC3         ███████
AC3          ██████
AAC          █████        (Standard actuel)
MP3          ████
Vorbis       ███
MP2          ██           (Très léger)
```

---

## 📁 Structure du projet

```
/media/
├── Films/                    # Source
│   ├── Action/
│   │   ├── Film1.mkv
│   │   └── Film2.mp4
│   ├── Comedy/
│   │   └── Film3.avi
│   └── done.txt              # Fichier de suivi
│
└── Films_optimized/          # Destination (créée automatiquement)
    ├── Action/
    │   ├── Film1.mkv         # Transcodé
    │   └── Film2.mp4         # Transcodé
    └── Comedy/
        └── Film3.avi         # Copié si déjà optimal
```

### Fichier `done.txt`

Exemple de contenu :

```
Action/Film1.mkv
Action/Film2.mp4
Comedy/Film3.avi
```

**Comportement :**
- Fichiers listés = déjà traités, sautés
- Fichiers non listés = à traiter
- Si destination manquante mais dans `done.txt` → re-transcodage

---

## 🎬 Exemples de commandes FFmpeg générées

### 1. Transcodage vidéo uniquement

```bash
ffmpeg -hide_banner -loglevel error -stats -y \
  -i "source.mkv" \
  -c:v h264_amf \
  -b:v 6000k -maxrate 6000k -bufsize 36000k \
  -pix_fmt yuv420p \
  -map 0:v:0 \
  -c:a copy \
  -map 0:a:0 \
  -c:s copy \
  -map_metadata 0 -map_metadata:s:v -1 -map_chapters 0 \
  "destination.mkv"
```

### 2. Remuxing audio (suppression de pistes)

```bash
ffmpeg -hide_banner -loglevel error -stats -y \
  -i "source.mkv" \
  -c:v copy \
  -map 0:v:0 \
  -map 0:a:1 \
  -c:a copy \
  -c:s copy \
  -map_metadata 0 -map_metadata:s:v -1 -map_chapters 0 \
  "destination.mkv"
```

### 3. Transcodage audio avec downmix

```bash
ffmpeg -hide_banner -loglevel error -stats -y \
  -i "source.mkv" \
  -c:v copy \
  -map 0:v:0 \
  -map 0:a:0 \
  -c:a aac -b:a 192k -ac 2 \
  -c:s copy \
  -map_metadata 0 -map_metadata:s:v -1 -map_chapters 0 \
  "destination.mkv"
```

### 4. Transcodage complet avec scaling

```bash
ffmpeg -hide_banner -loglevel error -stats -y \
  -i "source.mkv" \
  -c:v libx264 \
  -preset faster \
  -b:v 6000k -maxrate 7200k \
  -vf scale=1920:-2 \
  -pix_fmt yuv420p \
  -map 0:v:0 \
  -map 0:a:0 \
  -c:a aac -b:a 192k \
  -c:s copy \
  -map_metadata 0 -map_metadata:s:v -1 -map_chapters 0 \
  "destination.mkv"
```

---

## 📈 Sorties et logs

### Sortie console (exemple)

```
================================================================================
Processing: Action/Film_4K.mkv
================================================================================
   [INFO] Analyzing: Film_4K.mkv
   [INFO]   → Video: h264 12000kb/s VBR 3840x2160 | Audio: aac 192kb/s
   [INFO]   → Transcode needed (VIDEO ONLY): video bitrate 12000 > 6000 kb/s; width 3840 > 1920
   [INFO] Transcoding (video only, audio copy): Film_4K.mkv → Film_4K.mkv
frame= 5420 fps=87 q=28.0 size=  125440kB time=00:03:00.83 bitrate=5678.2kbits/s speed=3.62x
   [INFO]   → Success

================================================================================
Processing: Comedy/Old_Movie.avi
================================================================================
   [INFO] Analyzing: Old_Movie.avi
   [INFO]   → Video: mpeg4 2500kb/s CBR 720x480 | Audio: mp3 128kb/s
   [INFO]   → Transcode needed (VIDEO + AUDIO): video codec mpeg4 (rank 8) heavier than h264 (rank 3); audio codec mp3 (rank 8) heavier than aac (rank 7)
   [INFO] Transcoding (video+audio): Old_Movie.avi → Old_Movie.avi
frame= 2880 fps=105 q=26.0 size=   42560kB time=00:02:00.00 bitrate=2909.8kbits/s speed=4.38x
   [INFO]   → Success

================================================================================
SUMMARY
================================================================================
Total files:              150
Skipped (done):           50
Skipped (optimal):        30
Transcoded (video+audio): 25
Transcoded (video only):  35
Transcoded (audio only):  10
Failed:                   0
================================================================================
```

### Fichier log (avec `--log transcode.log`)

```
[2025-01-15 14:30:22] INFO:    Starting transcode job
[2025-01-15 14:30:22] INFO:    Source: /media/Films
[2025-01-15 14:30:22] INFO:    Destination: /media/Films_optimized
[2025-01-15 14:30:22] INFO:    Video: h264 @ 6000 kb/s, max 1920x1080
[2025-01-15 14:30:22] INFO:    Audio: aac @ 192 kb/s
[2025-01-15 14:30:23] INFO:    Loaded 50 entries from done.txt
[2025-01-15 14:30:23] INFO:    Found 150 video files in source
[2025-01-15 14:30:25] INFO:    Analyzing: Film_4K.mkv
[2025-01-15 14:30:25] INFO:      → Video: h264 12000kb/s VBR 3840x2160 | Audio: aac 192kb/s
[2025-01-15 14:30:25] INFO:      → Transcode needed (VIDEO ONLY): video bitrate 12000 > 6000 kb/s
[2025-01-15 14:30:25] INFO:    Transcoding (video only, audio copy): Film_4K.mkv → Film_4K.mkv
[2025-01-15 14:33:42] INFO:      → Success
```

---

## 🐛 Dépannage

### Problème : "ffprobe failed"

**Cause :** FFmpeg/FFprobe non installé ou fichier corrompu

**Solution :**
```bash
# Vérifier installation
ffprobe -version

# Tester manuellement
ffprobe -v error -show_format "fichier.mkv"
```

---

### Problème : "Encoder not found"

**Cause :** Encodeur GPU non disponible

**Solution :**
```bash
# Lister encodeurs disponibles
ffmpeg -encoders | grep nvenc  # NVIDIA
ffmpeg -encoders | grep amf    # AMD

# Utiliser --gpu none si pas de support GPU
```

---

### Problème : Transcodages inutiles

**Cause :** Paramètres trop restrictifs

**Solution :**
```bash
# Utiliser dry-run pour tester
./transcode_library.py --dry-run --verbose ...

# Vérifier hiérarchie des codecs
# Ex: HEVC source → H.264 cible ne transcode PAS (sauf --force-codec-video)
```

---

### Problème : Fichiers ignorés (AV1)

**Cause :** `--skip-codec av1` par défaut

**Solution :**
```bash
# Retirer le skip (attention, AV1 est très lourd à décoder)
./transcode_library.py --skip-codec ""  # Vide = aucun skip

# Ou forcer sans argument (selon implémentation)
```

---

## 📊 Performances

### Vitesses approximatives (GPU AMD RX 6600)

| Type | Résolution | Codec | FPS moyen | Ratio temps réel |
|------|------------|-------|-----------|------------------|
| Vidéo only | 1080p | H.264 | 150-200 | 6-8x |
| Vidéo only | 1080p | HEVC | 80-120 | 3-5x |
| Audio only | N/A | AAC | 2000+ | 80x+ |
| Complet | 1080p | H.264+AAC | 140-180 | 5-7x |

### Vitesses approximatives (CPU - i7-10700K)

| Type | Résolution | Codec | FPS moyen | Ratio temps réel |
|------|------------|-------|-----------|------------------|
| Vidéo only | 1080p | H.264 (medium) | 60-80 | 2-3x |
| Vidéo only | 1080p | H.264 (slow) | 30-40 | 1-1.5x |
| Audio only | N/A | AAC | 2000+ | 80x+ |

**Note :** Les performances varient selon la complexité de la vidéo et la charge système.

---

## 🔒 Bonnes pratiques

### 1. Toujours tester avec dry-run

```bash
./transcode_library.py --dry-run --verbose ... | tee test.log
```

### 2. Faire des backups

```bash
# Copier done.txt avant traitement massif
cp /media/Films/done.txt /media/Films/done.txt.backup
```

### 3. Traiter par petits lots

```bash
# Traiter un sous-dossier d'abord
./transcode_library.py \
  --source "/media/Films/Action" \
  --destination "/media/Films_opt/Action" \
  ...
```

### 4. Surveiller l'espace disque

```bash
# Vérifier avant de lancer
df -h /media/destination

# Destination ≈ 60-80% de la taille source (selon paramètres)
```

### 5. Utiliser des logs

```bash
./transcode_library.py \
  --log "transcode_$(date +%Y%m%d).log" \
  --error-log "errors_$(date +%Y%m%d).log" \
  ...
```

---

## 🎓 Cas d'usage avancés

### 1. Pipeline de transcodage progressif

```bash
# Étape 1: Identifier les gros fichiers
./transcode_library.py --dry-run --vb 8000 ... > big_files.log

# Étape 2: Transcoder seulement ceux-ci
./transcode_library.py --vb 8000 ...

# Étape 3: Deuxième passe plus agressive
./transcode_library.py --vb 6000 ...
```

### 2. Migration de codec

```bash
# Convertir toute une bibliothèque H.264 → HEVC
./transcode_library.py \
  --source /media/H264_lib \
  --destination /media/HEVC_lib \
  --vc hevc \
  --ac aac \
  --vb 4000 \
  --ab 192 \
  --force-codec-video \
  --gpu nvidia \
  --quality high
```

### 3. Optimisation multi-appareil

```bash
# Serveur → Version tablette
./transcode_library.py \
  --source /nas/Master \
  --destination /nas/Mobile \
  --vc h264 --ac aac \
  --vb 2000 --ab 96 \
  --max-width 1280 --max-height 720 \
  --one-audio-track --audio-channels 2

# Serveur → Version TV 4K
./transcode_library.py \
  --source /nas/Master \
  --destination /nas/TV_4K \
  --vc hevc --ac aac \
  --vb 15000 --ab 256 \
  --max-width 3840 --max-height 2160 \
  --gpu nvidia
```

---

## 📝 Notes techniques

### VBR vs CBR

- **VBR (Variable Bitrate)** : Débit variable selon la complexité
  - ✅ Meilleure qualité/taille
  - ❌ Moins compatible (certains lecteurs)
  
- **CBR (Constant Bitrate)** : Débit fixe
  - ✅ Compatibilité maximale
  - ❌ Fichiers plus gros

**Utiliser `--force-cbr`** pour les appareils exigeants (lecteurs DVD, anciennes TV).

### Détection CBR/VBR

Le script analyse les 100 premiers packets vidéo :
- Variation < 10% → CBR
- Variation ≥ 10% → VBR

### Bitrate vs Résolution

Recommandations générales (H.264, 24-30 fps) :

| Résolution | Bitrate recommandé | Usage |
|------------|-------------------|-------|
| 480p | 1500-2500 kb/s | Mobile, faible bande passante |
| 720p | 2500-4000 kb/s | Tablettes, streaming standard |
| 1080p | 5000-8000 kb/s | TV Full HD, qualité standard |
| 1080p HQ | 8000-12000 kb/s | Archivage, cinéma |
| 4K | 15000-25000 kb/s | TV 4K, qualité maximale |

---

## 📞 Support et contribution

### Problèmes connus

1. **Fichiers avec sous-titres graphiques (PGS)** : Copiés tels quels, non convertis
2. **Métadonnées HDR** : Non préservées en transcodage H.264
3. **Audio multi-objets (Atmos)** : Downmix en canaux classiques

### Améliorations futures

- [ ] Support HDR10/Dolby Vision
- [ ] Détection automatique du meilleur preset GPU
- [ ] Interface web de monitoring
- [ ] Support matroska tags avancés
- [ ] Parallélisation (plusieurs fichiers simultanés)

---

## 📄 Licence

Ce script est fourni "tel quel" sans garantie. Utilisez-le à vos risques et périls. Testez toujours avec `--dry-run` avant un traitement massif.

---

## 🙏 Remerciements

- **FFmpeg team** pour l'outil extraordinaire
- **MediaInfo** pour l'inspiration sur la détection de format
- Claude.ai et ChatGPT :)

---

*Documentation générée le 2025-01-15 - Version 2.0*