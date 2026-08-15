"""
Onglet : convertisseur de formats d'image (PNG/JPEG/WEBP/BMP/GIF/TIFF/ICO), formats
RAW d'appareil photo (.cr2/.nef/.arw/.dng/...) en entrée, et PDF (image(s) -> PDF,
PDF -> images).
"""

import os
import threading
import queue
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image
import pymupdf

from common import (
    PAD, DEFAULT_OUTPUT_DIR, ensure_dir, enable_file_drop, open_image_any,
    RAW_EXTENSIONS, HEIF_EXTENSIONS, build_scrollable_body, export_log_to_file,
)
from i18n import t

RASTER_FORMATS = ["PNG", "JPEG", "WEBP", "BMP", "GIF", "TIFF", "ICO"]
ALL_TARGET_FORMATS = RASTER_FORMATS + ["PDF"]

# Formats qui ne supportent pas la transparence -> il faut aplatir sur un fond blanc.
_NO_ALPHA_FORMATS = {"JPEG", "BMP"}

_FORMAT_EXTENSIONS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "GIF": ".gif",
    "TIFF": ".tiff",
    "ICO": ".ico",
}

INPUT_FILETYPES = [
    (t("imgconv.filetype_images_pdf"),
     "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.tif *.ico *.pdf"),
    (t("imgconv.filetype_raw"),
     "*.cr2 *.cr3 *.nef *.arw *.dng *.orf *.rw2 *.raf *.pef *.srw *.raw"),
    (t("imgconv.filetype_heic"), "*.heic *.heif"),
    (t("common.all_files"), "*.*"),
]
INPUT_EXTENSIONS = (
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif", ".ico", ".pdf"}
    | RAW_EXTENSIONS | HEIF_EXTENSIONS
)


# ----------------------------------------------------------------- Logique ---
def convert_image_file(input_path: str, output_path: str, output_format: str) -> None:
    """Convertit une image raster vers un autre format raster via Pillow.

    Gère correctement la conversion de mode (ex. RGBA -> RGB avec fond blanc
    pour les formats qui ne supportent pas la transparence, comme JPEG/BMP).
    """
    output_format = output_format.upper()
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        ensure_dir(out_dir)

    # open_image_any() gère aussi les formats RAW d'appareil photo (.cr2/.nef/.arw/...)
    # via rawpy, en plus des formats classiques ouverts directement par Pillow.
    im = open_image_any(input_path)
    try:
        if output_format in _NO_ALPHA_FORMATS:
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            elif im.mode != "RGB":
                im = im.convert("RGB")
        elif output_format == "GIF":
            if im.mode not in ("P", "L"):
                im = im.convert("RGBA") if "A" in im.mode else im.convert("RGB")
        elif output_format == "ICO":
            if im.mode != "RGBA":
                im = im.convert("RGBA")

        save_kwargs = {}
        if output_format == "ICO":
            size = min(max(im.size), 256)
            save_kwargs["sizes"] = [(size, size)]

        im.save(output_path, output_format, **save_kwargs)
    finally:
        im.close()


def images_to_pdf(input_paths: list, output_path: str, combine: bool) -> list:
    """Convertit une ou plusieurs images en PDF via Pillow.

    Si combine=True, toutes les images deviennent les pages d'un seul PDF
    (output_path). Si combine=False, chaque image produit son propre PDF
    (même dossier que output_path, nom d'origine + .pdf).
    Retourne la liste des chemins des PDF réellement créés.
    """
    if not input_paths:
        return []

    ensure_dir(os.path.dirname(os.path.abspath(output_path)) or ".")

    def _load_rgb(path):
        im = open_image_any(path)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            return background
        if im.mode != "RGB":
            return im.convert("RGB")
        return im

    created = []

    if combine:
        images = [_load_rgb(p) for p in input_paths]
        first, rest = images[0], images[1:]
        first.save(output_path, "PDF", save_all=True, append_images=rest)
        created.append(output_path)
    else:
        out_dir = os.path.dirname(output_path) or "."
        for path in input_paths:
            im = _load_rgb(path)
            base = os.path.splitext(os.path.basename(path))[0]
            dest = os.path.join(out_dir, base + ".pdf")
            im.save(dest, "PDF", save_all=True)
            created.append(dest)

    return created


