"""
Helpers partagés entre tous les onglets de l'appli (chemins, binaires embarqués).
"""

import datetime
import io
import os
import sys
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from i18n import t

DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads" / "MediaDownloader")

# Style commun réutilisé par tous les onglets pour garder une mise en page cohérente.
PAD = {"padx": 16, "pady": 8}

# Formats RAW d'appareil photo courants -- Pillow ne sait pas les ouvrir nativement,
# il faut passer par rawpy/LibRaw (voir open_image_any ci-dessous).
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw", ".raw"}

# Photos iPhone (HEIC/HEIF). Contrairement au RAW, pas besoin de traitement spécial dans
# open_image_any : pillow_heif s'enregistre comme plugin Pillow standard une fois pour
# toutes ci-dessous, donc Image.open() les gère nativement ensuite, en lecture seule
# (pillow_heif de cette version ne fournit qu'un lecteur, pas d'encodeur HEIF).
HEIF_EXTENSIONS = {".heic", ".heif"}
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def app_dir():
    """Dossier contenant l'exe (une fois packagé) ou app.py (en dev)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundled_bin_dir():
    """Dossier bin\\ à côté de l'exe/script, s'il existe (ffmpeg/ffprobe/deno embarqués)."""
    candidate = os.path.join(app_dir(), "bin")
    if os.path.isfile(os.path.join(candidate, "ffmpeg.exe")):
        return candidate
    return None


def bundled_ffmpeg_exe():
    """Chemin complet vers ffmpeg.exe embarqué, ou juste 'ffmpeg' pour retomber sur le PATH."""
    bin_dir = bundled_bin_dir()
    if bin_dir:
        return os.path.join(bin_dir, "ffmpeg.exe")
    return "ffmpeg"


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def build_scrollable_body(parent):
    """Crée et retourne un CTkScrollableFrame occupant tout `parent`, utilisé comme
    conteneur racine du contenu de chaque onglet (à la place de `parent` directement).

    But : certains outils ont beaucoup de contenu (zone de log, aperçu de GIF...) qui
    peut dépasser la hauteur de la fenêtre si elle est redimensionnée en plus petit --
    sans ça, le bas (boutons, journal...) serait tout simplement coupé et inaccessible.
    Avec ce conteneur, une barre de défilement apparaît automatiquement dès que le
    contenu dépasse l'espace visible, au lieu de rogner silencieusement la fenêtre.
    """
    body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    body.pack(fill="both", expand=True)
    return body


def export_log_to_file(log_box, tool_name):
    """Sauvegarde le contenu intégral d'un journal (CTkTextbox) dans un fichier .txt choisi
    par l'utilisateur -- pratique pour transmettre le journal complet en cas d'erreur (ex.
    coller son contenu à Claude) sans avoir à copier/coller manuellement depuis la fenêtre,
    ce qui tronque souvent les longs messages d'erreur multi-lignes."""
    content = log_box.get("1.0", "end").strip()
    if not content:
        messagebox.showinfo(t("common.log_empty_title"), t("common.log_empty_message"))
        return

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    initial_name = f"{t('common.log_file_prefix')}_{tool_name}_{timestamp}.txt"
    path = filedialog.asksaveasfilename(
        title=t("common.export_log_title"),
        initialfile=initial_name,
        defaultextension=".txt",
        filetypes=[(t("common.text_file"), "*.txt"), (t("common.all_files"), "*.*")],
    )
    if not path:
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    messagebox.showinfo(t("common.log_exported_title"), t("common.log_exported_message", path=path))


def open_image_any(path):
    """Ouvre une image et retourne toujours un objet Pillow standard, y compris pour les
    formats RAW d'appareil photo (.cr2/.nef/.arw/.dng/...) qui passent par rawpy/LibRaw
    (dématriçage avec la balance des blancs de l'appareil) plutôt que Pillow directement,
    Pillow ne sachant pas lire ces formats. Lève un message clair si rawpy manque."""
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXTENSIONS:
        try:
            import rawpy
        except ImportError as e:
            raise RuntimeError(
                t("common.raw_needs_rawpy", name=os.path.basename(path))
            ) from e

        # On lit d'abord les octets nous-mêmes via Python plutôt que de laisser LibRaw
        # ouvrir le chemin directement. Deux raisons :
        # 1) Si le fichier est stocké sur OneDrive/un cloud et pas encore téléchargé
        #    localement (icône nuage), l'ouverture Python standard force bien le
        #    téléchargement à la demande, alors que l'ouverture bas niveau de LibRaw
        #    échoue parfois avec une erreur d'E/S opaque sur ces fichiers "en ligne
        #    uniquement".
        # 2) Ça évite d'éventuels soucis d'encodage de chemin côté LibRaw.
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise RuntimeError(
                t("common.file_unreadable", name=os.path.basename(path), error=e)
            ) from e

        try:
            with rawpy.imread(io.BytesIO(data)) as raw:
                rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        except Exception as e:
            raise RuntimeError(
                t("common.raw_decode_failed", name=os.path.basename(path), error=e)
            ) from e
        return Image.fromarray(rgb)

    img = Image.open(path)
    img.load()
    return img


def enable_file_drop(widget, on_files_dropped, extensions=None):
    """Enregistre `widget` comme cible de glisser-déposer de fichiers Windows Explorer.

    `on_files_dropped(paths)` est appelé avec la liste des chemins déposés (filtrés sur
    `extensions` si fourni, ex. {'.png', '.jpg'} -- silencieusement ignoré si aucun fichier
    déposé ne correspond). Ne fait rien (pas de crash) si tkinterdnd2 n'est pas disponible
    ou si la fenêtre racine n'a pas été initialisée en mode glisser-déposer (voir app.py) :
    dans ce cas, la sélection via le bouton "Parcourir..." reste le seul moyen, dégradation
    silencieuse plutôt qu'erreur bloquante.
    """
    try:
        from tkinterdnd2 import DND_FILES
    except ImportError:
        return

    def _handler(event):
        try:
            paths = list(widget.tk.splitlist(event.data))
        except Exception:
            return
        if extensions:
            paths = [p for p in paths if os.path.splitext(p)[1].lower() in extensions]
        if paths:
            on_files_dropped(paths)

    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", _handler)
    except Exception:
        pass
