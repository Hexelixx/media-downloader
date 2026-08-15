"""
Onglet : compresseur d'images (par lot, avec préréglages selon la plateforme de destination).
"""

import os
import io
import threading
import queue
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image

from common import (
    PAD, DEFAULT_OUTPUT_DIR, ensure_dir, enable_file_drop, open_image_any,
    RAW_EXTENSIONS, HEIF_EXTENSIONS, build_scrollable_body, export_log_to_file,
)
from i18n import t

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"} | RAW_EXTENSIONS | HEIF_EXTENSIONS


# --------------------------------------------------------------------------- #
#  Logique pure (aucune référence à un widget), testable indépendamment.      #
# --------------------------------------------------------------------------- #

# Préréglages par plateforme : (quality, max_dimension), indexés par identifiant stable
# (indépendant de la langue) -- le menu déroulant, lui, affiche les libellés traduits.
_PRESET_IDS = ["max_quality", "social", "web", "email", "avatar", "custom"]
_PRESET_VALUES = {
    "max_quality": (90, None),
    "social": (75, 1920),
    "web": (65, 1600),
    "email": (60, 1280),
    "avatar": (60, 512),
    "custom": None,
}

PRESET_ORDER = [t(f"imgcomp.preset.{pid}") for pid in _PRESET_IDS]
PRESETS = {label: _PRESET_VALUES[pid] for label, pid in zip(PRESET_ORDER, _PRESET_IDS)}
# Le préréglage "Personnalisé" est un cas testé explicitement (bascule automatique dès
# que l'utilisateur touche un curseur) -> on garde son libellé traduit sous la main.
CUSTOM_PRESET = t("imgcomp.preset.custom")

# Modes de compression du CTkSegmentedButton, comparés par valeur ailleurs dans le code.
MODE_MANUAL = t("imgcomp.mode_manual")
MODE_TARGET = t("imgcomp.mode_target")

KEEP_FORMAT = t("imgcomp.keep_format")
OUTPUT_FORMATS = [KEEP_FORMAT, "JPEG", "WEBP", "PNG"]

_FORMAT_TO_EXT = {
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "PNG": ".png",
}


def _resize_if_needed(img: Image.Image, max_dimension):
    """Redimensionne (downscale seulement) si la plus grande dimension dépasse max_dimension."""
    if not max_dimension:
        return img
    width, height = img.size
    largest = max(width, height)
    if largest <= max_dimension:
        return img
    ratio = max_dimension / float(largest)
    new_size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    return img.resize(new_size, Image.LANCZOS)


def _flatten_to_rgb(img: Image.Image, background=(255, 255, 255)):
    """Aplati une image (avec transparence ou en mode palette) sur un fond uni, en RGB."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, background)
        base.paste(rgba, mask=rgba.split()[3])
        return base
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def resolve_output_format(input_path: str, output_format: str) -> str:
    """Résout 'GARDER' vers le format Pillow correspondant à l'extension d'entrée."""
    output_format = (output_format or "").upper()
    if output_format not in ("GARDER", ""):
        return output_format
    ext = os.path.splitext(input_path)[1].lower().lstrip(".")
    mapping = {
        "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP",
        "bmp": "BMP", "gif": "GIF", "tiff": "TIFF", "tif": "TIFF",
    }
    return mapping.get(ext, "JPEG")


def suggest_output_path(input_path: str, output_dir: str, output_format: str) -> str:
    """Construit un chemin de sortie dans output_dir, avec l'extension adaptée au format choisi."""
    resolved = resolve_output_format(input_path, output_format)
    base = os.path.splitext(os.path.basename(input_path))[0]
    ext = _FORMAT_TO_EXT.get(resolved, os.path.splitext(input_path)[1] or ".jpg")
    return os.path.join(output_dir, base + ext)


def _prepare_image(input_path: str, max_dimension):
    """Ouvre l'image source (formats classiques ou RAW via open_image_any), respecte son
    orientation EXIF, la redimensionne si besoin, et retourne (img, exif_bytes) -- prête à
    encoder. exif_bytes est None si l'image n'a pas de métadonnées EXIF."""
    img = open_image_any(input_path)
    exif_bytes = img.info.get("exif")
    try:
        from PIL import ImageOps
        transposed = ImageOps.exif_transpose(img)
        if transposed is not None:
            img = transposed
            exif_bytes = img.info.get("exif", exif_bytes)
    except Exception:
        pass
    return _resize_if_needed(img, max_dimension), exif_bytes


