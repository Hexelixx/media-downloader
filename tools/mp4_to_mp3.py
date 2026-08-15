"""
Onglet : conversion locale vidéo -> audio (extraction de la piste son), plusieurs formats.
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

# Formats audio de sortie disponibles : codec ffmpeg, extension, et si le bitrate
# s'applique (formats compressés/"lossy") ou pas (formats sans perte -> pas de bitrate).
# Indexés par un identifiant stable, PAS par le libellé affiché : "WAV (sans perte)"
# devient "WAV (lossless)" en anglais, et un dictionnaire dont les clés changent avec la
# langue de l'interface est une bombe à retardement.
_AUDIO_FORMAT_IDS = ["mp3", "aac", "ogg", "wav", "flac"]
_AUDIO_FORMAT_SPECS = {
    "mp3": {"codec": "libmp3lame", "ext": ".mp3", "lossy": True},
    "aac": {"codec": "aac", "ext": ".m4a", "lossy": True},
    "ogg": {"codec": "libvorbis", "ext": ".ogg", "lossy": True},
    "wav": {"codec": "pcm_s16le", "ext": ".wav", "lossy": False},
    "flac": {"codec": "flac", "ext": ".flac", "lossy": False},
}

# Le menu déroulant, lui, travaille en libellés traduits -> table libellé -> spec.
AUDIO_FORMAT_ORDER = [t(f"audio.format.{fid}") for fid in _AUDIO_FORMAT_IDS]
AUDIO_FORMATS = {
    label: _AUDIO_FORMAT_SPECS[fid]
    for label, fid in zip(AUDIO_FORMAT_ORDER, _AUDIO_FORMAT_IDS)
}

BITRATE_CHOICES = ["320 kbps", "192 kbps", "128 kbps"]
DEFAULT_BITRATE = "192 kbps"


# ------------------------------------------------------------------ Logic ---
def extract_audio(input_path: str, output_path: str, codec: str, ffmpeg_exe: str, bitrate_kbps: int = None) -> None:
    """Lance ffmpeg pour extraire la piste audio de input_path vers output_path avec le
    codec donné. bitrate_kbps est ignoré pour les codecs sans perte (WAV/FLAC). Crée le
    dossier de destination si besoin (filet de sécurité, indépendant de l'appelant). Lève
    une exception (avec le stderr de ffmpeg dans le message) si ffmpeg échoue."""
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        ensure_dir(out_dir)

    cmd = [ffmpeg_exe, "-y", "-i", input_path, "-vn", "-acodec", codec]
    if bitrate_kbps:
        cmd += ["-b:a", f"{bitrate_kbps}k"]
    cmd.append(output_path)

    result = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            t("audio.ffmpeg_failed", code=result.returncode, path=input_path, stderr=result.stderr)
        )


def output_path_for(input_path: str, output_dir: str, ext: str) -> str:
    """Calcule le chemin de sortie pour un fichier d'entrée donné, avec l'extension du
    format audio choisi."""
    base = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(output_dir, base + ext)


def parse_bitrate(choice: str) -> int:
    """Convertit un choix de menu ("192 kbps") en entier kbps (192)."""
    return int(choice.split()[0])


# --------------------------------------------------------------------- UI ---
class Mp4ToMp3Tab(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.log_queue = queue.Queue()
        self.convert_thread = None
        self.selected_files = []

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        body = build_scrollable_body(self)

        files_frame = ctk.CTkFrame(body)
        files_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(files_frame, text=t("audio.files_label"),
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

        format_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        format_col.pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(format_col, text=t("audio.format")).pack(anchor="w")
        self.audio_format_var = ctk.StringVar(value=AUDIO_FORMAT_ORDER[0])
        self.audio_format_menu = ctk.CTkOptionMenu(format_col, values=AUDIO_FORMAT_ORDER,
                                                     variable=self.audio_format_var,
                                                     command=self._on_format_change)
        self.audio_format_menu.pack(anchor="w", pady=4)

        self.bitrate_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        self.bitrate_col.pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(self.bitrate_col, text=t("audio.bitrate")).pack(anchor="w")
        self.bitrate_var = ctk.StringVar(value=DEFAULT_BITRATE)
        self.bitrate_menu = ctk.CTkOptionMenu(self.bitrate_col, values=BITRATE_CHOICES, variable=self.bitrate_var)
        self.bitrate_menu.pack(anchor="w", pady=4)

        out_frame = ctk.CTkFrame(body)
        out_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(out_frame, text=t("common.destination_folder"), anchor="w").pack(
            fill="x", padx=8, pady=(8, 0))
        out_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        out_row.pack(fill="x", padx=8, pady=8)
        self.output_entry = ctk.CTkEntry(out_row)
        self.output_entry.insert(0, os.path.join(DEFAULT_OUTPUT_DIR, t("audio.output_subdir")))
        self.output_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text=t("common.browse"), width=100, command=self._choose_folder).pack(
            side="left", padx=(8, 0))

        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.pack(fill="x", **PAD)
        self.convert_btn = ctk.CTkButton(btn_frame, text=t("common.convert"), command=self._start_conversion)
        self.convert_btn.pack(side="left")
        ctk.CTkButton(btn_frame, text=t("common.open_folder"), command=self._open_folder).pack(side="right")
        ctk.CTkButton(btn_frame, text=t("common.export_logs"), width=140,
                      command=lambda: export_log_to_file(self.log_box, t("logname.audio"))).pack(side="right", padx=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(body)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", **PAD)

        self.status_label = ctk.CTkLabel(body, text=t("common.ready"), anchor="w")
        self.status_label.pack(fill="x", padx=16)

        self.log_box = ctk.CTkTextbox(body, height=200)
        self.log_box.pack(fill="both", expand=True, **PAD)
        self.log_box.configure(state="disabled")

    def _on_format_change(self, value):
        if AUDIO_FORMATS[value]["lossy"]:
            self.bitrate_col.pack(side="left", padx=8, pady=8)
        else:
            self.bitrate_col.pack_forget()

    def _choose_files(self):
        files = filedialog.askopenfilenames(title=t("audio.choose_videos_title"), filetypes=VIDEO_FILETYPES)
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
                             or os.path.join(DEFAULT_OUTPUT_DIR, t("audio.output_subdir")))
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
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # -------------------------------------------------------- Conversion ---
    def _start_conversion(self):
        if not self.selected_files:
            messagebox.showwarning(t("common.no_file_title"), t("audio.no_file_message"))
            return

        output_dir = ensure_dir(
            self.output_entry.get().strip() or os.path.join(DEFAULT_OUTPUT_DIR, t("audio.output_subdir")))
        audio_format = self.audio_format_var.get()
        format_info = AUDIO_FORMATS[audio_format]
        bitrate_kbps = parse_bitrate(self.bitrate_var.get()) if format_info["lossy"] else None
        files = list(self.selected_files)

        self.convert_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.status_label.configure(text=t("common.preparing"))

        self.convert_thread = threading.Thread(
            target=self._run_conversion, args=(files, output_dir, format_info, bitrate_kbps), daemon=True)
        self.convert_thread.start()

    def _run_conversion(self, files, output_dir, format_info, bitrate_kbps):
        ffmpeg_exe = bundled_ffmpeg_exe()
        total = len(files)
        errors = 0
        try:
            for i, input_path in enumerate(files, start=1):
                name = os.path.basename(input_path)
                progress_message = t("audio.processing", index=i, total=total, name=name)
                self.log_queue.put(("status", progress_message))
                self._log(progress_message)
                out_path = output_path_for(input_path, output_dir, format_info["ext"])
                try:
                    extract_audio(input_path, out_path, format_info["codec"], ffmpeg_exe, bitrate_kbps)
                except Exception as e:
                    errors += 1
                    message = str(e)
                    tail = "\n".join(message.splitlines()[-15:])
                    self._log(t("audio.file_failed", name=name, error=tail))
                self.log_queue.put(("progress", i / total))

            if errors:
                summary = t("common.finished_with_errors", errors=errors, total=total)
                self.log_queue.put(("status", summary))
                self._log(summary)
            else:
                self.log_queue.put(("status", t("common.done")))
                self._log(t("audio.finished"))
        except Exception:
            self.log_queue.put(("status", t("common.unexpected_error_status")))
            self._log(t("common.unexpected_error_log") + "\n" + traceback.format_exc())
        finally:
            self.log_queue.put(("done", None))
