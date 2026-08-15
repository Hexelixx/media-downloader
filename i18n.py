"""
Traductions de l'interface (français / anglais) + persistance de la langue choisie.

Ce module est volontairement AUTONOME : il n'importe rien du reste du projet (pas même
common.py). Raison : common.py et les 7 onglets ont besoin de t() dès leur import, donc
une dépendance i18n -> common créerait un import circulaire. La petite duplication de
app_dir() ci-dessous est le prix (assumé) de cette indépendance.

La langue est lue sur disque AU MOMENT DE L'IMPORT de ce module, et pas via un init()
appelé depuis app.py. C'est important : plusieurs modules d'outils calculent des
constantes traduites au niveau module (listes de préréglages, filtres de fichiers...),
donc la langue doit déjà être connue au moment où Python exécute leur corps.
"""

import json
import os
import sys

# Langue de repli : toute clé absente de l'anglais retombe sur le français, qui fait
# donc office de dictionnaire de référence (toujours complet).
DEFAULT_LANGUAGE = "fr"
AVAILABLE_LANGUAGES = ("fr", "en")

# Libellés du sélecteur de langue : volontairement écrits dans leur propre langue
# (convention universelle -- on ne traduit pas "English" en "Anglais" dans un
# sélecteur, sinon un anglophone perdu dans une UI française ne le retrouve pas).
LANGUAGE_LABELS = {"fr": "Français", "en": "English"}
LABEL_TO_LANGUAGE = {label: code for code, label in LANGUAGE_LABELS.items()}

CONFIG_FILENAME = "settings.json"


def app_dir():
    """Dossier contenant l'exe (une fois packagé) ou les sources (en dev).

    Duplique volontairement common.app_dir() -- voir le docstring du module.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _fallback_config_path():
    """Emplacement de repli si le dossier de l'exe n'est pas inscriptible (cas d'une
    installation dans Program Files, ou d'un exe lancé depuis une clé USB en lecture
    seule) : le profil utilisateur est toujours inscriptible, lui."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MediaDownloader", CONFIG_FILENAME)


def config_path():
    """Chemin du fichier de config effectivement utilisé pour la LECTURE : celui à côté
    de l'exe s'il existe, sinon celui de repli. Un fichier posé à côté de l'exe gagne
    donc toujours, ce qui garde l'appli portable (dossier copiable tel quel)."""
    primary = os.path.join(app_dir(), CONFIG_FILENAME)
    if os.path.isfile(primary):
        return primary
    fallback = _fallback_config_path()
    if os.path.isfile(fallback):
        return fallback
    return primary


def _read_config():
    try:
        # utf-8-sig et pas utf-8 : les éditeurs Windows (Bloc-notes, PowerShell Out-File...)
        # ajoutent volontiers un BOM en tête de fichier, que json.load() refuse en utf-8 nu
        # ("Expecting value: line 1 column 1"). Le fichier serait alors silencieusement
        # ignoré et l'appli repartirait en français sans explication -- constaté en test.
        # utf-8-sig lit correctement les deux cas (avec et sans BOM).
        with open(config_path(), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # Fichier absent (premier lancement) ou corrompu : on repart des valeurs par
        # défaut plutôt que de faire planter le démarrage de l'appli pour si peu.
        return {}


def _write_config(data):
    """Écrit la config à côté de l'exe, avec repli sur le profil utilisateur si ce
    dossier est en lecture seule. Retourne le chemin réellement écrit, ou None."""
    primary = os.path.join(app_dir(), CONFIG_FILENAME)
    for path in (primary, _fallback_config_path()):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return path
        except OSError:
            continue
    return None


def load_language():
    """Langue sauvegardée, ou le français par défaut (premier lancement / valeur
    inconnue dans le fichier)."""
    code = _read_config().get("language")
    return code if code in AVAILABLE_LANGUAGES else DEFAULT_LANGUAGE


def save_language(code):
    """Persiste la langue choisie en conservant les autres clés éventuelles du fichier
    de config (il n'y en a pas d'autres aujourd'hui, mais écraser tout le fichier serait
    un piège à retardement le jour où on y ajoutera un réglage)."""
    if code not in AVAILABLE_LANGUAGES:
        code = DEFAULT_LANGUAGE
    data = _read_config()
    data["language"] = code
    _write_config(data)


# Langue active du processus : fixée une fois pour toutes au démarrage. Changer de
# langue en cours de route ne re-rendrait pas les widgets déjà construits (limite de
# CustomTkinter), donc app.py sauvegarde puis relance l'appli -- voir ToolboxApp.
_current_language = load_language()


def get_language():
    return _current_language


def set_language(code):
    """Change la langue du PROCESSUS EN COURS. Utile pour les tests ; en usage normal
    c'est le redémarrage de l'appli qui applique le changement."""
    global _current_language
    if code in AVAILABLE_LANGUAGES:
        _current_language = code


def t(key, **kwargs):
    """Traduit `key` dans la langue courante, avec interpolation optionnelle.

    Trois filets de sécurité successifs, pour qu'une traduction manquante ou une
    interpolation ratée n'affiche jamais une trace d'erreur à l'utilisateur final :
      1. clé absente dans la langue courante -> version française ;
      2. clé absente partout -> on renvoie la clé elle-même (visible mais inoffensif) ;
      3. .format() en échec (variable manquante) -> on renvoie le texte non interpolé.
    Dans les trois cas on avertit sur stderr, mais UNIQUEMENT en dev : une fois packagé
    en mode fenêtré, sys.stderr vaut None et écrire dedans planterait.
    """
    table = TRANSLATIONS.get(_current_language, {})
    text = table.get(key)

    if text is None:
        text = TRANSLATIONS[DEFAULT_LANGUAGE].get(key)
        if text is None:
            _warn(f"[i18n] clé inconnue : {key!r}")
            return key
        _warn(f"[i18n] clé non traduite en {_current_language!r} : {key!r}")

    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError) as e:
            _warn(f"[i18n] interpolation impossible pour {key!r} : {e}")
    return text