def _encode_image(img: Image.Image, quality: int, resolved_format: str, exif_bytes=None) -> bytes:
    """Encode img en mémoire selon resolved_format/quality et réinjecte exif_bytes dans le
    fichier de sortie si le format le supporte (JPEG/WEBP/TIFF/PNG), pour ne jamais perdre
    les métadonnées (date de prise de vue, appareil, position GPS...) lors de la compression."""
    buffer = io.BytesIO()
    save_kwargs = {"exif": exif_bytes} if exif_bytes and resolved_format in ("JPEG", "WEBP", "TIFF", "PNG") else {}

    if resolved_format == "JPEG":
        rgb_img = _flatten_to_rgb(img)
        rgb_img.save(buffer, format="JPEG", quality=int(quality), optimize=True, **save_kwargs)
    elif resolved_format == "WEBP":
        save_img = img.convert("RGBA") if img.mode in ("RGBA", "LA") else _flatten_to_rgb(img) if img.mode == "P" else img
        save_img.save(buffer, format="WEBP", quality=int(quality), method=6, **save_kwargs)
    elif resolved_format == "PNG":
        png_img = img
        if png_img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            png_img = png_img.convert("RGBA")
        if quality < 80:
            # Réduction de palette pour diminuer nettement le poids des PNG.
            try:
                if png_img.mode == "RGBA":
                    alpha = png_img.split()[3]
                    quantized = png_img.convert("RGB").quantize(colors=256, method=Image.MEDIANCUT)
                    quantized = quantized.convert("RGBA")
                    quantized.putalpha(alpha)
                    png_img = quantized
                else:
                    png_img = png_img.convert("RGB").quantize(colors=256, method=Image.MEDIANCUT)
            except Exception:
                pass
        png_img.save(buffer, format="PNG", optimize=True, **save_kwargs)
    else:
        # Format non spécifiquement géré (BMP, GIF, TIFF...) : sauvegarde directe optimisée.
        img.save(buffer, format=resolved_format, **save_kwargs)

    return buffer.getvalue()


def compress_image(input_path: str, output_path: str, quality: int, max_dimension, output_format: str) -> dict:
    """Compresse une image à une qualité donnée.

    output_format dans {'JPEG', 'WEBP', 'PNG', 'GARDER'} ('GARDER' = même format que l'entrée).
    Redimensionne (downscale seulement, jamais upscale) si max_dimension est fourni et que la
    plus grande dimension le dépasse, en conservant le ratio. Préserve les métadonnées EXIF
    d'origine (date, appareil, GPS...) quand le format de sortie le permet.

    Retourne un dict : {'input_size': int, 'output_size': int, 'width': int, 'height': int,
    'format': str}.
    """
    input_size = os.path.getsize(input_path)
    resolved_format = resolve_output_format(input_path, output_format)

    img, exif_bytes = _prepare_image(input_path, max_dimension)
    try:
        # Traitement entièrement en mémoire : input_path et output_path peuvent être
        # le même fichier sans risque de corruption.
        data = _encode_image(img, quality, resolved_format, exif_bytes)
        final_width, final_height = img.size
    finally:
        img.close()

    ensure_dir(os.path.dirname(output_path) or ".")
    with open(output_path, "wb") as f:
        f.write(data)

    output_size = os.path.getsize(output_path)

    return {
        "input_size": input_size,
        "output_size": output_size,
        "width": final_width,
        "height": final_height,
        "format": resolved_format,
    }


def compress_image_to_target(input_path: str, output_path: str, target_bytes: int, output_format: str,
                              max_dimension=None, min_quality: int = 10, max_quality: int = 95) -> dict:
    """Compresse une image en cherchant automatiquement la qualité (par dichotomie), puis si
    besoin en réduisant progressivement la dimension, pour approcher au mieux target_bytes
    sans le dépasser. Préserve les métadonnées EXIF comme compress_image().

    Retourne le même dict que compress_image(), plus 'quality_used': int et
    'target_reached': bool (False si même la qualité/dimension minimales dépassent encore
    target_bytes -- le meilleur résultat trouvé est quand même écrit sur disque).
    """
    input_size = os.path.getsize(input_path)
    resolved_format = resolve_output_format(input_path, output_format)

    dimension = max_dimension
    best_data, best_quality, best_dims = None, None, None

    for _dimension_round in range(6):
        img, exif_bytes = _prepare_image(input_path, dimension)
        try:
            lo, hi = min_quality, max_quality
            data = _encode_image(img, hi, resolved_format, exif_bytes)
            best_data, best_quality, best_dims = data, hi, img.size

            if len(data) > target_bytes:
                # Dichotomie sur la qualité entre min_quality et max_quality.
                while lo <= hi:
                    mid = (lo + hi) // 2
                    data = _encode_image(img, mid, resolved_format, exif_bytes)
                    if len(data) <= target_bytes:
                        best_data, best_quality, best_dims = data, mid, img.size
                        lo = mid + 1
                    else:
                        hi = mid - 1
        finally:
            current_dims = img.size
            img.close()

        if len(best_data) <= target_bytes:
            break

        # Même à qualité minimale c'est encore trop lourd : réduit la dimension et retente.
        dimension = max(200, int((dimension or max(current_dims)) * 0.85))

    ensure_dir(os.path.dirname(output_path) or ".")
    with open(output_path, "wb") as f:
        f.write(best_data)

    output_size = os.path.getsize(output_path)

    return {
        "input_size": input_size,
        "output_size": output_size,
        "width": best_dims[0],
        "height": best_dims[1],
        "format": resolved_format,
        "quality_used": best_quality,
        "target_reached": output_size <= target_bytes,
    }


