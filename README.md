# Boîte à outils média (Felix)

Appli desktop Windows à onglets, construite sur [yt-dlp](https://github.com/yt-dlp/yt-dlp)
+ [FFmpeg](https://ffmpeg.org/) + [Deno](https://deno.com/) (déchiffrement YouTube) +
[Pillow](https://python-pillow.org/) + [PyMuPDF](https://pymupdf.readthedocs.io/) +
[python-docx](https://python-docx.readthedocs.io/) + [reportlab](https://www.reportlab.com/),
avec une interface [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter).

## Interface

Menu latéral à gauche (au lieu d'onglets en haut) : clique sur le bouton **☰** en haut pour
replier/déplier la liste des outils et récupérer de la place. En bas du menu (visible
seulement déplié), sélecteur **Système / Clair / Sombre** pour choisir le thème — "Système"
suit automatiquement le réglage clair/sombre de Windows, les deux autres forcent un thème
fixe. Chaque outil garde son état (fichiers sélectionnés, réglages, opération en cours...)
même quand on navigue ailleurs et qu'on revient.

Juste en dessous, sélecteur **Français / English** pour la langue de toute l'interface —
noms des outils, boutons, messages du journal et fenêtres d'alerte compris. Au changement,
**l'appli se relance toute seule** (nécessaire : les textes déjà affichés ne peuvent pas
être réécrits à chaud) et rouvre directement dans la nouvelle langue ; il n'y a rien à
confirmer. Le choix est mémorisé dans un petit fichier `settings.json` créé à côté de
l'exe, donc l'appli redémarre toujours dans la dernière langue choisie. Au tout premier
lancement, elle est en français. Les dossiers de destination proposés par défaut suivent la
langue (`Images compressées` en français, `Compressed images` en anglais) : si tu changes
de langue après avoir déjà traité des fichiers, pense à vérifier le dossier de destination
affiché dans l'onglet.

Tout en bas du menu, le bouton **Vérifier les mises à jour** interroge GitHub pour voir
s'il existe une version plus récente que celle installée — dont le numéro est affiché en
gris juste en dessous du bouton (`v2026.08.15`, au format date, voir
[Schéma de version](#schéma-de-version)). Trois cas possibles :

- **Déjà à jour** : un message le dit, il n'y a rien à faire.
- **Nouvelle version disponible** : l'appli affiche l'ancien et le nouveau numéro (plus
  les nouveautés, si la version publiée en mentionne) et demande confirmation. Si tu
  acceptes, elle **télécharge la nouvelle version avec une barre de progression**
  (annulable à tout moment), puis **se ferme, s'installe et se rouvre toute seule** —
  il n'y a rien à télécharger à la main, ni de `.zip` à décompresser. L'opération ne
  remplace que `MediaDownloader.exe` : le dossier `bin\` et le fichier `settings.json`
  (ta langue) restent en place.
- **Pas de connexion, GitHub injoignable ou trop de vérifications d'affilée** : un
  message clair l'explique, l'appli continue de fonctionner normalement.

La vérification se fait **en tâche de fond** : la fenêtre ne se fige jamais, même si
GitHub met du temps à répondre. Aucun compte ni mot de passe n'est nécessaire (le dépôt
est public) et **l'appli ne vérifie rien toute seule** — rien ne part sur le réseau tant
que tu n'as pas cliqué sur le bouton.

Le contenu de chaque outil défile verticalement (comme une page web) si la fenêtre est
redimensionnée en plus petit : rien n'est jamais coupé/inaccessible, même quand le journal
ou l'aperçu d'un GIF prend beaucoup de place. Une barre de défilement apparaît
automatiquement seulement quand c'est nécessaire.

## Les 7 outils

1. **Vidéos (YouTube, TikTok...)** : télécharge une vidéo/playlist en MP4 ou MP3, avec
   métadonnées (titre/artiste/album) + pochette embarquée en option. Basé sur yt-dlp, qui
   supporte nativement ~1800 sites (YouTube, TikTok, Instagram, Twitter/X, Facebook, Vimeo,
   Twitch, Reddit, Dailymotion, SoundCloud...), pas seulement YouTube/TikTok.
   1. Double-clique sur `dist\MediaDownloader.exe`.
   2. Colle un lien vidéo.
   3. Choisis **MP4** ou **MP3**, la qualité, et coche **« Playlist entière »**
      si le lien est une playlist/liste que tu veux télécharger en entier.
   4. Coche **« Titre / artiste / album + pochette »** pour intégrer ces infos dans le
      fichier (tags ID3 pour MP3, tags MP4 pour les vidéos, pochette embarquée dans les
      deux cas). L'artiste est repris de la chaîne si la vidéo n'a pas d'artiste explicite ;
      l'album n'est renseigné que si la source en fournit un (rare hors YouTube Music).
   5. Choisis le dossier de destination (par défaut `Téléchargements\MediaDownloader`).
   6. Clique **Télécharger**. La barre de progression et le journal en bas
      affichent l'avancement ; **Annuler** interrompt proprement.
   - En cas d'échec (protections anti-bot de certains sites, TikTok en particulier, qui
     varient d'une requête à l'autre), l'appli **réessaie automatiquement jusqu'à 3 fois**
     avant d'abandonner — ça suffit à résoudre la grande majorité des échecs ponctuels.
   - **Vimeo** : les liens `vimeo.com/<id>` classiques échouent actuellement de façon
     systématique côté Vimeo (identifiants OAuth "anonymes" révoqués, problème externe,
     pas un bug de l'appli). L'appli bascule automatiquement sur l'URL équivalente
     `player.vimeo.com/video/<id>`, qui contourne le souci.
   - Si aucune résolution disponible ne correspond au palier de qualité demandé (fréquent
     sur Instagram Reels, qui n'offrent parfois que des résolutions non standards comme
     1280p/1920p), l'appli prend automatiquement la meilleure qualité disponible plutôt
     que d'échouer.
   - **MP4 sans son** : YouTube sert souvent de l'audio Opus comme piste "meilleure qualité",
     mais Opus fusionné dans un conteneur `.mp4` ne se lit pas dans beaucoup de lecteurs
     (vidéo silencieuse). L'appli **préfère automatiquement l'AAC** (nativement compatible
     MP4) quand les deux sont disponibles, sans exclure Opus si c'est la seule option.
2. **Compresseur d'images** : réduit le poids d'une ou plusieurs images.
   - **Mode Qualité manuelle** : préréglages par plateforme (réseaux sociaux, web, email,
     avatar) ou réglage libre qualité/dimension.
   - **Mode Taille cible** : indique le poids maximum voulu (ex. 500 Ko) et l'outil cherche
     automatiquement la qualité (puis, si besoin, réduit aussi la dimension) pour l'atteindre
     sans le dépasser.
   - Les **métadonnées EXIF** (date, appareil, position GPS...) sont **toujours préservées**
     dans les deux modes, quand le format de sortie le permet (JPEG/WEBP/PNG/TIFF).
   - Accepte aussi les photos **RAW d'appareil photo** (.cr2, .cr3, .nef, .arw, .dng, .orf,
     .rw2, .raf, .pef, .srw) et **HEIC/HEIF d'iPhone** en entrée.
3. **Vidéo → Audio** : extrait la piste audio d'un ou plusieurs fichiers vidéo **locaux**
   déjà sur le disque (rien à voir avec le téléchargement de l'onglet 1), au choix en
   **MP3, AAC (.m4a), OGG** (compressés, bitrate réglable) ou **WAV/FLAC** (sans perte).
4. **Compresseur de vidéo** : réduit le poids d'une vidéo (H.264, qualité pilotée par CRF)
   avec des préréglages par plateforme, redimensionnement optionnel (jamais d'agrandissement).
5. **Créateur de GIF** : transforme un extrait de vidéo (départ + durée réglables) en GIF
   animé, avec choix des images/seconde et de la largeur. Utilise la technique ffmpeg en
   deux passes (palette optimisée puis appliquée) pour un bien meilleur rendu couleur
   qu'une conversion directe. Bouton **« Générer l'aperçu »** : montre directement dans
   l'appli, en animé, l'extrait exact (mêmes départ/durée/fps) qui sera créé — en résolution
   réduite pour que ce soit rapide, mais avec le même contenu que le GIF final.
6. **Convertisseur de texte** : convertit entre `.txt`, `.md`, `.html`, `.docx` **et `.pdf`**,
   dans n'importe quel sens (y compris `.pdf` en source comme en destination). Accepte aussi
   les anciens fichiers `.doc` (Word 97-2003) en entrée — via Microsoft Word installé sur
   la machine (aucune bibliothèque Python ne sait lire ce format binaire). Pour `.doc` →
   `.docx` et `.doc` → `.pdf` spécifiquement, la conversion passe par l'enregistrement
   natif de Word (pas par le pivot texte générique), ce qui **préserve la mise en page**
   (polices, gras/italique, tableaux, styles de titres...) au lieu de tout réduire à du
   texte brut. Les autres formats en sortie depuis `.doc` (`.txt`/`.md`/`.html`) perdent
   la mise en page par nature (ce sont des formats texte sans mise en forme). Sans Word
   installé, un message clair l'indique au lieu d'échouer silencieusement.
7. **Convertisseur d'images** : convertit entre PNG/JPEG/WEBP/BMP/GIF/TIFF/ICO, plus
   image(s) → PDF et PDF → images (par page). Accepte aussi les RAW d'appareil photo et
   les HEIC/HEIF d'iPhone en entrée, vers n'importe quel format de sortie.

Chaque outil traite ses fichiers dans un sous-dossier dédié sous
`Téléchargements\MediaDownloader\` (modifiable dans chaque onglet).

Sur les 6 onglets de conversion/compression (pas celui vidéos en ligne, qui prend un lien),
tu peux **glisser-déposer directement des fichiers depuis l'Explorateur Windows** sur la
zone "Fichiers à..." au lieu de passer par le bouton "Parcourir..." — les deux méthodes
sont équivalentes et remplacent la sélection précédente.

## Relancer depuis le code source (sans passer par l'exe)

```powershell
cd media-downloader
.\.venv\Scripts\python.exe app.py
```

## Reconstruire l'exécutable après une modification

```powershell
cd media-downloader
.\.venv\Scripts\pyinstaller.exe --noconfirm MediaDownloader.spec
```
Le nouvel exe apparaît dans `dist\MediaDownloader.exe`.

> **Toujours passer par le fichier `.spec`**, jamais par `--onefile --windowed app.py`
> directement : la ligne de commande **écrase** le `.spec` et perd au passage les
> `hiddenimports` (`lxml.etree`, `lxml._elementpath`) que PyInstaller ne détecte pas tout
> seul — le convertisseur de texte plante alors à l'ouverture d'un `.docx`, mais
> seulement dans l'exe, jamais en dev, ce qui est particulièrement pénible à diagnostiquer.

## Publier une nouvelle version

Le bouton **Vérifier les mises à jour** de l'appli compare sa propre version au dernier
tag publié dans les [Releases GitHub](https://github.com/Hexelixx/media-downloader/releases)
du dépôt. Publier une version, c'est donc créer une release avec l'exe en pièce jointe.

Tout est automatisé par `release.ps1` :

```powershell
cd media-downloader
.\release.ps1 -Notes "Ce qui a changé, en une phrase"
```

Le script enchaîne, en s'arrêtant à la première erreur :

1. vérifie que `git`/`gh` sont là et que le dépôt n'a pas de modification non commitée ;
2. calcule le nouveau numéro de version (la **date du jour**, voir plus bas) ;
3. réécrit `APP_VERSION` dans `common.py` ;
4. reconstruit `dist\MediaDownloader.exe` ;
5. commit + tag `vAAAA.MM.JJ` + push ;
6. crée la release GitHub avec l'exe attaché.

Options utiles :

| Option | Effet |
| --- | --- |
| `-DryRun` | Affiche tout ce qui serait fait, sans rien modifier ni publier. À lancer en premier en cas de doute. |
| `-Version 2026.12.25` | Force un numéro précis au lieu de la date du jour. |
| `-SkipBuild` | Réutilise `dist\MediaDownloader.exe` tel quel (uniquement si tu viens de le construire). |
| `-Notes "..."` | Notes de version, affichées sur GitHub **et** dans la boîte de dialogue de mise à jour. |

### Schéma de version

Versionnage **par date** : `AAAA.MM.JJ` (`2026.08.15`), plus un suffixe `.2`, `.3`... si
plusieurs versions sortent le même jour. Le tag git correspondant est préfixé d'un `v`
(`v2026.08.15`).

Pourquoi la date plutôt que du `1.4.2` : cette appli est livrée en bloc, il n'y a pas
d'API publique dont on casserait la compatibilité, donc « majeur / mineur / correctif »
ne veut rien dire ici. Une date répond en revanche à la seule question qu'on se pose
vraiment devant l'exe : *il date de quand, celui-là ?* C'est aussi exactement le schéma
utilisé par yt-dlp, la dépendance principale du projet.

Le numéro de version n'existe **qu'à un seul endroit** : la constante `APP_VERSION` en
haut de `common.py`. Ne l'écris jamais en dur ailleurs — `release.ps1` réécrit cette
ligne, l'interface l'affiche et le mécanisme de mise à jour la compare. Le tag GitHub et
`APP_VERSION` restent ainsi cohérents par construction.

### En cas de pépin

- **La release est publiée mais l'appli ne la voit pas** : vérifie que le tag est bien de
  la forme `v2026.08.15` (un tag `latest`, `release-3` ou `v1.2.3-beta` est ignoré,
  volontairement) et que l'exe est bien attaché à la release.
- **`gh` demande une authentification** : `gh auth login` une fois, puis relance.
- **Le script refuse de partir** (« modifications non commitées ») : c'est voulu, ça évite
  de publier un exe construit à partir de code non relu. Commit d'abord.

## Dépendances installées sur cette machine

- Python 3.12 (winget: `Python.Python.3.12`)
- FFmpeg (winget: `Gyan.FFmpeg`)
- Deno (winget: `DenoLand.Deno`) — requis par yt-dlp pour déchiffrer les vidéos YouTube ;
  sans lui, les téléchargements YouTube peuvent échouer avec une erreur `HTTP 403 Forbidden`
- Environnement virtuel `.venv` avec `yt-dlp`, `customtkinter`, `pyinstaller`, `Pillow`,
  `pymupdf`, `python-docx`, `markdown`, `html2text`, `reportlab`, `tkinterdnd2`, `pywin32`,
  `rawpy`, `pillow-heif`, `curl_cffi` (0.10-0.15.x précisément -- une version plus récente
  casse le contournement anti-bot de yt-dlp pour certains sites comme Dailymotion)

## Binaires embarqués (dossier `bin\`)

`dist\bin\` contient `ffmpeg.exe`, `ffprobe.exe` et `deno.exe` — copiés depuis
l'installation locale pour que l'exe fonctionne sur une autre machine **sans rien
installer**. Ce dossier doit toujours rester à côté de `MediaDownloader.exe`.
Pour le régénérer après une mise à jour de ces outils :
```powershell
Copy-Item "<chemin vers ffmpeg.exe>" ".\bin\ffmpeg.exe" -Force
Copy-Item "<chemin vers ffprobe.exe>" ".\bin\ffprobe.exe" -Force
Copy-Item "<chemin vers deno.exe>" ".\bin\deno.exe" -Force
```

## Limites connues

- **Spotify n'est pas supporté** : Spotify chiffre son flux (DRM), il n'existe pas
  de moyen légitime de télécharger directement depuis un lien Spotify comme on le
  fait avec YouTube/TikTok. Si tu veux ça plus tard, l'approche standard (via l'outil
  `spotDL`) est de lire les métadonnées du morceau via l'API Spotify puis de
  chercher et télécharger l'équivalent sur YouTube — dis-le-moi si tu veux que
  je l'ajoute comme onglet séparé.
- YouTube fait évoluer régulièrement ses protections anti-bot ; si des erreurs
  `403 Forbidden` réapparaissent malgré Deno, il faudra probablement mettre à jour
  `yt-dlp` (`pip install --upgrade yt-dlp` puis reconstruire l'exe).
- Le téléchargement de contenu protégé par le droit d'auteur sans autorisation
  peut violer les CGU de YouTube/TikTok et la loi selon ta juridiction et ton
  usage (contenu perso, CC/domaine public, ou usage strictement privé où
  toléré = OK ; redistribuer du contenu d'autrui = à éviter).