def _warn(message):
    if getattr(sys, "frozen", False):
        return  # pas de stderr utilisable dans un exe fenêtré
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Dictionnaires de traduction.                                               #
#                                                                             #
#  Convention de clés : "<zone>.<intitulé>", zone = "app"/"common"/nom court  #
#  de l'onglet. Le français est la référence : toute clé doit y exister.      #
# --------------------------------------------------------------------------- #

_FR = {
    # ------------------------------------------------------------- Fenêtre ---
    "app.title": "Boîte à outils média — Felix",
    "app.theme_label": "Thème",
    "app.theme_system": "Système",
    "app.theme_light": "Clair",
    "app.theme_dark": "Sombre",
    "app.language_label": "Langue",

    # ------------------------------------------------------ Menu latéral ---
    "tool.downloader": "Vidéos (YouTube, TikTok...)",
    "tool.image_compressor": "Compresseur d'images",
    "tool.audio": "Vidéo → Audio",
    "tool.video_compressor": "Compresseur de vidéo",
    "tool.gif": "Créateur de GIF",
    "tool.text": "Convertisseur de texte",
    "tool.image_converter": "Convertisseur d'images",

    # ------------------------------------------------- Éléments partagés ---
    "common.browse": "Parcourir...",
    "common.open_folder": "Ouvrir le dossier",
    "common.export_logs": "Exporter les logs",
    "common.destination_folder": "Dossier de destination :",
    "common.ready": "Prêt.",
    "common.preparing": "Préparation...",
    "common.done": "Terminé.",
    "common.cancel": "Annuler",
    "common.convert": "Convertir",
    "common.compress": "Compresser",
    "common.no_file_selected": "Aucun fichier sélectionné.",
    "common.one_file_selected": "1 fichier sélectionné.",
    "common.n_files_selected": "{n} fichiers sélectionnés.",
    "common.cancel_requested": "Annulation demandée...",
    "common.stopped_cancelled": "Arrêté (annulé).",
    "common.cancelled_by_user": "Opération annulée par l'utilisateur.",
    "common.unexpected_error_status": "Erreur inattendue.",
    "common.unexpected_error_log": "Erreur inattendue :",
    "common.all_files": "Tous les fichiers",
    "common.video_files": "Fichiers vidéo",
    "common.choose_videos": "Choisir des vidéos",
    "common.no_file_title": "Aucun fichier",
    "common.finished_with_errors": "Terminé avec {errors} erreur(s) sur {total}.",
    "common.unit_bytes": "o",
    "common.unit_kb": "Ko",
    "common.unit_mb": "Mo",
    "common.unit_gb": "Go",

    # ----------------------------------------- Export du journal (common) ---
    "common.log_empty_title": "Journal vide",
    "common.log_empty_message": "Il n'y a rien à exporter pour l'instant.",
    "common.export_log_title": "Exporter le journal",
    "common.log_file_prefix": "journal",
    "common.text_file": "Fichier texte",
    "common.log_exported_title": "Journal exporté",
    "common.log_exported_message": "Journal enregistré :\n{path}",

    # Noms d'outil utilisés dans le nom de fichier du journal exporté.
    "logname.videos": "videos",
    "logname.images": "images",
    "logname.audio": "audio",
    "logname.video": "video",
    "logname.gif": "gif",
    "logname.text": "texte",
    "logname.image_conversion": "conversion_images",

    # ------------------------------------------ Ouverture d'image (common) ---
    "common.raw_needs_rawpy": "Le fichier RAW '{name}' nécessite le module 'rawpy' (non installé).",
    "common.file_unreadable": (
        "Impossible de lire le fichier '{name}' : {error}. S'il est stocké sur "
        "OneDrive/un cloud, vérifie qu'il est bien téléchargé localement (clic droit -> "
        "'Toujours conserver sur cet appareil')."
    ),
    "common.raw_decode_failed": (
        "Impossible de décoder le RAW '{name}' ({error}). Le fichier est peut-être "
        "corrompu, incomplet (pas totalement téléchargé depuis OneDrive/un cloud), ou "
        "d'un modèle d'appareil photo non reconnu par LibRaw."
    ),

    # ------------------------------------------------ Onglet : Vidéos ---
    "downloader.url_label": (
        "Lien vidéo — YouTube, TikTok, et bien d'autres (Instagram, Twitter/X, Facebook, "
        "Vimeo, Twitch, Reddit, Dailymotion, SoundCloud, +1800 sites au total) :"
    ),
    "downloader.format": "Format",
    "downloader.quality": "Qualité",
    "downloader.quality_best": "Meilleure",
    "downloader.whole_playlist": "Playlist entière",
    "downloader.metadata": "Titre / artiste / album + pochette",
    "downloader.download": "Télécharger",
    "downloader.missing_url_title": "Lien manquant",
    "downloader.missing_url_message": "Colle un lien YouTube ou TikTok.",
    "downloader.cancelled_by_user": "Téléchargement annulé par l'utilisateur.",
    "downloader.downloading": "Téléchargement : {filename}  {speed}  ETA {eta}",
    "downloader.finalizing": "Conversion / finalisation...",
    "downloader.starting": "Démarrage : {url}",
    "downloader.postprocessing_failed": (
        "Le post-traitement a échoué (voir le détail dans le journal ci-dessus) -- "
        "souvent un verrou de fichier transitoire (antivirus) sous Windows."
    ),
    "downloader.vimeo_retry": (
        "⚠ Vimeo a rejeté le lien standard (problème connu côté Vimeo) -- nouvel essai "
        "via {url}"
    ),
    "downloader.attempt_failed": "⚠ Échec de la tentative {attempt}/{total}, nouvel essai dans 3s...",
    "downloader.finished": "Téléchargement terminé.",
    "downloader.stopped_playlist_status": "Arrêté -- au moins une piste a échoué.",
    "downloader.stopped_playlist_log": (
        "Arrêté : {error}\nLes autres pistes de la playlist qui ont déjà réussi sont "
        "conservées -- seules celle(s) en échec manquent. Relance le téléchargement de "
        "cette même playlist pour ne réessayer que les pistes manquantes."
    ),
    "downloader.stopped_status": "Arrêté (erreur ou annulation).",
    "downloader.stopped_log": "Arrêté : {error}",

    # --------------------------------------- Onglet : Compresseur d'images ---
    "imgcomp.files_label": (
        "Images à compresser (formats classiques + RAW appareil photo + HEIC/HEIF iPhone) "
        "— glisse-dépose aussi possible :"
    ),
    "imgcomp.choose_images": "Choisir des images...",
    "imgcomp.choose_images_title": "Choisir des images",
    "imgcomp.mode_label": "Mode de compression",
    "imgcomp.mode_manual": "Qualité manuelle",
    "imgcomp.mode_target": "Taille cible",
    "imgcomp.preset_label": "Préréglage (plateforme de destination)",
    "imgcomp.preset.max_quality": "Qualité maximale (peu de compression)",
    "imgcomp.preset.social": "Réseaux sociaux (Discord/WhatsApp/Messenger)",
    "imgcomp.preset.web": "Web / site internet",
    "imgcomp.preset.email": "Email (pièce jointe légère)",
    "imgcomp.preset.avatar": "Avatar / miniature",
    "imgcomp.preset.custom": "Personnalisé",
    "imgcomp.quality": "Qualité",
    "imgcomp.max_dimension": "Dimension max (px, vide = aucune)",
    "imgcomp.target_size": "Poids maximum voulu",
    "imgcomp.target_placeholder": "ex. 500",
    "imgcomp.target_hint": "(qualité et, si besoin, dimension ajustées automatiquement)",
    "imgcomp.output_format": "Format de sortie",
    "imgcomp.keep_format": "Garder le format d'origine",
    "imgcomp.output_subdir": "Images compressées",
    "imgcomp.filetype_images": "Images",
    "imgcomp.filetype_raw": "RAW appareil photo",
    "imgcomp.filetype_heic": "HEIC/HEIF (iPhone)",
    "imgcomp.no_image_title": "Aucune image",
    "imgcomp.no_image_message": "Choisis d'abord une ou plusieurs images à compresser.",
    "imgcomp.invalid_dimension_title": "Dimension invalide",
    "imgcomp.invalid_dimension_message": "La dimension max doit être un nombre entier (ou vide).",
    "imgcomp.invalid_size_title": "Taille invalide",
    "imgcomp.invalid_size_message": "La taille cible doit être un nombre positif.",
    "imgcomp.start_target": "Démarrage de la compression de {total} fichier(s) (cible : {target} max)...",
    "imgcomp.start": "Démarrage de la compression de {total} fichier(s)...",
    "imgcomp.compressing": "Compression de {name} ({index}/{total})...",
    "imgcomp.quality_used": " [qualité {quality}]",
    "imgcomp.target_missed": " ⚠ cible non atteinte (image déjà très compressée)",
    "imgcomp.file_result": "{name} : {before} -> {after} ({sign}{pct}%){extra}",
    "imgcomp.file_failed": "✖ {name} : échec ({error})",
    "imgcomp.total": "Terminé. Total : {before} -> {after} ({sign}{pct}%). {errors} erreur(s).",

    # ------------------------------------------ Onglet : Vidéo -> Audio ---
    "audio.files_label": "Fichiers vidéo à convertir — glisse-dépose aussi possible :",
    "audio.choose_videos_title": "Sélectionner des vidéos",
    "audio.format": "Format audio",
    "audio.format.mp3": "MP3",
    "audio.format.aac": "AAC (.m4a)",
    "audio.format.ogg": "OGG (Vorbis)",
    "audio.format.wav": "WAV (sans perte)",
    "audio.format.flac": "FLAC (sans perte)",
    "audio.bitrate": "Bitrate",
    "audio.output_subdir": "Audio extrait",
    "audio.no_file_message": "Sélectionne au moins un fichier vidéo à convertir.",
    "audio.processing": "Traitement {index}/{total} : {name}",
    "audio.file_failed": "Erreur sur '{name}' :\n{error}",
    "audio.finished": "Conversion terminée.",
    "audio.ffmpeg_failed": "ffmpeg a échoué (code {code}) pour '{path}' :\n{stderr}",

    # ------------------------------------- Onglet : Compresseur de vidéo ---
    "vidcomp.files_label": "Vidéos à compresser — glisse-dépose aussi possible :",
    "vidcomp.preset_label": "Préréglage (plateforme de destination)",
    "vidcomp.preset.max_quality": "Qualité maximale (peu de compression)",
    "vidcomp.preset.social": "Réseaux sociaux (Discord/WhatsApp/Messenger)",
    "vidcomp.preset.web": "Web / site internet",
    "vidcomp.preset.email": "Email (pièce jointe légère)",
    "vidcomp.preset.custom": "Personnalisé",
    "vidcomp.crf": "Qualité (CRF -- plus bas = meilleure qualité)",
    "vidcomp.max_width": "Largeur max (px, vide = aucune)",
    "vidcomp.output_subdir": "Vidéos compressées",
    "vidcomp.file_suffix": "_compressé",
    "vidcomp.ready": "Prêt. (le ré-encodage vidéo prend du temps -- proportionnel à la durée)",
    "vidcomp.no_video_title": "Aucune vidéo",
    "vidcomp.no_video_message": "Choisis d'abord une ou plusieurs vidéos à compresser.",
    "vidcomp.invalid_width_title": "Largeur invalide",
    "vidcomp.invalid_width_message": "La largeur max doit être un nombre entier (ou vide).",
    "vidcomp.cancel_requested": "Annulation demandée (le fichier en cours de compression finira d'abord)...",
    "vidcomp.start": "Démarrage de la compression de {total} vidéo(s)...",
    "vidcomp.compressing": "Compression de {name} ({index}/{total})... ça peut prendre un moment.",
    "vidcomp.file_result": "{name} : {before} -> {after} ({sign}{pct}%)",
    "vidcomp.file_failed": "✖ {name} : échec\n{error}",
    "vidcomp.total": "Terminé. Total : {before} -> {after} ({sign}{pct}%). {errors} erreur(s).",
    "vidcomp.ffmpeg_failed": "ffmpeg a échoué (code {code}) pour '{path}' :\n{stderr}",

    # ------------------------------------------- Onglet : Créateur de GIF ---
    "gif.files_label": "Vidéo(s) source — glisse-dépose aussi possible :",
    "gif.settings_hint": (
        "Le même réglage de départ/durée s'applique à tous les fichiers sélectionnés "
        "(laisser vide = du début à la fin) :"
    ),
    "gif.start": "Départ (s ou mm:ss)",
    "gif.duration": "Durée (s, vide = jusqu'à la fin)",
    "gif.duration_placeholder": "ex. 3",
    "gif.fps": "Images/seconde",
    "gif.width": "Largeur (px)",
    "gif.preview_label": "Aperçu de l'extrait (1er fichier sélectionné, généré en petit format pour la rapidité) :",
    "gif.generate_preview": "Générer l'aperçu",
    "gif.no_preview": "Aucun aperçu généré pour l'instant.",
    "gif.generating_preview": "Génération de l'aperçu...",
    "gif.preview_ready": "Aperçu généré ({frames} images) — c'est ce qui sera dans le GIF final (en pleine résolution).",
    "gif.preview_unreadable": "✖ Aperçu illisible : {error}",
    "gif.preview_empty": "✖ Aperçu vide (extrait trop court ?)",
    "gif.preview_error": "✖ {error}",
    "gif.unknown_error": "erreur inconnue",
    "gif.create": "Créer le(s) GIF",
    "gif.output_subdir": "GIF",
    "gif.no_file_message": "Choisis au moins une vidéo.",
    "gif.invalid_setting_title": "Réglage invalide",
    "gif.negative_start": "Le départ ne peut pas être négatif.",
    "gif.invalid_duration": "La durée doit être un nombre positif de secondes (ou vide).",
    "gif.invalid_time_format": "Format de temps invalide : '{text}' (attendu mm:ss ou h:mm:ss)",
    "gif.creating": "Création du GIF {index}/{total} : {name}...",
    "gif.file_result": "{name} -> {output} ({size})",
    "gif.file_failed": "✖ {name} : échec\n{error}",
    "gif.finished": "Création des GIF terminée.",
    "gif.palette_failed": "ffmpeg (génération palette) a échoué :\n{stderr}",
    "gif.encode_failed": "ffmpeg (application palette) a échoué :\n{stderr}",

    # -------------------------------------- Onglet : Convertisseur de texte ---
    "text.files_label": "Fichiers à convertir (.txt, .md, .html, .docx, .doc, .pdf) — glisse-dépose aussi possible :",
    "text.files_placeholder": "Aucun fichier sélectionné",
    "text.n_files_selected": "{n} fichiers sélectionnés",
    "text.choose_files_title": "Choisir des fichiers à convertir",
    "text.filetype_documents": "Documents texte",
    "text.convert_to": "Convertir vers",
    "text.output_subdir": "Documents convertis",
    "text.no_file_message": "Sélectionne au moins un fichier à convertir.",
    "text.converting": "Conversion : {name} ({index}/{total})",
    "text.file_ok": "OK : {name} -> {output}",
    "text.file_failed": "Erreur : {name} -> {error}",
    "text.finished": "Conversion terminée.",
    "text.doc_needs_word": (
        "Les fichiers .doc (ancien format Word) nécessitent Microsoft Word installé sur "
        "cette machine pour être lus. Solution : ouvre le fichier dans Word, 'Enregistrer "
        "sous' au format .docx, puis reconvertis ce nouveau fichier."
    ),
    "text.doc_read_failed": (
        "Impossible de lire le .doc via Word (Word n'est peut-être pas installé, ou le "
        "fichier est corrompu/protégé) : {error}"
    ),
    "text.doc_convert_needs_word": "La conversion d'un .doc nécessite Microsoft Word installé sur cette machine.",
    "text.doc_convert_failed": "Conversion .doc -> {ext} via Word échouée : {error}",
    "text.unsupported_docx_container": "Type de conteneur .docx non géré : {type}",
    "text.unsupported_source": "Format source non supporté : '{ext}'. Formats acceptés : {accepted}",
    "text.unsupported_target": "Format de destination non supporté : '{ext}'. Formats acceptés : {accepted}",

    # ------------------------------------ Onglet : Convertisseur d'images ---
    "imgconv.files_label": (
        "Fichiers à convertir (images, RAW appareil photo, HEIC/HEIF iPhone et/ou PDF) "
        "— glisse-dépose aussi possible :"
    ),
    "imgconv.choose_files_title": "Choisir des fichiers",
    "imgconv.filetype_images_pdf": "Images et PDF",
    "imgconv.filetype_raw": "RAW appareil photo",
    "imgconv.filetype_heic": "HEIC/HEIF (iPhone)",
    "imgconv.convert_to": "Convertir vers",
    "imgconv.combine_pdf": "Combiner toutes les images en un seul PDF",
    "imgconv.dpi": "Résolution (DPI)",
    "imgconv.output_subdir": "Images converties",
    "imgconv.combined_pdf_name": "images_converties.pdf",
    "imgconv.n_files_selected": "{n} fichiers sélectionnés.",
    "imgconv.no_file_message": "Choisis au moins un fichier à convertir.",
    "imgconv.skipped_pdf": "Ignoré (déjà un PDF) : {name}",
    "imgconv.extracting_pages": "Extraction des pages : {name}",
    "imgconv.converting": "Conversion : {name}",
    "imgconv.created": "Créé : {path}",
    "imgconv.file_failed": "✖ {name} : échec ({error})",
    "imgconv.generating_pdf": "Génération du PDF...",
    "imgconv.pdf_failed": "✖ Génération du PDF échouée : {error}",
    "imgconv.finished_status": "Terminé. {count} fichier(s) créé(s).",
    "imgconv.finished": "Conversion terminée.",
}

