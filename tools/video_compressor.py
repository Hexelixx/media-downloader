"""
Onglet : compresseur de vidéo (H.264/CRF + redimensionnement optionnel, par lot, avec
préréglages selon la plateforme de destination -- même esprit que le compresseur d'images).
"""

import os
import subprocess
import threading
import queue
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox

from common import (
    PAD, DEFAULT_OUTPUT_DIR, bundled_ffmpeg_exe, ensure_dir, enable_file_drop,
    build_scrollable_body, export_log_to_file,
)
from i18n import t

VIDEO_FILETYPES = [
    (t("common.video_files"), "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.mpg *.mpeg"),
    (t("common.all_files"), "*.*"),
]
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg"}

# Préréglages par plateforme : (crf, max_width). CRF ffmpeg/x264 : plus petit = meilleure
# qualité/fichier plus lourd (18 quasi sans perte visible, 28+ nettement compressé).
# Indexés par identifiant stable, comme dans le compresseur d'images -- les libellés
# affichés, eux, changent avec la langue.
_PRESET_IDS = ["max_quality", "social", "web", "email", "custom"]
_PRESET_VALUES = {
    "max_quality": (18, None),
    "social": (28, 1280),
    "web": (26, 1920),
    "email": (30, 854),
    "custom": None,
}

PRESET_ORDER = [t(f"vidcomp.preset.{pid}") for pid in _PRESET_IDS]
PRESETS = {label: _PRESET_VALUES[pid] for label, pid in zip(PRESET_ORDER, _PRESET_IDS)}
CUSTOM_PRESET = t("vidcomp.preset.custom")

AUDIO_BITRATE_KBPS = 128


# --------------------------------------------------------------------------- #
#  Logique pure (aucune référence à un widget), testable indépendamment.      #
# --------------------------------------------------------------------------- #

def compress_video(input_path: str, output_path: str, crf: int, max_width, ffmpeg_exe: str) -> None:
    """Compresse une vidéo via ffmpeg (H.264/libx264, qualité pilotée par CRF).

    Redimensionne (downscale seulement, jamais upscale, hauteur ajustée automatiquement
    en conservant le ratio) si max_width est fourni et que la largeur le dépasse. La piste
    audio est ré-encodée en AAC 128 kbps (léger, universellement compatible). Crée le
    dossier de destination si besoin. Lève une exception (avec le stderr de ffmpeg dans
    le message) si ffmpeg échoue.
    """
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        ensure_dir(out_dir)

    cmd = [ffmpeg_exe, "-y", "-i", input_path, "-c:v", "libx264", "-crf", str(int(crf)), "-preset", "medium"]
    if max_width:
        # scale='min(largeur,iw)':-2 -> ne réduit que si l'image est plus large, jamais
        # d'agrandissement ; -2 laisse ffmpeg calculer une hauteur paire (requis par H.264).
        cmd += ["-vf", f"scale='min({int(max_width)},iw)':-2"]
    cmd += ["-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_KBPS}k", "-movflags", "+faststart", output_path]

    result = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            t("vidcomp.ffmpeg_failed", code=result.returncode, path=input_path, stderr=result.stderr)
        )


def output_path_for(input_path: str, output_dir: str) -> str:
    """Calcule le chemin de sortie .mp4 pour un fichier d'entrée donné."""
    base = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(output_dir, base + t("vidcomp.file_suffix") + ".mp4")


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} {t('common.unit_bytes')}"
    kb = num_bytes / 1024.0
    if kb < 1024:
        return f"{kb:.0f} {t('common.unit_kb')}"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.1f} {t('common.unit_mb')}"
    return f"{mb / 1024.0:.2f} {t('common.unit_gb')}"


def reduction_percent(input_size: int, output_size: int) -> float:
    if input_size <= 0:
        return 0.0
    return (1 - (output_size / input_size)) * 100.0


# --------------------------------------------------------------------------- #
#  Interface CustomTkinter                                                    #
# --------------------------------------------------------------------------- #