def format_size(num_bytes: int) -> str:
    """Formatte une taille en octets en chaîne lisible (Ko / Mo)."""
    if num_bytes < 1024:
        return f"{num_bytes} {t('common.unit_bytes')}"
    kb = num_bytes / 1024.0
    if kb < 1024:
        return f"{kb:.0f} {t('common.unit_kb')}"
    mb = kb / 1024.0
    return f"{mb:.1f} {t('common.unit_mb')}"


def reduction_percent(input_size: int, output_size: int) -> float:
    if input_size <= 0:
        return 0.0
    return (1 - (output_size / input_size)) * 100.0


# --------------------------------------------------------------------------- #
#  Interface CustomTkinter                                                    #
# --------------------------------------------------------------------------- #

class ImageCompressorTab(ctk.CTkFrame):
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
        ctk.CTkLabel(files_frame, text=t("imgcomp.files_label"),
                     anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        row = ctk.CTkFrame(files_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=8)
        self.files_label = ctk.CTkLabel(row, text=t("common.no_file_selected"), anchor="w")
        self.files_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text=t("imgcomp.choose_images"), width=160,
                      command=self._choose_files).pack(side="left", padx=(8, 0))

        enable_file_drop(files_frame, self._set_selected_files, extensions=IMAGE_EXTENSIONS)

        mode_frame = ctk.CTkFrame(body)
        mode_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(mode_frame, text=t("imgcomp.mode_label"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        self.mode_var = ctk.StringVar(value=MODE_MANUAL)
        self.mode_selector = ctk.CTkSegmentedButton(
            mode_frame, values=[MODE_MANUAL, MODE_TARGET],
            variable=self.mode_var, command=self._on_mode_change)
        self.mode_selector.pack(fill="x", padx=8, pady=8)

        opts_frame = ctk.CTkFrame(body)
        opts_frame.pack(fill="x", **PAD)

        preset_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        preset_col.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(preset_col, text=t("imgcomp.preset_label"), anchor="w").pack(fill="x")
        self.preset_var = ctk.StringVar(value=PRESET_ORDER[1])
        self.preset_menu = ctk.CTkOptionMenu(preset_col, values=PRESET_ORDER, variable=self.preset_var,
                                              command=self._on_preset_change)
        self.preset_menu.pack(fill="x", pady=4)

        # Conteneur stable dans lequel manual_frame/target_frame sont interchangés (évite
        # que l'ordre d'affichage ne saute par rapport à fmt_row/out_frame en dessous).
        self.mode_body = ctk.CTkFrame(opts_frame, fg_color="transparent")
        self.mode_body.pack(fill="x")

        # --- Mode "Qualité manuelle" : slider qualité + dimension max ---------------
        self.manual_frame = ctk.CTkFrame(self.mode_body, fg_color="transparent")
        self.manual_frame.pack(fill="x")

        sliders_row = ctk.CTkFrame(self.manual_frame, fg_color="transparent")
        sliders_row.pack(fill="x", padx=8, pady=4)

        quality_col = ctk.CTkFrame(sliders_row, fg_color="transparent")
        quality_col.pack(side="left", fill="x", expand=True)
        qlabel_row = ctk.CTkFrame(quality_col, fg_color="transparent")
        qlabel_row.pack(fill="x")
        ctk.CTkLabel(qlabel_row, text=t("imgcomp.quality"), anchor="w").pack(side="left")
        self.quality_value_label = ctk.CTkLabel(qlabel_row, text="75")
        self.quality_value_label.pack(side="right")
        self.quality_slider = ctk.CTkSlider(quality_col, from_=1, to=100, number_of_steps=99,
                                             command=self._on_quality_slide)
        self.quality_slider.set(75)
        self.quality_slider.pack(fill="x", pady=4)

        dim_col = ctk.CTkFrame(sliders_row, fg_color="transparent")
        dim_col.pack(side="left", padx=(16, 0))
        ctk.CTkLabel(dim_col, text=t("imgcomp.max_dimension"), anchor="w").pack(fill="x")
        self.max_dimension_entry = ctk.CTkEntry(dim_col, width=140)
        self.max_dimension_entry.insert(0, "1920")
        self.max_dimension_entry.pack(pady=4)
        self.max_dimension_entry.bind("<KeyRelease>", self._on_manual_edit)

        # --- Mode "Taille cible" : taille voulue + unité -----------------------------
        self.target_frame = ctk.CTkFrame(self.mode_body, fg_color="transparent")
        # .pack() géré par _on_mode_change() -- caché par défaut (mode manuel au départ)

        ctk.CTkLabel(self.target_frame, text=t("imgcomp.target_size"), anchor="w").pack(
            side="left", padx=8, pady=8)
        self.target_size_entry = ctk.CTkEntry(self.target_frame, width=100,
                                               placeholder_text=t("imgcomp.target_placeholder"))
        self.target_size_entry.insert(0, "500")
        self.target_size_entry.pack(side="left", padx=(0, 8), pady=8)
        self.target_unit_var = ctk.StringVar(value=t("common.unit_kb"))
        ctk.CTkOptionMenu(self.target_frame, values=[t("common.unit_kb"), t("common.unit_mb")],
                           variable=self.target_unit_var, width=80).pack(side="left", pady=8)
        ctk.CTkLabel(self.target_frame,
                     text=t("imgcomp.target_hint"),
                     anchor="w", text_color=("gray40", "gray60")).pack(side="left", padx=8, pady=8)

        fmt_row = ctk.CTkFrame(opts_frame, fg_color="transparent")
        fmt_row.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(fmt_row, text=t("imgcomp.output_format"), anchor="w").pack(side="left")
        self.format_var = ctk.StringVar(value=OUTPUT_FORMATS[0])
        self.format_menu = ctk.CTkOptionMenu(fmt_row, values=OUTPUT_FORMATS, variable=self.format_var)
        self.format_menu.pack(side="left", padx=(8, 0))

        out_frame = ctk.CTkFrame(body)
        out_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(out_frame, text=t("common.destination_folder"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        out_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        out_row.pack(fill="x", padx=8, pady=8)
        self.output_entry = ctk.CTkEntry(out_row)
        self.output_entry.insert(0, os.path.join(DEFAULT_OUTPUT_DIR, t("imgcomp.output_subdir")))
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
                      command=lambda: export_log_to_file(self.log_box, t("logname.images"))).pack(side="right", padx=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(body)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", **PAD)

        self.status_label = ctk.CTkLabel(body, text=t("common.ready"), anchor="w")
        self.status_label.pack(fill="x", padx=16)

        self.log_box = ctk.CTkTextbox(body, height=200)
        self.log_box.pack(fill="both", expand=True, **PAD)
        self.log_box.configure(state="disabled")

        # Applique le préréglage initial (index 1 : réseaux sociaux) à la vue.
        self._apply_preset_to_widgets(self.preset_var.get())

    # ------------------------------------------------------------- Preset ---
    def _apply_preset_to_widgets(self, preset_name):
        preset = PRESETS.get(preset_name)
        if preset is None:
            return
        quality, max_dimension = preset
        self.quality_slider.set(quality)
        self.quality_value_label.configure(text=str(quality))
        self.max_dimension_entry.delete(0, "end")
        if max_dimension:
            self.max_dimension_entry.insert(0, str(max_dimension))

    def _on_preset_change(self, value):
        self._apply_preset_to_widgets(value)

    def _on_mode_change(self, value):
        if value == MODE_TARGET:
            self.manual_frame.pack_forget()
            self.target_frame.pack(fill="x")
        else:
            self.target_frame.pack_forget()
            self.manual_frame.pack(fill="x")

    def _on_quality_slide(self, value):
        self.quality_value_label.configure(text=str(int(value)))
        self._switch_to_custom()

    def _on_manual_edit(self, _event=None):
        self._switch_to_custom()

    def _switch_to_custom(self):
        if self.preset_var.get() != CUSTOM_PRESET:
            self.preset_var.set(CUSTOM_PRESET)

    # ---------------------------------------------------------- File/dir ---
    def _choose_files(self):
        filetypes = [
            (t("imgcomp.filetype_images"), "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff"),
            (t("imgcomp.filetype_raw"), "*.cr2 *.cr3 *.nef *.arw *.dng *.orf *.rw2 *.raf *.pef *.srw *.raw"),
            (t("imgcomp.filetype_heic"), "*.heic *.heif"),
            (t("common.all_files"), "*.*"),
        ]
        files = filedialog.askopenfilenames(title=t("imgcomp.choose_images_title"), filetypes=filetypes)
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
                             or os.path.join(DEFAULT_OUTPUT_DIR, t("imgcomp.output_subdir")))
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
            messagebox.showwarning(t("imgcomp.no_image_title"), t("imgcomp.no_image_message"))
            return

        output_dir = ensure_dir(self.output_entry.get().strip()
                                 or os.path.join(DEFAULT_OUTPUT_DIR, t("imgcomp.output_subdir")))

        is_target_mode = self.mode_var.get() == MODE_TARGET
        quality = int(self.quality_slider.get())
        dim_text = self.max_dimension_entry.get().strip()
        max_dimension = None
        if dim_text:
            try:
                max_dimension = int(dim_text)
                if max_dimension <= 0:
                    max_dimension = None
            except ValueError:
                messagebox.showwarning(t("imgcomp.invalid_dimension_title"),
                                        t("imgcomp.invalid_dimension_message"))
                return

        target_bytes = None
        if is_target_mode:
            try:
                target_value = float(self.target_size_entry.get().strip().replace(",", "."))
                if target_value <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(t("imgcomp.invalid_size_title"), t("imgcomp.invalid_size_message"))
                return
            multiplier = 1024 * 1024 if self.target_unit_var.get() == t("common.unit_mb") else 1024
            target_bytes = int(target_value * multiplier)

        output_format = "GARDER" if self.format_var.get() == KEEP_FORMAT else self.format_var.get()

        self.compress_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.status_label.configure(text=t("common.preparing"))
        self.cancel_flag.clear()

        files = list(self.selected_files)
        self.worker_thread = threading.Thread(
            target=self._run_compression,
            args=(files, output_dir, quality, max_dimension, output_format, target_bytes),
            daemon=True,
        )
        self.worker_thread.start()

    def _cancel_compression(self):
        self.cancel_flag.set()
        self._log(t("common.cancel_requested"))

    def _run_compression(self, files, output_dir, quality, max_dimension, output_format, target_bytes):
        total = len(files)
        total_input = 0
        total_output = 0
        errors = 0
        try:
            if target_bytes:
                self._log(t("imgcomp.start_target", total=total, target=format_size(target_bytes)))
            else:
                self._log(t("imgcomp.start", total=total))
            for i, path in enumerate(files, start=1):
                if self.cancel_flag.is_set():
                    self._log(t("common.cancelled_by_user"))
                    break

                name = os.path.basename(path)
                self.log_queue.put(("status", t("imgcomp.compressing", name=name, index=i, total=total)))

                try:
                    out_path = suggest_output_path(path, output_dir, output_format)
                    if target_bytes:
                        result = compress_image_to_target(path, out_path, target_bytes, output_format, max_dimension)
                    else:
                        result = compress_image(path, out_path, quality, max_dimension, output_format)
                    total_input += result["input_size"]
                    total_output += result["output_size"]
                    pct = reduction_percent(result["input_size"], result["output_size"])
                    sign = "-" if pct >= 0 else "+"
                    extra = ""
                    if target_bytes:
                        extra = t("imgcomp.quality_used", quality=result["quality_used"])
                        if not result["target_reached"]:
                            extra += t("imgcomp.target_missed")
                    self._log(t(
                        "imgcomp.file_result", name=name,
                        before=format_size(result["input_size"]),
                        after=format_size(result["output_size"]),
                        sign=sign, pct=f"{abs(pct):.0f}", extra=extra,
                    ))
                except Exception as e:
                    errors += 1
                    self._log(t("imgcomp.file_failed", name=name, error=e))

                self.log_queue.put(("progress", i / total))

            if not self.cancel_flag.is_set():
                if total_input > 0:
                    overall_pct = reduction_percent(total_input, total_output)
                    overall_sign = "-" if overall_pct >= 0 else "+"
                    self._log(t(
                        "imgcomp.total", before=format_size(total_input),
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
