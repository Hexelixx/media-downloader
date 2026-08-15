"""
Onglet : créateur de GIF animé à partir d'un extrait de vidéo.

Utilise la technique standard ffmpeg en deux passes (palettegen + paletteuse) : génère
une palette de couleurs optimisée sur l'extrait puis l'applique, ce qui donne un rendu
nettement meilleur (moins de bruit/bandes de couleur) qu'une conversion directe.
"""

import os
import subprocess
import tempfile
import threading
import queue
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image

from common import (
    PAD, DEFAULT_OUTPUT_DIR, bundled_ffmpeg_exe, ensure_dir, enable_file_drop,
    build_scrollable_body, export_log_to_file,
)
from i18n import t

PREVIEW_MAX_WIDTH = 320  # aperçu volontairement plus petit que la sortie finale, pour la rapidité

VIDEO_FILETYPES = [
    (t("common.video_files"), "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.mpg *.mpeg"),
    (t("common.all_files"), "*.*"),
]
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg"}

FPS_CHOICES = ["8", "10", "12", "15", "20", "25"]
DEFAULT_FPS = "12"
WIDTH_CHOICES = ["320", "480", "640", "854", "1280"]
DEFAULT_WIDTH = "480"


# --------------------------------------------------------------------------- #
#  Logique pure (aucune référence à un widget), testable indépendamment.      #
# --------------------------------------------------------------------------- #

def parse_time_to_seconds(text: str) -> float:
    """Convertit un temps saisi en secondes. Accepte '90', '12.5', ou 'mm:ss'/'h:mm:ss'.
    Chaîne vide -> 0.0."""
    text = (text or "").strip()
    if not text:
        return 0.0
    if ":" in text:
        parts = text.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(t("gif.invalid_time_format", text=text))
        parts = [float(p) for p in parts]
        seconds = 0.0
        for p in parts:
            seconds = seconds * 60 + p
        return seconds
    return float(text)