class VideoCompressorTab(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.cancel_flag = threading.Event()
        self.selected_files = []

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        body = build_scrollable_body(self)

        files_frame = ctk.CTkFrame(body)
        files_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(files_frame, text=t("vidcomp.files_label"),
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

        preset_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        preset_col.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(preset_col, text=t("vidcomp.preset_label"), anchor="w").pack(fill="x")
        self.preset_var = ctk.StringVar(value=PRESET_ORDER[1])
        self.preset_menu = ctk.CTkOptionMenu(preset_col, values=PRESET_ORDER, variable=self.preset_var,
                                              command=self._on_preset_change)
        self.preset_menu.pack(fill="x", pady=4)

        sliders_row = ctk.CTkFrame(opts_frame, fg_color="transparent")
        sliders_row.pack(fill="x", padx=8, pady=4)

        crf_col = ctk.CTkFrame(sliders_row, fg_color="transparent")
        crf_col.pack(side="left", fill="x", expand=True)
        crf_label_row = ctk.CTkFrame(crf_col, fg_color="transparent")
        crf_label_row.pack(fill="x")
        ctk.CTkLabel(crf_label_row, text=t("vidcomp.crf"),
                     anchor="w").pack(side="left")
        self.crf_value_label = ctk.CTkLabel(crf_label_row, text="28")
        self.crf_value_label.pack(side="right")
        self.crf_slider = ctk.CTkSlider(crf_col, from_=15, to=40, number_of_steps=25,
                                         command=self._on_crf_slide)
        self.crf_slider.set(28)
        self.crf_slider.pack(fill="x", pady=4)

        width_col = ctk.CTkFrame(sliders_row, fg_color="transparent")
        width_col.pack(side="left", padx=(16, 0))
        ctk.CTkLabel(width_col, text=t("vidcomp.max_width"), anchor="w").pack(fill="x")
        self.max_width_entry = ctk.CTkEntry(width_col, width=140)
        self.max_width_entry.insert(0, "1280")
        self.max_width_entry.pack(pady=4)
        self.max_width_entry.bind("<KeyRelease>", self._on_manual_edit)

        out_frame = ctk.CTkFrame(body)
        out_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(out_frame, text=t("common.destination_folder"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        out_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        out_row.pack(fill="x", padx=8, pady=8)
        self.output_entry = ctk.CTkEntry(out_row)
        self.output_entry.insert(0, os.path.join(DEFAULT_OUTPUT_DIR, t("vidcomp.output_subdir")))
        self.output_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text=t("common.browse"), width=100, command=self._choose_folder).pack(side="left", padx=(8, 0))

        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.pack(fill="x", **PAD)
        self.compress_btn = ctk.CTkButton(btn_frame, text=t("common.compress"), command=self._start_compression)
        self.compress_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btn_frame, text=t("common.cancel"), fg_color="#a33333", hover_color="#822222",
                                         command=self._cancel_compression, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text=t("common.open_folder"), command=self._open_folder).pack(side="right")
        ctk.CTkButton(btn_frame, text=t("common.export_logs"), width=140,
                      command=lambda: export_log_to_file(self.log_box, t("logname.video"))).pack(side="right", padx=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(body)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", **PAD)

        self.status_label = ctk.CTkLabel(body, text=t("vidcomp.ready"), anchor="w")
        self.status_label.pack(fill="x", padx=16)

        self.log_box = ctk.CTkTextbox(body, height=200)
        self.log_box.pack(fill="both", expand=True, **PAD)
        self.log_box.configure(state="disabled")

        self._apply_preset_to_widgets(self.preset_var.get())

    # ------------------------------------------------------------- Preset ---
    def _apply_preset_to_widgets(self, preset_name):
        preset = PRESETS.get(preset_name)
        if preset is None:
            return
        crf, max_width = preset
        self.crf_slider.set(crf)
        self.crf_value_label.configure(text=str(crf))
        self.max_width_entry.delete(0, "end")
        if max_width:
            self.max_width_entry.insert(0, str(max_width))

    def _on_preset_change(self, value):
        self._apply_preset_to_widgets(value)

    def _on_crf_slide(self, value):
        self.crf_value_label.configure(text=str(int(value)))
        self._switch_to_custom()

    def _on_manual_edit(self, _event=None):
        self._switch_to_custom()

    def _switch_to_custom(self):
        if self.preset_var.get() != CUSTOM_PRESET:
            self.preset_var.set(CUSTOM_PRESET)

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
                             or os.path.join(DEFAULT_OUTPUT_DIR, t("vidcomp.output_subdir")))
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
                    self.compress_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # -------------------------------------------------------- Compression ---
    def _start_compression(self):
        if not self.selected_files:
            messagebox.showwarning(t("vidcomp.no_video_title"), t("vidcomp.no_video_message"))
            return

        output_dir = ensure_dir(self.output_entry.get().strip()
                                 or os.path.join(DEFAULT_OUTPUT_DIR, t("vidcomp.output_subdir")))

        crf = int(self.crf_slider.get())
        width_text = self.max_width_entry.get().strip()
        max_width = None
        if width_text:
            try:
                max_width = int(width_text)
                if max_width <= 0:
                    max_width = None
            except ValueError:
                messagebox.showwarning(t("vidcomp.invalid_width_title"), t("vidcomp.invalid_width_message"))
                return

        self.compress_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.status_label.configure(text=t("common.preparing"))
        self.cancel_flag.clear()

        files = list(self.selected_files)
        self.worker_thread = threading.Thread(
            target=self._run_compression, args=(files, output_dir, crf, max_width), daemon=True)
        self.worker_thread.start()

    def _cancel_compression(self):
        self.cancel_flag.set()
        self._log(t("vidcomp.cancel_requested"))

    def _run_compression(self, files, output_dir, crf, max_width):
        ffmpeg_exe = bundled_ffmpeg_exe()
        total = len(files)
        total_input = 0
        total_output = 0
        errors = 0
        try:
            self._log(t("vidcomp.start", total=total))
            for i, path in enumerate(files, start=1):
                if self.cancel_flag.is_set():
                    self._log(t("common.cancelled_by_user"))
                    break

                name = os.path.basename(path)
                self.log_queue.put(("status", t("vidcomp.compressing", name=name, index=i, total=total)))

                try:
                    out_path = output_path_for(path, output_dir)
                    input_size = os.path.getsize(path)
                    compress_video(path, out_path, crf, max_width, ffmpeg_exe)
                    output_size = os.path.getsize(out_path)
                    total_input += input_size
                    total_output += output_size
                    pct = reduction_percent(input_size, output_size)
                    sign = "-" if pct >= 0 else "+"
                    self._log(t(
                        "vidcomp.file_result", name=name, before=format_size(input_size),
                        after=format_size(output_size), sign=sign, pct=f"{abs(pct):.0f}",
                    ))
                except Exception as e:
                    errors += 1
                    message = str(e)
                    tail = "\n".join(message.splitlines()[-15:])
                    self._log(t("vidcomp.file_failed", name=name, error=tail))

                self.log_queue.put(("progress", i / total))

            if not self.cancel_flag.is_set():
                if total_input > 0:
                    overall_pct = reduction_percent(total_input, total_output)
                    overall_sign = "-" if overall_pct >= 0 else "+"
                    self._log(t(
                        "vidcomp.total", before=format_size(total_input),
                        after=format_size(total_output), sign=overall_sign,
                        pct=f"{abs(overall_pct):.0f}", errors=errors,
                    ))
                self.log_queue.put(("status", t("common.done")))
            else:
                self.log_queue.put(("status", t("common.stopped_cancelled")))
        except Exception:
            self.log_queue.put(("status", t("common.unexpected_error_status")))
            self._log(t("common.unexpected_error_log") + "\n" + traceback.format_exc())
        finally:
            self.log_queue.put(("done", None))
