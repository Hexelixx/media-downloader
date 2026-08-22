# Boîte à outils média

Application de bureau Windows regroupant 7 outils de téléchargement, compression et
conversion de fichiers vidéo, audio et image — en un seul exécutable, sans installation.

Construite sur [yt-dlp](https://github.com/yt-dlp/yt-dlp), [FFmpeg](https://ffmpeg.org/),
[Deno](https://deno.com/), [Pillow](https://python-pillow.org/),
[PyMuPDF](https://pymupdf.readthedocs.io/), [python-docx](https://python-docx.readthedocs.io/)
et [reportlab](https://www.reportlab.com/), avec une interface
[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter).

## Télécharger et installer

1. Aller sur la page [**Releases**](https://github.com/Hexelixx/media-downloader/releases/latest).
2. Télécharger **`MediaDownloader-complet.zip`**.
3. Décompresser le zip où vous voulez (Bureau, Documents...).
4. Lancer `MediaDownloader.exe`.

Aucune installation, aucun compte, aucune dépendance à installer séparément — tout le
nécessaire (FFmpeg, Deno) est fourni dans le dossier `bin\` à côté de l'exécutable.
Fonctionne sur Windows 10/11 (64 bits).

> Le fichier `MediaDownloader.exe` proposé séparément sur la page Releases n'est **pas**
> destiné à un premier téléchargement : c'est celui que l'application va chercher
> elle-même via son bouton de mise à jour intégré (voir plus bas).

## Les 7 outils

1. **Vidéos (YouTube, TikTok...)** — télécharge une vidéo ou une playlist en MP4 ou MP3.
   Basé sur yt-dlp, qui supporte nativement environ 1800 sites (YouTube, TikTok,
   Instagram, Twitter/X, Facebook, Vimeo, Twitch, Reddit, Dailymotion, SoundCloud...).
   - Métadonnées optionnelles : titre, artiste, album, pochette embarquée, et numéro de
     piste (utile pour les lecteurs MP3 qui trient les albums par position).
   - Téléchargement de playlist entière en une fois, avec reprise automatique : si une
     piste échoue, les suivantes continuent et un nouvel essai ne retélécharge que ce
     qui manque.
   - Retente automatiquement jusqu'à 3 fois en cas d'échec réseau ponctuel (protections
     anti-bot fréquentes sur TikTok notamment).
   - Contourne automatiquement les liens Vimeo classiques (`vimeo.com/<id>`), actuellement
     cassés côté Vimeo, en basculant sur l'URL équivalente `player.vimeo.com/video/<id>`.
   - Repli automatique sur la meilleure qualité disponible si la résolution demandée
     n'existe pas (fréquent sur Instagram Reels).
   - Préfère l'audio AAC à l'Opus pour les MP4 : Opus dans un conteneur MP4 ne se lit pas
     dans tous les lecteurs, contrairement à l'AAC.
2. **Compresseur d'images** — réduit le poids d'une ou plusieurs images.
   - Mode qualité manuelle (préréglages par plateforme) ou mode taille cible (poids
     maximum voulu, qualité/dimension ajustées automatiquement).
   - Métadonnées EXIF (date, appareil, position GPS) toujours préservées quand le format
     de sortie le permet.
   - Accepte les formats RAW d'appareil photo (.cr2, .cr3, .nef, .arw, .dng, .orf, .rw2,
     .raf, .pef, .srw) et HEIC/HEIF d'iPhone en entrée.
3. **Vidéo → Audio** — extrait la piste audio de fichiers vidéo locaux, vers MP3, AAC,
   OGG (compressés, bitrate réglable) ou WAV/FLAC (sans perte).
4. **Compresseur de vidéo** — réduit le poids d'une vidéo (H.264, qualité pilotée par
   CRF), avec préréglages par plateforme et redimensionnement optionnel.
5. **Créateur de GIF** — transforme un extrait de vidéo en GIF animé (palette optimisée
   sur deux passes ffmpeg, pour un bien meilleur rendu couleur qu'une conversion directe),
   avec aperçu animé avant export.
6. **Convertisseur de texte** — convertit entre `.txt`, `.md`, `.html`, `.docx` et `.pdf`
   dans n'importe quel sens. Accepte aussi les anciens `.doc` (Word 97-2003) en entrée via
   Microsoft Word, avec préservation de la mise en page pour les sorties `.docx`/`.pdf`.
7. **Convertisseur d'images** — convertit entre PNG, JPEG, WEBP, BMP, GIF, TIFF, ICO,
   image(s) → PDF et PDF → images. Accepte aussi les formats RAW et HEIC/HEIF en entrée.

Chaque outil traite ses fichiers dans un sous-dossier dédié sous
`Téléchargements\MediaDownloader\` (modifiable dans chaque onglet). Sur les 6 onglets de
conversion/compression, le glisser-déposer de fichiers depuis l'Explorateur Windows est
supporté en plus du bouton « Parcourir... ».

## Interface

- **Menu latéral rétractable** (bouton ☰) plutôt que des onglets en haut, pour garder
  chaque outil visible d'un coup d'œil.
- **Thème** Système / Clair / Sombre, appliqué immédiatement.
- **Langue** Français / English pour toute l'interface. Le changement redémarre
  l'application (nécessaire pour réafficher les textes déjà construits) et le choix est
  mémorisé pour les lancements suivants.
- **Mise à jour intégrée** : le bouton *Vérifier les mises à jour*, en bas du menu,
  compare la version installée à la dernière publiée sur GitHub et propose de
  l'installer en un clic — téléchargement, remplacement de l'exécutable et relance
  automatiques, sans rien à faire manuellement. Aucune vérification n'a lieu sans action
  explicite de l'utilisateur.
- **Défilement automatique** dans chaque outil si le contenu dépasse la taille de la
  fenêtre : rien n'est jamais coupé ou inaccessible.

## Développement

### Prérequis

- Python 3.12
- [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) et [Deno](https://deno.com/) (pour
  reconstituer le dossier `bin\`, voir plus bas)
- `git` et [GitHub CLI (`gh`)](https://cli.github.com/) pour publier des releases

### Installer l'environnement

```powershell
git clone https://github.com/Hexelixx/media-downloader.git
cd media-downloader
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
```

### Lancer depuis les sources

```powershell
.\.venv\Scripts\python.exe app.py
```

### Reconstruire l'exécutable

```powershell
.\.venv\Scripts\pyinstaller.exe --noconfirm MediaDownloader.spec
```

Le nouvel exécutable apparaît dans `dist\MediaDownloader.exe`.

> **Toujours passer par le fichier `.spec`**, jamais par `--onefile --windowed app.py`
> directement : la ligne de commande écrase le `.spec` et perd au passage les
> `hiddenimports` (`lxml.etree`, `lxml._elementpath`) que PyInstaller ne détecte pas tout
> seul — le convertisseur de texte plante alors à l'ouverture d'un `.docx`, mais
> seulement une fois packagé, jamais en développement.

### Dossier `bin\` (binaires embarqués)

`dist\bin\` doit contenir `ffmpeg.exe`, `ffprobe.exe` et `deno.exe`, copiés depuis une
installation locale, pour que l'exécutable fonctionne sur une autre machine sans rien
installer :

```powershell
Copy-Item "<chemin vers ffmpeg.exe>"  ".\dist\bin\ffmpeg.exe"  -Force
Copy-Item "<chemin vers ffprobe.exe>" ".\dist\bin\ffprobe.exe" -Force
Copy-Item "<chemin vers deno.exe>"    ".\dist\bin\deno.exe"    -Force
```

### Publier une nouvelle version

Le bouton *Vérifier les mises à jour* de l'application compare sa version au dernier tag
publié dans les [Releases GitHub](https://github.com/Hexelixx/media-downloader/releases).
Publier une version, c'est donc créer une release avec l'exécutable en pièce jointe —
entièrement automatisé par `release.ps1` :

```powershell
.\release.ps1 -Notes "Ce qui a changé, en une phrase"
```

Le script, en s'arrêtant à la première erreur :

1. vérifie que `git`/`gh` sont disponibles et que le dépôt n'a pas de modification non
   commitée ;
2. calcule le nouveau numéro de version (la date du jour, voir ci-dessous) ;
3. réécrit `APP_VERSION` dans `common.py` ;
4. reconstruit `dist\MediaDownloader.exe` ;
5. commit, tague `vAAAA.MM.JJ`, pousse sur GitHub ;
6. crée la release avec l'exécutable seul (pour le mécanisme de mise à jour) et un zip
   complet exe + `bin\` (pour un premier téléchargement autonome).

| Option | Effet |
| --- | --- |
| `-DryRun` | Affiche tout ce qui serait fait, sans rien modifier ni publier. |
| `-Version 2026.12.25` | Force un numéro précis au lieu de la date du jour. |
| `-SkipBuild` | Réutilise `dist\MediaDownloader.exe` tel quel. |
| `-Notes "..."` | Notes de version, affichées sur GitHub et dans l'application. |

**Schéma de version** : `AAAA.MM.JJ`, avec un suffixe `.2`, `.3`... si plusieurs versions
sortent le même jour (même schéma que yt-dlp, la dépendance principale du projet — pas de
notion pertinente de « majeur/mineur/correctif » pour un outil livré en bloc sans API
publique). Le numéro de version n'existe qu'à un seul endroit dans le code, la constante
`APP_VERSION` dans `common.py` : `release.ps1` la réécrit, l'interface l'affiche, et le
mécanisme de mise à jour la compare — jamais de duplication à maintenir en synchronisation.

**En cas de pépin** :
- *La release est publiée mais l'application ne la voit pas* — vérifier que le tag est
  bien de la forme `v2026.08.15` (un tag `latest` ou `v1.2.3-beta` est ignoré
  volontairement) et que `MediaDownloader.exe` est bien attaché à la release.
- *`gh` demande une authentification* — `gh auth login`, une fois, puis relancer.
- *Le script refuse de partir (« modifications non commitées »)* — comportement voulu,
  pour ne jamais publier un exécutable construit à partir de code non relu.

## Limites connues

- **Spotify n'est pas supporté** : Spotify chiffre son flux (DRM), il n'existe pas de
  moyen légitime de télécharger directement depuis un lien Spotify.
- YouTube fait évoluer régulièrement ses protections anti-bot ; des erreurs
  `403 Forbidden` persistantes malgré Deno indiquent généralement qu'il faut mettre à
  jour `yt-dlp` (`pip install --upgrade yt-dlp`, puis reconstruire l'exécutable).
- Le téléchargement de contenu protégé par le droit d'auteur sans autorisation peut
  violer les conditions d'utilisation des plateformes et la loi selon la juridiction et
  l'usage.