def video_to_gif(input_path: str, output_path: str, ffmpeg_exe: str, start_seconds: float = 0.0,
                  duration_seconds: float = None, fps: int = 12, width: int = 480) -> None:
    """Convertit un extrait de vidéo (à partir de start_seconds, sur duration_seconds --
    None/0 = jusqu'à la fin) en GIF animé, via la technique palettegen/paletteuse (2 passes
    ffmpeg) pour une bien meilleure qualité de couleurs qu'une conversion directe.

    Crée le dossier de destination si besoin. Lève une exception (avec le stderr de
    ffmpeg dans le message) si une passe échoue.
    """
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        ensure_dir(out_dir)

    fd, palette_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        vf_base = f"fps={fps},scale={int(width)}:-1:flags=lanczos"

        seek_args = ["-ss", str(start_seconds)] if start_seconds else []
        duration_args = ["-t", str(duration_seconds)] if duration_seconds else []

        # Passe 1 : génère une palette de couleurs optimisée pour cet extrait précis.
        cmd_palette = (
            [ffmpeg_exe, "-y"] + seek_args + ["-i", input_path] + duration_args
            + ["-vf", f"{vf_base},palettegen", palette_path]
        )
        result = subprocess.run(cmd_palette, capture_output=True, text=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise RuntimeError(t("gif.palette_failed", stderr=result.stderr))

        # Passe 2 : ré-encode l'extrait en appliquant cette palette. IMPORTANT : -ss/-t
        # doivent être placés avant le -i de la vidéo (PAS entre les deux -i), sinon
        # ffmpeg les applique au second input (la palette, une image fixe) au lieu de
        # la vidéo, et la durée demandée est silencieusement ignorée.
        cmd_gif = (
            [ffmpeg_exe, "-y"] + seek_args + duration_args + ["-i", input_path]
            + ["-i", palette_path, "-lavfi", f"{vf_base}[x];[x][1:v]paletteuse", output_path]
        )
        result = subprocess.run(cmd_gif, capture_output=True, text=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise RuntimeError(t("gif.encode_failed", stderr=result.stderr))
    finally:
        try:
            os.remove(palette_path)
        except OSError:
            pass


def output_path_for(input_path: str, output_dir: str) -> str:
    base = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(output_dir, base + ".gif")


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} {t('common.unit_bytes')}"
    kb = num_bytes / 1024.0
    if kb < 1024:
        return f"{kb:.0f} {t('common.unit_kb')}"
    return f"{kb / 1024.0:.1f} {t('common.unit_mb')}"


# --------------------------------------------------------------------------- #
#  Interface CustomTkinter                                                    #
# --------------------------------------------------------------------------- #

class GifMakerTab(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.cancel_flag = threading.Event()
        self.selected_files = []

        self._preview_temp_path = None
        self._preview_frames = []  # liste de (CTkImage, duree_ms)
        self._preview_frame_index = 0
        self._preview_after_id = None
        # Image transparente 1x1 réutilisée pour "vider" l'aperçu. IMPORTANT : passer
        # image=None à CTkLabel.configure() ne vide PAS réellement l'image affichée
        # (no-op connu de CustomTkinter -- _update_image() ignore silencieusement le
        # cas image=None) ; l'ancienne image reste référencée par le widget sous-jacent
        # et plante (TclError "image ... doesn't exist") une fois que les anciennes
        # frames sont libérées par le ramasse-miettes. On configure donc toujours une
        # image concrète et vivante, jamais None.
        _blank = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        self._blank_preview_image = ctk.CTkImage(light_image=_blank, dark_image=_blank, size=(1, 1))

        self._build_ui()
        self.after(100, self._poll_queue)
        self.bind("<Destroy>", self._on_destroy)

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        body = build_scrollable_body(self)

        files_frame = ctk.CTkFrame(body)
        files_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(files_frame, text=t("gif.files_label"),
                     anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        row = ctk.CTkFrame(files_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=8)
        self.files_label = ctk.CTkLabel(row, text=t("common.no_file_selected"), anchor="w")
        self.files_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text=t("common.browse"), width=100, command=self._choose_files).pack(
            side="left", padx=(8, 0))

        enable_file_drop(files_frame, self._set_selected_files, extensions=VIDEO_EXTENSIONS)

        opts_frame = ctk.CTkFrame(body)
        opts_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(opts_frame, text=t("gif.settings_hint"),
                     anchor="w", text_color=("gray40", "gray60")).pack(fill="x", padx=8, pady=(8, 0))

        time_row = ctk.CTkFrame(opts_frame, fg_color="transparent")
        time_row.pack(fill="x", padx=8, pady=4)

        start_col = ctk.CTkFrame(time_row, fg_color="transparent")
        start_col.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(start_col, text=t("gif.start"), anchor="w").pack(fill="x")
        self.start_entry = ctk.CTkEntry(start_col, width=120, placeholder_text="0:00")
        self.start_entry.pack(pady=4)

        duration_col = ctk.CTkFrame(time_row, fg_color="transparent")
        duration_col.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(duration_col, text=t("gif.duration"), anchor="w").pack(fill="x")
        self.duration_entry = ctk.CTkEntry(duration_col, width=120,
                                            placeholder_text=t("gif.duration_placeholder"))
        self.duration_entry.pack(pady=4)

        fps_col = ctk.CTkFrame(time_row, fg_color="transparent")
        fps_col.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(fps_col, text=t("gif.fps"), anchor="w").pack(fill="x")
        self.fps_var = ctk.StringVar(value=DEFAULT_FPS)
        ctk.CTkOptionMenu(fps_col, values=FPS_CHOICES, variable=self.fps_var, width=100).pack(pady=4)

        width_col = ctk.CTkFrame(time_row, fg_color="transparent")
        width_col.pack(side="left")
        ctk.CTkLabel(width_col, text=t("gif.width"), anchor="w").pack(fill="x")
        self.width_var = ctk.StringVar(value=DEFAULT_WIDTH)
        ctk.CTkOptionMenu(width_col, values=WIDTH_CHOICES, variable=self.width_var, width=100).pack(pady=4)

        # --- Aperçu ------------------------------------------------------------
        preview_frame = ctk.CTkFrame(body)
        preview_frame.pack(fill="x", **PAD)
        preview_header = ctk.CTkFrame(preview_frame, fg_color="transparent")
        preview_header.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkLabel(preview_header, text=t("gif.preview_label"), anchor="w").pack(side="left")
        self.preview_btn = ctk.CTkButton(preview_header, text=t("gif.generate_preview"), width=140,
                                          command=self._start_preview)
        self.preview_btn.pack(side="right")

        self.preview_display = ctk.CTkLabel(
            preview_frame, text=t("gif.no_preview"),
            height=180, fg_color=("gray85", "gray20"), corner_radius=6)
        self.preview_display.pack(fill="x", padx=8, pady=8)

        self.preview_status_label = ctk.CTkLabel(preview_frame, text="", anchor="w",
                                                   text_color=("gray40", "gray60"))
        self.preview_status_label.pack(fill="x", padx=8, pady=(0, 8))

        out_frame = ctk.CTkFrame(body)
        out_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(out_frame, text=t("common.destination_folder"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        out_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        out_row.pack(fill="x", padx=8, pady=8)
        self.output_entry = ctk.CTkEntry(out_row)
        self.output_entry.insert(0, os.path.join(DEFAULT_OUTPUT_DIR, t("gif.output_subdir")))
        self.output_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text=t("common.browse"), width=100, command=self._choose_folder).pack(side="left", padx=(8, 0))

        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.pack(fill="x", **PAD)
        self.convert_btn = ctk.CTkButton(btn_frame, text=t("gif.create"), command=self._start_conversion)
        self.convert_btn.pack(side="left")
        ctk.CTkButton(btn_frame, text=t("common.open_folder"), command=self._open_folder).pack(side="right")
        ctk.CTkButton(btn_frame, text=t("common.export_logs"), width=140,
                      command=lambda: export_log_to_file(self.log_box, t("logname.gif"))).pack(side="right", padx=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(body)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", **PAD)

        self.status_label = ctk.CTkLabel(body, text=t("common.ready"), anchor="w")
        self.status_label.pack(fill="x", padx=16)

        self.log_box = ctk.CTkTextbox(body, height=200)
        self.log_box.pack(fill="both", expand=True, **PAD)
        self.log_box.configure(state="disabled")

    # ---------------------------------------------------------- File/dir ---
    def _choose_files(self):
        files = filedialog.askopenfilenames(title=t("common.choose_videos"), filetypes=VIDEO_FILETYPES)
        if files:
            self._set_selected_files(list(files))

    def _set_selected_files(self, files):
        self.selected_files = list(files)
        n = len(self.selected_files)
        label = t("common.one_file_selected") if n == 1 else t("common.n_files_selected", n=n)
        self.files_label.configure(text=label)

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_entry.get() or DEFAULT_OUTPUT_DIR)
        if folder:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)

    def _open_folder(self):
        folder = ensure_dir(self.output_entry.get().strip()
                             or os.path.join(DEFAULT_OUTPUT_DIR, t("gif.output_subdir")))
        os.startfile(folder)

    # ------------------------------------------------------------ Logging ---
    def _log(self, message):
        self.log_queue.put(("log", message))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", payload + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "progress":
                    self.progress_bar.set(payload)
                elif kind == "status":
                    self.status_label.configure(text=payload)
                elif kind == "done":
                    self.convert_btn.configure(state="normal")
                elif kind == "preview_ready":
                    self._load_preview_animation(payload)
                elif kind == "preview_error":
                    self.preview_status_label.configure(text=t("gif.preview_error", error=payload),
                                                         text_color=("#a33333", "#e57373"))
                    self.preview_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ----------------------------------------------------------- Réglages ---
    def _read_clip_settings(self):
        """Lit et valide départ/durée/fps/largeur depuis les champs. Lève ValueError
        (avec un message adapté à afficher tel quel) si un champ est invalide."""
        start_seconds = parse_time_to_seconds(self.start_entry.get())
        if start_seconds < 0:
            raise ValueError(t("gif.negative_start"))

        duration_text = self.duration_entry.get().strip()
        duration_seconds = None
        if duration_text:
            try:
                duration_seconds = float(duration_text)
                if duration_seconds <= 0:
                    raise ValueError
            except ValueError:
                raise ValueError(t("gif.invalid_duration"))

        fps = int(self.fps_var.get())
        width = int(self.width_var.get())
        return start_seconds, duration_seconds, fps, width

    # -------------------------------------------------------- Conversion ---
    def _start_conversion(self):
        if not self.selected_files:
            messagebox.showwarning(t("common.no_file_title"), t("gif.no_file_message"))
            return

        try:
            start_seconds, duration_seconds, fps, width = self._read_clip_settings()
        except ValueError as e:
            messagebox.showwarning(t("gif.invalid_setting_title"), str(e))
            return

        output_dir = ensure_dir(self.output_entry.get().strip()
                                 or os.path.join(DEFAULT_OUTPUT_DIR, t("gif.output_subdir")))

        self.convert_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.status_label.configure(text=t("common.preparing"))

        files = list(self.selected_files)
        self.worker_thread = threading.Thread(
            target=self._run_conversion,
            args=(files, output_dir, start_seconds, duration_seconds, fps, width),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_conversion(self, files, output_dir, start_seconds, duration_seconds, fps, width):
        ffmpeg_exe = bundled_ffmpeg_exe()
        total = len(files)
        errors = 0
        try:
            for i, path in enumerate(files, start=1):
                name = os.path.basename(path)
                self.log_queue.put(("status", t("gif.creating", index=i, total=total, name=name)))
                try:
                    out_path = output_path_for(path, output_dir)
                    video_to_gif(path, out_path, ffmpeg_exe, start_seconds, duration_seconds, fps, width)
                    size = os.path.getsize(out_path)
                    self._log(t("gif.file_result", name=name, output=os.path.basename(out_path),
                                size=format_size(size)))
                except Exception as e:
                    errors += 1
                    message = str(e)
                    tail = "\n".join(message.splitlines()[-15:])
                    self._log(t("gif.file_failed", name=name, error=tail))

                self.log_queue.put(("progress", i / total))

            if errors:
                self.log_queue.put(("status", t("common.finished_with_errors", errors=errors, total=total)))
            else:
                self.log_queue.put(("status", t("common.done")))
                self._log(t("gif.finished"))
        except Exception:
            self.log_queue.put(("status", t("common.unexpected_error_status")))
            self._log(t("common.unexpected_error_log") + "\n" + traceback.format_exc())
        finally:
            self.log_queue.put(("done", None))

    # ------------------------------------------------------------ Aperçu ---
    def _start_preview(self):
        if str(self.preview_btn.cget("state")) == "disabled":
            return  # génération déjà en cours -- évite les générations concurrentes
        if not self.selected_files:
            messagebox.showwarning(t("common.no_file_title"), t("gif.no_file_message"))
            return

        try:
            start_seconds, duration_seconds, fps, width = self._read_clip_settings()
        except ValueError as e:
            messagebox.showwarning(t("gif.invalid_setting_title"), str(e))
            return

        preview_width = min(width, PREVIEW_MAX_WIDTH)
        path = self.selected_files[0]

        self._stop_preview_animation()
        self.preview_btn.configure(state="disabled")
        self.preview_display.configure(text=t("gif.generating_preview"), image=self._blank_preview_image)
        self.preview_status_label.configure(text="", text_color=("gray40", "gray60"))

        threading.Thread(
            target=self._run_preview,
            args=(path, start_seconds, duration_seconds, fps, preview_width),
            daemon=True,
        ).start()

    def _run_preview(self, path, start_seconds, duration_seconds, fps, preview_width):
        ffmpeg_exe = bundled_ffmpeg_exe()
        fd, temp_path = tempfile.mkstemp(suffix="_apercu.gif")
        os.close(fd)
        try:
            video_to_gif(path, temp_path, ffmpeg_exe, start_seconds, duration_seconds, fps, preview_width)
            self.log_queue.put(("preview_ready", temp_path))
        except Exception as e:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            message = str(e).splitlines()[-1] if str(e) else t("gif.unknown_error")
            self.log_queue.put(("preview_error", message))

    def _load_preview_animation(self, gif_path):
        # Supprime l'ancien fichier temporaire d'aperçu (s'il y en avait un) une fois
        # qu'on n'en a plus besoin.
        old_path = self._preview_temp_path
        self._preview_temp_path = gif_path

        try:
            # Ferme explicitement le fichier une fois les frames extraites (chaque frame
            # est déjà une copie indépendante via .copy(), donc ça ne casse rien) --
            # sans ça, PIL garde le fichier ouvert/verrouillé sur Windows et les
            # suppressions ultérieures de ce temp file échouent silencieusement,
            # laissant des fichiers orphelins s'accumuler dans le dossier temp.
            with Image.open(gif_path) as img:
                frames = []
                try:
                    while True:
                        frame = img.convert("RGBA").copy()
                        duration_ms = img.info.get("duration", 100) or 100
                        ctk_image = ctk.CTkImage(light_image=frame, dark_image=frame, size=frame.size)
                        frames.append((ctk_image, duration_ms))
                        img.seek(img.tell() + 1)
                except EOFError:
                    pass
        except Exception as e:
            self.preview_status_label.configure(text=t("gif.preview_unreadable", error=e),
                                                 text_color=("#a33333", "#e57373"))
            self.preview_btn.configure(state="normal")
            return
        finally:
            if old_path and old_path != gif_path:
                try:
                    os.remove(old_path)
                except OSError:
                    pass

        if not frames:
            self.preview_status_label.configure(text=t("gif.preview_empty"),
                                                 text_color=("#a33333", "#e57373"))
            self.preview_btn.configure(state="normal")
            return

        self._preview_frames = frames
        self._preview_frame_index = 0
        self.preview_display.configure(text="")
        self.preview_status_label.configure(
            text=t("gif.preview_ready", frames=len(frames)), text_color=("gray40", "gray60"))
        self.preview_btn.configure(state="normal")
        self._animate_preview_frame()

    def _animate_preview_frame(self):
        if not self._preview_frames or not self.winfo_exists():
            return
        ctk_image, duration_ms = self._preview_frames[self._preview_frame_index]
        self.preview_display.configure(image=ctk_image)
        self._preview_frame_index = (self._preview_frame_index + 1) % len(self._preview_frames)
        self._preview_after_id = self.after(max(int(duration_ms), 20), self._animate_preview_frame)

    def _stop_preview_animation(self):
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None

    def _on_destroy(self, event):
        # <Destroy> se déclenche une fois par widget détruit dans toute la sous-arborescence
        # (widgets internes de CustomTkinter compris, ex. le canvas interne d'un CTkFrame) --
        # PAS uniquement pour self malgré le bind fait sur self. On ne filtre donc pas sur
        # event.widget (ça raterait le nettoyage, comme observé en pratique) ; on protège
        # plutôt avec un drapeau pour n'exécuter le nettoyage qu'une seule fois.
        if getattr(self, "_destroy_cleanup_done", False):
            return
        self._destroy_cleanup_done = True

        self._stop_preview_animation()
        if self._preview_temp_path:
            try:
                os.remove(self._preview_temp_path)
            except OSError:
                pass