_EN = {
    # ------------------------------------------------------------- Window ---
    "app.title": "Media Toolbox — Felix",
    "app.theme_label": "Theme",
    "app.theme_system": "System",
    "app.theme_light": "Light",
    "app.theme_dark": "Dark",
    "app.language_label": "Language",

    # --------------------------------------------------------- Side menu ---
    "tool.downloader": "Videos (YouTube, TikTok...)",
    "tool.image_compressor": "Image compressor",
    "tool.audio": "Video → Audio",
    "tool.video_compressor": "Video compressor",
    "tool.gif": "GIF maker",
    "tool.text": "Text converter",
    "tool.image_converter": "Image converter",

    # ---------------------------------------------------- Shared elements ---
    "common.browse": "Browse...",
    "common.open_folder": "Open folder",
    "common.export_logs": "Export logs",
    "common.destination_folder": "Destination folder:",
    "common.ready": "Ready.",
    "common.preparing": "Preparing...",
    "common.done": "Done.",
    "common.cancel": "Cancel",
    "common.convert": "Convert",
    "common.compress": "Compress",
    "common.no_file_selected": "No file selected.",
    "common.one_file_selected": "1 file selected.",
    "common.n_files_selected": "{n} files selected.",
    "common.cancel_requested": "Cancellation requested...",
    "common.stopped_cancelled": "Stopped (cancelled).",
    "common.cancelled_by_user": "Operation cancelled by the user.",
    "common.unexpected_error_status": "Unexpected error.",
    "common.unexpected_error_log": "Unexpected error:",
    "common.all_files": "All files",
    "common.video_files": "Video files",
    "common.choose_videos": "Choose videos",
    "common.no_file_title": "No file",
    "common.finished_with_errors": "Finished with {errors} error(s) out of {total}.",
    "common.unit_bytes": "B",
    "common.unit_kb": "KB",
    "common.unit_mb": "MB",
    "common.unit_gb": "GB",

    # ------------------------------------------- Log export (common.py) ---
    "common.log_empty_title": "Empty log",
    "common.log_empty_message": "There is nothing to export yet.",
    "common.export_log_title": "Export the log",
    "common.log_file_prefix": "log",
    "common.text_file": "Text file",
    "common.log_exported_title": "Log exported",
    "common.log_exported_message": "Log saved to:\n{path}",

    "logname.videos": "videos",
    "logname.images": "images",
    "logname.audio": "audio",
    "logname.video": "video",
    "logname.gif": "gif",
    "logname.text": "text",
    "logname.image_conversion": "image_conversion",

    # ------------------------------------------- Image loading (common.py) ---
    "common.raw_needs_rawpy": "The RAW file '{name}' requires the 'rawpy' module (not installed).",
    "common.file_unreadable": (
        "Could not read the file '{name}': {error}. If it is stored on OneDrive/a cloud "
        "drive, make sure it has been downloaded locally (right-click -> 'Always keep on "
        "this device')."
    ),
    "common.raw_decode_failed": (
        "Could not decode the RAW file '{name}' ({error}). The file may be corrupted, "
        "incomplete (not fully downloaded from OneDrive/a cloud drive), or from a camera "
        "model LibRaw does not recognise."
    ),

    # --------------------------------------------------- Tab: Videos ---
    "downloader.url_label": (
        "Video link — YouTube, TikTok and many more (Instagram, Twitter/X, Facebook, "
        "Vimeo, Twitch, Reddit, Dailymotion, SoundCloud, 1800+ sites in total):"
    ),
    "downloader.format": "Format",
    "downloader.quality": "Quality",
    "downloader.quality_best": "Best",
    "downloader.whole_playlist": "Whole playlist",
    "downloader.metadata": "Title / artist / album + cover art",
    "downloader.download": "Download",
    "downloader.missing_url_title": "Missing link",
    "downloader.missing_url_message": "Paste a YouTube or TikTok link.",
    "downloader.cancelled_by_user": "Download cancelled by the user.",
    "downloader.downloading": "Downloading: {filename}  {speed}  ETA {eta}",
    "downloader.finalizing": "Converting / finalising...",
    "downloader.starting": "Starting: {url}",
    "downloader.postprocessing_failed": (
        "Post-processing failed (see the details in the log above) -- often a transient "
        "file lock (antivirus) on Windows."
    ),
    "downloader.vimeo_retry": (
        "⚠ Vimeo rejected the standard link (known issue on Vimeo's side) -- retrying "
        "via {url}"
    ),
    "downloader.attempt_failed": "⚠ Attempt {attempt}/{total} failed, retrying in 3s...",
    "downloader.finished": "Download finished.",
    "downloader.stopped_playlist_status": "Stopped -- at least one track failed.",
    "downloader.stopped_playlist_log": (
        "Stopped: {error}\nThe other playlist tracks that already succeeded are kept -- "
        "only the failed one(s) are missing. Start the download of this same playlist "
        "again to retry only the missing tracks."
    ),
    "downloader.stopped_status": "Stopped (error or cancellation).",
    "downloader.stopped_log": "Stopped: {error}",

    # ------------------------------------------- Tab: Image compressor ---
    "imgcomp.files_label": (
        "Images to compress (common formats + camera RAW + iPhone HEIC/HEIF) — "
        "drag and drop also works:"
    ),
    "imgcomp.choose_images": "Choose images...",
    "imgcomp.choose_images_title": "Choose images",
    "imgcomp.mode_label": "Compression mode",
    "imgcomp.mode_manual": "Manual quality",
    "imgcomp.mode_target": "Target size",
    "imgcomp.preset_label": "Preset (destination platform)",
    "imgcomp.preset.max_quality": "Maximum quality (little compression)",
    "imgcomp.preset.social": "Social media (Discord/WhatsApp/Messenger)",
    "imgcomp.preset.web": "Web / website",
    "imgcomp.preset.email": "Email (light attachment)",
    "imgcomp.preset.avatar": "Avatar / thumbnail",
    "imgcomp.preset.custom": "Custom",
    "imgcomp.quality": "Quality",
    "imgcomp.max_dimension": "Max dimension (px, empty = none)",
    "imgcomp.target_size": "Maximum wanted size",
    "imgcomp.target_placeholder": "e.g. 500",
    "imgcomp.target_hint": "(quality and, if needed, dimension adjusted automatically)",
    "imgcomp.output_format": "Output format",
    "imgcomp.keep_format": "Keep the original format",
    "imgcomp.output_subdir": "Compressed images",
    "imgcomp.filetype_images": "Images",
    "imgcomp.filetype_raw": "Camera RAW",
    "imgcomp.filetype_heic": "HEIC/HEIF (iPhone)",
    "imgcomp.no_image_title": "No image",
    "imgcomp.no_image_message": "Choose one or more images to compress first.",
    "imgcomp.invalid_dimension_title": "Invalid dimension",
    "imgcomp.invalid_dimension_message": "The max dimension must be a whole number (or empty).",
    "imgcomp.invalid_size_title": "Invalid size",
    "imgcomp.invalid_size_message": "The target size must be a positive number.",
    "imgcomp.start_target": "Starting compression of {total} file(s) (target: {target} max)...",
    "imgcomp.start": "Starting compression of {total} file(s)...",
    "imgcomp.compressing": "Compressing {name} ({index}/{total})...",
    "imgcomp.quality_used": " [quality {quality}]",
    "imgcomp.target_missed": " ⚠ target not reached (image already heavily compressed)",
    "imgcomp.file_result": "{name}: {before} -> {after} ({sign}{pct}%){extra}",
    "imgcomp.file_failed": "✖ {name}: failed ({error})",
    "imgcomp.total": "Done. Total: {before} -> {after} ({sign}{pct}%). {errors} error(s).",

    # ---------------------------------------------- Tab: Video -> Audio ---
    "audio.files_label": "Video files to convert — drag and drop also works:",
    "audio.choose_videos_title": "Select videos",
    "audio.format": "Audio format",
    "audio.format.mp3": "MP3",
    "audio.format.aac": "AAC (.m4a)",
    "audio.format.ogg": "OGG (Vorbis)",
    "audio.format.wav": "WAV (lossless)",
    "audio.format.flac": "FLAC (lossless)",
    "audio.bitrate": "Bitrate",
    "audio.output_subdir": "Extracted audio",
    "audio.no_file_message": "Select at least one video file to convert.",
    "audio.processing": "Processing {index}/{total}: {name}",
    "audio.file_failed": "Error on '{name}':\n{error}",
    "audio.finished": "Conversion finished.",
    "audio.ffmpeg_failed": "ffmpeg failed (code {code}) for '{path}':\n{stderr}",

    # ------------------------------------------- Tab: Video compressor ---
    "vidcomp.files_label": "Videos to compress — drag and drop also works:",
    "vidcomp.preset_label": "Preset (destination platform)",
    "vidcomp.preset.max_quality": "Maximum quality (little compression)",
    "vidcomp.preset.social": "Social media (Discord/WhatsApp/Messenger)",
    "vidcomp.preset.web": "Web / website",
    "vidcomp.preset.email": "Email (light attachment)",
    "vidcomp.preset.custom": "Custom",
    "vidcomp.crf": "Quality (CRF -- lower = better quality)",
    "vidcomp.max_width": "Max width (px, empty = none)",
    "vidcomp.output_subdir": "Compressed videos",
    "vidcomp.file_suffix": "_compressed",
    "vidcomp.ready": "Ready. (video re-encoding takes time -- proportional to the duration)",
    "vidcomp.no_video_title": "No video",
    "vidcomp.no_video_message": "Choose one or more videos to compress first.",
    "vidcomp.invalid_width_title": "Invalid width",
    "vidcomp.invalid_width_message": "The max width must be a whole number (or empty).",
    "vidcomp.cancel_requested": "Cancellation requested (the file being compressed will finish first)...",
    "vidcomp.start": "Starting compression of {total} video(s)...",
    "vidcomp.compressing": "Compressing {name} ({index}/{total})... this can take a while.",
    "vidcomp.file_result": "{name}: {before} -> {after} ({sign}{pct}%)",
    "vidcomp.file_failed": "✖ {name}: failed\n{error}",
    "vidcomp.total": "Done. Total: {before} -> {after} ({sign}{pct}%). {errors} error(s).",
    "vidcomp.ffmpeg_failed": "ffmpeg failed (code {code}) for '{path}':\n{stderr}",

    # ------------------------------------------------------ Tab: GIF maker ---
    "gif.files_label": "Source video(s) — drag and drop also works:",
    "gif.settings_hint": (
        "The same start/duration setting applies to every selected file (leave empty = "
        "from start to end):"
    ),
    "gif.start": "Start (s or mm:ss)",
    "gif.duration": "Duration (s, empty = until the end)",
    "gif.duration_placeholder": "e.g. 3",
    "gif.fps": "Frames/second",
    "gif.width": "Width (px)",
    "gif.preview_label": "Clip preview (1st selected file, generated small for speed):",
    "gif.generate_preview": "Generate preview",
    "gif.no_preview": "No preview generated yet.",
    "gif.generating_preview": "Generating the preview...",
    "gif.preview_ready": "Preview generated ({frames} frames) — this is what the final GIF will contain (at full resolution).",
    "gif.preview_unreadable": "✖ Unreadable preview: {error}",
    "gif.preview_empty": "✖ Empty preview (clip too short?)",
    "gif.preview_error": "✖ {error}",
    "gif.unknown_error": "unknown error",
    "gif.create": "Create GIF(s)",
    "gif.output_subdir": "GIF",
    "gif.no_file_message": "Choose at least one video.",
    "gif.invalid_setting_title": "Invalid setting",
    "gif.negative_start": "The start time cannot be negative.",
    "gif.invalid_duration": "The duration must be a positive number of seconds (or empty).",
    "gif.invalid_time_format": "Invalid time format: '{text}' (expected mm:ss or h:mm:ss)",
    "gif.creating": "Creating GIF {index}/{total}: {name}...",
    "gif.file_result": "{name} -> {output} ({size})",
    "gif.file_failed": "✖ {name}: failed\n{error}",
    "gif.finished": "GIF creation finished.",
    "gif.palette_failed": "ffmpeg (palette generation) failed:\n{stderr}",
    "gif.encode_failed": "ffmpeg (palette application) failed:\n{stderr}",

    # -------------------------------------------------- Tab: Text converter ---
    "text.files_label": "Files to convert (.txt, .md, .html, .docx, .doc, .pdf) — drag and drop also works:",
    "text.files_placeholder": "No file selected",
    "text.n_files_selected": "{n} files selected",
    "text.choose_files_title": "Choose files to convert",
    "text.filetype_documents": "Text documents",
    "text.convert_to": "Convert to",
    "text.output_subdir": "Converted documents",
    "text.no_file_message": "Select at least one file to convert.",
    "text.converting": "Converting: {name} ({index}/{total})",
    "text.file_ok": "OK: {name} -> {output}",
    "text.file_failed": "Error: {name} -> {error}",
    "text.finished": "Conversion finished.",
    "text.doc_needs_word": (
        ".doc files (the old Word format) require Microsoft Word installed on this "
        "machine to be read. Workaround: open the file in Word, 'Save as' in .docx "
        "format, then convert that new file instead."
    ),
    "text.doc_read_failed": (
        "Could not read the .doc through Word (Word may not be installed, or the file is "
        "corrupted/protected): {error}"
    ),
    "text.doc_convert_needs_word": "Converting a .doc requires Microsoft Word installed on this machine.",
    "text.doc_convert_failed": ".doc -> {ext} conversion through Word failed: {error}",
    "text.unsupported_docx_container": "Unsupported .docx container type: {type}",
    "text.unsupported_source": "Unsupported source format: '{ext}'. Accepted formats: {accepted}",
    "text.unsupported_target": "Unsupported destination format: '{ext}'. Accepted formats: {accepted}",

    # ------------------------------------------------- Tab: Image converter ---
    "imgconv.files_label": (
        "Files to convert (images, camera RAW, iPhone HEIC/HEIF and/or PDF) — drag and "
        "drop also works:"
    ),
    "imgconv.choose_files_title": "Choose files",
    "imgconv.filetype_images_pdf": "Images and PDF",
    "imgconv.filetype_raw": "Camera RAW",
    "imgconv.filetype_heic": "HEIC/HEIF (iPhone)",
    "imgconv.convert_to": "Convert to",
    "imgconv.combine_pdf": "Combine all images into a single PDF",
    "imgconv.dpi": "Resolution (DPI)",
    "imgconv.output_subdir": "Converted images",
    "imgconv.combined_pdf_name": "converted_images.pdf",
    "imgconv.n_files_selected": "{n} files selected.",
    "imgconv.no_file_message": "Choose at least one file to convert.",
    "imgconv.skipped_pdf": "Skipped (already a PDF): {name}",
    "imgconv.extracting_pages": "Extracting pages: {name}",
    "imgconv.converting": "Converting: {name}",
    "imgconv.created": "Created: {path}",
    "imgconv.file_failed": "✖ {name}: failed ({error})",
    "imgconv.generating_pdf": "Generating the PDF...",
    "imgconv.pdf_failed": "✖ PDF generation failed: {error}",
    "imgconv.finished_status": "Done. {count} file(s) created.",
    "imgconv.finished": "Conversion finished.",
}

TRANSLATIONS = {"fr": _FR, "en": _EN}