def pdf_to_images(input_path: str, output_dir: str, output_format: str, dpi: int = 150) -> list:
    """Rend chaque page d'un PDF en image via pymupdf.

    Sauvegarde en output_format dans output_dir avec un nom du type
    '<nom_pdf>_page_001.<ext>'. Retourne la liste des chemins créés.
    """
    output_format = output_format.upper()
    ext = _FORMAT_EXTENSIONS.get(output_format, "." + output_format.lower())
    base = os.path.splitext(os.path.basename(input_path))[0]
    ensure_dir(output_dir)

    created = []
    doc = pymupdf.open(input_path)
    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(dpi=dpi)
            dest = os.path.join(output_dir, f"{base}_page_{page_index + 1:03d}{ext}")

            if output_format in ("PNG",):
                pix.save(dest)
            else:
                mode = "RGBA" if pix.alpha else "RGB"
                im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                if output_format in _NO_ALPHA_FORMATS and mode == "RGBA":
                    background = Image.new("RGB", im.size, (255, 255, 255))
                    background.paste(im, mask=im.split()[-1])
                    im = background
                save_kwargs = {}
                if output_format == "ICO":
                    size = min(max(im.size), 256)
                    save_kwargs["sizes"] = [(size, size)]
                im.save(dest, output_format, **save_kwargs)

            created.append(dest)
    finally:
        doc.close()

    return created


def is_pdf(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".pdf"


# --------------------------------------------------------------------- UI ---
class ImageConverterTab(ctk.CTkFrame):
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
        ctk.CTkLabel(files_frame, text=t("imgconv.files_label"),
                     anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        row = ctk.CTkFrame(files_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=8)
        self.files_label = ctk.CTkLabel(row, text=t("common.no_file_selected"), anchor="w")
        self.files_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text=t("common.browse"), width=100, command=self._choose_files).pack(side="left", padx=(8, 0))

        enable_file_drop(files_frame, self._set_selected_files, extensions=INPUT_EXTENSIONS)

        opts_frame = ctk.CTkFrame(body)
        opts_frame.pack(fill="x", **PAD)

        target_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        target_col.pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(target_col, text=t("imgconv.convert_to")).pack(anchor="w")
        self.target_var = ctk.StringVar(value="PNG")
        self.target_menu = ctk.CTkOptionMenu(target_col, values=ALL_TARGET_FORMATS, variable=self.target_var,
                                              command=self._on_target_change)
        self.target_menu.pack(anchor="w", pady=4)

        self.combine_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        self.combine_var = ctk.BooleanVar(value=True)
        self.combine_check = ctk.CTkCheckBox(self.combine_col, text=t("imgconv.combine_pdf"),
                                              variable=self.combine_var)
        self.combine_check.pack(anchor="w", pady=(18, 4))
        # Le conteneur n'est pack() que lorsqu'il est pertinent (voir _refresh_conditional_options).

        self.dpi_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        ctk.CTkLabel(self.dpi_col, text=t("imgconv.dpi")).pack(anchor="w")
        self.dpi_var = ctk.StringVar(value="150")
        self.dpi_menu = ctk.CTkOptionMenu(self.dpi_col, values=["72", "150", "300"], variable=self.dpi_var)
        self.dpi_menu.pack(anchor="w", pady=4)
        # Le conteneur n'est pack() que si les fichiers sélectionnés sont des PDF.

        out_frame = ctk.CTkFrame(body)
        out_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(out_frame, text=t("common.destination_folder"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        out_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        out_row.pack(fill="x", padx=8, pady=8)
        self.output_entry = ctk.CTkEntry(out_row)
        self.output_entry.insert(0, os.path.join(DEFAULT_OUTPUT_DIR, t("imgconv.output_subdir")))
        self.output_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text=t("common.browse"), width=100, command=self._choose_folder).pack(side="left", padx=(8, 0))

        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.pack(fill="x", **PAD)
        self.convert_btn = ctk.CTkButton(btn_frame, text=t("common.convert"), command=self._start_convert)
        self.convert_btn.pack(side="left")
        ctk.CTkButton(btn_frame, text=t("common.open_folder"), command=self._open_folder).pack(side="right")
        ctk.CTkButton(btn_frame, text=t("common.export_logs"), width=140,
                      command=lambda: export_log_to_file(self.log_box, t("logname.image_conversion"))).pack(side="right", padx=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(body)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", **PAD)

        self.status_label = ctk.CTkLabel(body, text=t("common.ready"), anchor="w")
        self.status_label.pack(fill="x", padx=16)

        self.log_box = ctk.CTkTextbox(body, height=200)
        self.log_box.pack(fill="both", expand=True, **PAD)
        self.log_box.configure(state="disabled")

    def _on_target_change(self, _value):
        self._refresh_conditional_options()

    def _refresh_conditional_options(self):
        target = self.target_var.get()
        pdf_inputs = self.selected_files and all(is_pdf(p) for p in self.selected_files)

        # Case "combiner" : uniquement pertinente quand on produit du PDF à partir
        # de plusieurs images (pas depuis des PDF en entrée).
        show_combine = (target == "PDF" and not pdf_inputs and len(self.selected_files) > 1)
        if show_combine:
            self.combine_col.pack(side="left", padx=8, pady=8)
        else:
            self.combine_col.pack_forget()

        # Sélecteur DPI : uniquement pertinent quand on part de PDF vers une image.
        show_dpi = bool(pdf_inputs) and target != "PDF"
        if show_dpi:
            self.dpi_col.pack(side="left", padx=8, pady=8)
        else:
            self.dpi_col.pack_forget()

    def _choose_files(self):
        paths = filedialog.askopenfilenames(title=t("imgconv.choose_files_title"), filetypes=INPUT_FILETYPES)
        if paths:
            self._set_selected_files(list(paths))

    def _set_selected_files(self, files):
        self.selected_files = list(files)
        if len(self.selected_files) == 1:
            self.files_label.configure(text=os.path.basename(self.selected_files[0]))
        else:
            self.files_label.configure(text=t("imgconv.n_files_selected", n=len(self.selected_files)))
        self._refresh_conditional_options()

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_entry.get() or DEFAULT_OUTPUT_DIR)
        if folder:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)

    def _open_folder(self):
        folder = ensure_dir(self.output_entry.get().strip() or DEFAULT_OUTPUT_DIR)
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

    # ----------------------------------------------------------- Convert ---
    def _start_convert(self):
        if not self.selected_files:
            messagebox.showwarning(t("common.no_file_title"), t("imgconv.no_file_message"))
            return

        output_dir = ensure_dir(self.output_entry.get().strip()
                                 or os.path.join(DEFAULT_OUTPUT_DIR, t("imgconv.output_subdir")))

        target_format = self.target_var.get()
        combine = self.combine_var.get()
        dpi = int(self.dpi_var.get())
        files = list(self.selected_files)

        self.convert_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.status_label.configure(text=t("common.preparing"))

        self.convert_thread = threading.Thread(
            target=self._run_convert, args=(files, target_format, combine, dpi, output_dir), daemon=True)
        self.convert_thread.start()

    def _run_convert(self, files, target_format, combine, dpi, output_dir):
        try:
            pdf_inputs = [p for p in files if is_pdf(p)]
            image_inputs = [p for p in files if not is_pdf(p)]
            total_steps = len(files)
            done_steps = 0
            produced = []

            def bump(n=1):
                nonlocal done_steps
                done_steps += n
                if total_steps:
                    self.log_queue.put(("progress", min(done_steps / total_steps, 1.0)))

            # 1) PDF -> images (peu importe la cible, sauf si la cible est aussi PDF,
            #    auquel cas on laisse le fichier PDF tel quel et on le signale).
            for pdf_path in pdf_inputs:
                if target_format == "PDF":
                    self._log(t("imgconv.skipped_pdf", name=os.path.basename(pdf_path)))
                    bump()
                    continue
                self.log_queue.put(("status", t("imgconv.extracting_pages", name=os.path.basename(pdf_path))))
                try:
                    pages = pdf_to_images(pdf_path, output_dir, target_format, dpi=dpi)
                    for page_path in pages:
                        self._log(t("imgconv.created", path=page_path))
                        produced.append(page_path)
                except Exception as e:
                    self._log(t("imgconv.file_failed", name=os.path.basename(pdf_path), error=e))
                bump()

            # 2) Images -> images (formats raster classiques)
            if target_format != "PDF":
                for img_path in image_inputs:
                    base = os.path.splitext(os.path.basename(img_path))[0]
                    ext = _FORMAT_EXTENSIONS.get(target_format, "." + target_format.lower())
                    dest = os.path.join(output_dir, base + ext)
                    self.log_queue.put(("status", t("imgconv.converting", name=os.path.basename(img_path))))
                    try:
                        convert_image_file(img_path, dest, target_format)
                        self._log(t("imgconv.created", path=dest))
                        produced.append(dest)
                    except Exception as e:
                        self._log(t("imgconv.file_failed", name=os.path.basename(img_path), error=e))
                    bump()

            # 3) Images -> PDF
            else:
                if image_inputs:
                    self.log_queue.put(("status", t("imgconv.generating_pdf")))
                    try:
                        if combine and len(image_inputs) > 1:
                            dest = os.path.join(output_dir, t("imgconv.combined_pdf_name"))
                            created = images_to_pdf(image_inputs, dest, combine=True)
                        else:
                            placeholder = os.path.join(output_dir, "placeholder.pdf")
                            created = images_to_pdf(image_inputs, placeholder, combine=False)
                        for p in created:
                            self._log(t("imgconv.created", path=p))
                            produced.append(p)
                    except Exception as e:
                        # combine=True fusionne toutes les images en un seul PDF : si l'une
                        # d'elles est invalide, on ne peut pas produire un PDF "partiel"
                        # cohérent, donc tout ce groupe échoue ensemble (contrairement aux
                        # deux boucles ci-dessus qui isolent les échecs fichier par fichier).
                        self._log(t("imgconv.pdf_failed", error=e))
                    bump(len(image_inputs))

            self.log_queue.put(("progress", 1.0))
            self.log_queue.put(("status", t("imgconv.finished_status", count=len(produced))))
            self._log(t("imgconv.finished"))
        except Exception:
            self.log_queue.put(("status", t("common.unexpected_error_status")))
            self._log(t("common.unexpected_error_log") + "\n" + traceback.format_exc())
        finally:
            self.log_queue.put(("done", None))
