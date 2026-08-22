"""
Onglet : téléchargeur vidéo (MP3/MP4, playlists, métadonnées + pochette).

Basé sur yt-dlp, qui supporte nativement ~1800 sites (YouTube, TikTok, Instagram,
Twitter/X, Facebook, Vimeo, Twitch, Reddit, Dailymotion, SoundCloud...) sans code
supplémentaire ici -- seul le nom de l'onglet et le texte d'aide ont été généralisés.
"""

import ctypes
import hashlib
import os
import re
import threading
import queue
import time
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox
import yt_dlp
from yt_dlp.postprocessor import MetadataParserPP
from yt_dlp.utils import DownloadCancelled, DownloadError

from common import (
    PAD, DEFAULT_OUTPUT_DIR, bundled_bin_dir, ensure_dir, build_scrollable_body,
    export_log_to_file,
)
from i18n import t

# Libellé du palier "meilleure qualité disponible" : mémorisé une fois pour toutes, car
# il sert aussi de valeur testée plus bas (comparer à une chaîne en dur ne marcherait
# évidemment plus en anglais).
QUALITY_BEST = t("downloader.quality_best")
VIDEO_QUALITIES = [QUALITY_BEST, "1080p", "720p", "480p"]
AUDIO_QUALITIES = ["320 kbps", "192 kbps", "128 kbps"]

# Vimeo a révoqué (côté Vimeo, pas un bug yt-dlp) les identifiants OAuth "anonymes"
# utilisés pour les URLs vimeo.com/<id> classiques -> échec systématique avec "Failed
# to fetch macos OAuth token: 401". Le contournement documenté est d'utiliser l'URL
# player.vimeo.com/video/<id> équivalente, qui passe par un autre chemin d'extraction.
_VIMEO_PLAIN_URL_RE = re.compile(r"^https?://(?:www\.)?vimeo\.com/(\d+)(?:[/?].*)?$", re.IGNORECASE)

_FILE_ATTRIBUTE_HIDDEN = 0x02


def _hide_file_windows(path):
    """Marque un fichier comme "caché" (attribut Windows), pas juste par convention de
    nom -- sans ça, le fichier d'archive de playlist (voir _run_download) apparaîtrait
    comme un fichier normal dans le dossier de musique de l'utilisateur, alors qu'il
    s'agit d'un détail d'implémentation interne, pas d'un fichier à voir/gérer."""
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), _FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


class DownloaderTab(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.log_queue = queue.Queue()
        self.download_thread = None
        self.cancel_flag = threading.Event()

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        body = build_scrollable_body(self)

        url_frame = ctk.CTkFrame(body)
        url_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(url_frame, text=t("downloader.url_label"),
                     anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        self.url_entry = ctk.CTkEntry(url_frame, placeholder_text="https://...")
        self.url_entry.pack(fill="x", padx=8, pady=8)

        opts_frame = ctk.CTkFrame(body)
        opts_frame.pack(fill="x", **PAD)

        fmt_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        fmt_col.pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(fmt_col, text=t("downloader.format")).pack(anchor="w")
        self.format_var = ctk.StringVar(value="MP4")
        self.format_menu = ctk.CTkOptionMenu(fmt_col, values=["MP4", "MP3"], variable=self.format_var,
                                              command=self._on_format_change)
        self.format_menu.pack(anchor="w", pady=4)

        qual_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        qual_col.pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(qual_col, text=t("downloader.quality")).pack(anchor="w")
        self.quality_var = ctk.StringVar(value="1080p")
        self.quality_menu = ctk.CTkOptionMenu(qual_col, values=VIDEO_QUALITIES,
                                               variable=self.quality_var)
        self.quality_menu.pack(anchor="w", pady=4)

        pl_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        pl_col.pack(side="left", padx=8, pady=8)
        self.playlist_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(pl_col, text=t("downloader.whole_playlist"),
                         variable=self.playlist_var).pack(anchor="w", pady=(18, 4))

        meta_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        meta_col.pack(side="left", padx=8, pady=8)
        self.metadata_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(meta_col, text=t("downloader.metadata"),
                         variable=self.metadata_var).pack(anchor="w", pady=(18, 4))

        track_col = ctk.CTkFrame(opts_frame, fg_color="transparent")
        track_col.pack(side="left", padx=8, pady=8)
        self.track_number_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(track_col, text=t("downloader.track_number"),
                         variable=self.track_number_var).pack(anchor="w", pady=(18, 4))

        out_frame = ctk.CTkFrame(body)
        out_frame.pack(fill="x", **PAD)
        ctk.CTkLabel(out_frame, text=t("common.destination_folder"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        row = ctk.CTkFrame(out_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=8)
        self.output_entry = ctk.CTkEntry(row)
        self.output_entry.insert(0, DEFAULT_OUTPUT_DIR)
        self.output_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text=t("common.browse"), width=100, command=self._choose_folder).pack(side="left", padx=(8, 0))

        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.pack(fill="x", **PAD)
        self.download_btn = ctk.CTkButton(btn_frame, text=t("downloader.download"), command=self._start_download)
        self.download_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btn_frame, text=t("common.cancel"), fg_color="#a33333", hover_color="#822222",
                                         command=self._cancel_download, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text=t("common.open_folder"), command=self._open_folder).pack(side="right")
        ctk.CTkButton(btn_frame, text=t("common.export_logs"), width=140,
                      command=lambda: export_log_to_file(self.log_box, t("logname.videos"))).pack(side="right", padx=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(body)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", **PAD)

        self.status_label = ctk.CTkLabel(body, text=t("common.ready"), anchor="w")
        self.status_label.pack(fill="x", padx=16)

        self.log_box = ctk.CTkTextbox(body, height=200)
        self.log_box.pack(fill="both", expand=True, **PAD)
        self.log_box.configure(state="disabled")

    def _on_format_change(self, value):
        if value == "MP3":
            self.quality_menu.configure(values=AUDIO_QUALITIES)
            self.quality_var.set(AUDIO_QUALITIES[0])
        else:
            self.quality_menu.configure(values=VIDEO_QUALITIES)
            self.quality_var.set("1080p")

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
                    self.download_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ---------------------------------------------------------- Download ---
    def _start_download(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning(t("downloader.missing_url_title"), t("downloader.missing_url_message"))
            return

        output_dir = ensure_dir(self.output_entry.get().strip() or DEFAULT_OUTPUT_DIR)

        fmt = self.format_var.get()
        quality = self.quality_var.get()
        is_playlist = self.playlist_var.get()
        want_metadata = self.metadata_var.get()
        want_track_number = self.track_number_var.get()

        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.status_label.configure(text=t("common.preparing"))
        self.cancel_flag.clear()

        self.download_thread = threading.Thread(
            target=self._run_download,
            args=(url, fmt, quality, is_playlist, want_metadata, want_track_number, output_dir),
            daemon=True)
        self.download_thread.start()

    def _cancel_download(self):
        self.cancel_flag.set()
        self._log(t("common.cancel_requested"))

    def _progress_hook(self, d):
        if self.cancel_flag.is_set():
            raise DownloadCancelled(t("downloader.cancelled_by_user"))

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                self.log_queue.put(("progress", downloaded / total))
            filename = os.path.basename(d.get("filename", "") or "")
            speed = (d.get("_speed_str") or "").strip()
            eta = (d.get("_eta_str") or "").strip()
            self.log_queue.put(("status", t("downloader.downloading", filename=filename, speed=speed, eta=eta)))
        elif d["status"] == "finished":
            self.log_queue.put(("progress", 1.0))
            self.log_queue.put(("status", t("downloader.finalizing")))

    def _run_download(self, url, fmt, quality, is_playlist, want_metadata, want_track_number, output_dir):
        # En playlist, chaque piste a une petite chance indépendante de heurter le verrou
        # de fichier transitoire (voir plus bas) -- sur une grosse playlist, la probabilité
        # qu'AU MOINS une piste sur toutes échoue à une tentative donnée devient vite élevée,
        # même si la quasi-totalité réussit. Sans archive, chaque nouvelle tentative
        # retéléchargeait TOUTE la playlist depuis le début (y compris les pistes déjà
        # réussies -- gaspillage de bande passante, fichiers .webm/.webp résiduels), et
        # après 3 tentatives malchanceuses sur des pistes différentes, l'appli abandonnait
        # avec "Arrêté" même quand presque tout avait réussi. Avec un fichier d'archive
        # (rempli par yt-dlp uniquement pour les pistes dont TOUT le post-traitement a
        # réussi, cf. code source), les tentatives suivantes sautent instantanément les
        # pistes déjà bonnes et ne retéléchargent que celle(s) qui a/ont vraiment échoué.
        #
        # Nommé à partir d'un hash de l'URL (stable d'un lancement à l'autre pour la MÊME
        # playlist) et placé dans le dossier de destination -- PAS supprimé si le
        # téléchargement échoue au bout des 3 tentatives, pour qu'un futur clic sur
        # "Télécharger" avec la même playlist reprenne où ça s'est arrêté au lieu de tout
        # refaire. Supprimé uniquement en cas de succès complet (sinon une piste supprimée
        # manuellement plus tard par l'utilisateur serait injustement resautée pour
        # toujours lors d'un futur retéléchargement de cette playlist). Cache Windows
        # explicitement (voir _hide_file_windows) pour ne pas polluer le dossier de
        # musique avec un fichier qui n'a rien à y faire visuellement.
        archive_path = None
        if is_playlist:
            url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
            archive_path = os.path.join(output_dir, f".mediadownloader_archive_{url_hash}.tmp")

        try:
            outtmpl = os.path.join(
                output_dir,
                "%(playlist_title)s/%(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s",
            )

            ydl_opts = {
                "outtmpl": outtmpl,
                "noplaylist": not is_playlist,
                "progress_hooks": [self._progress_hook],
                "logger": _YdlLogger(self._log),
                "ignoreerrors": is_playlist,  # continue playlist même si un item échoue
                "restrictfilenames": False,
                # Sans ça, yt-dlp écrit EN PLUS une pochette au niveau de la PLAYLIST
                # elle-même (ex. "In Rainbows.jpg" à la racine du dossier) quand
                # want_metadata est coché -- ce fichier n'est lié à aucune piste précise et
                # n'a aucune étape d'intégration prévue (EmbedThumbnail n'agit que sur
                # chaque piste individuellement), donc il reste indéfiniment dans le
                # dossier. allow_playlist_files=False désactive uniquement ces fichiers
                # "au niveau playlist" (thumbnail/description/infojson) sans toucher à la
                # pochette PAR PISTE, qui elle continue d'être intégrée puis nettoyée
                # normalement (déjà vérifié en pratique).
                "allow_playlist_files": False,
            }
            if archive_path:
                ydl_opts["download_archive"] = archive_path

            bin_dir = bundled_bin_dir()
            if bin_dir:
                ydl_opts["ffmpeg_location"] = bin_dir
                # Deno (moteur JS requis par YouTube pour déchiffrer les vidéos) :
                # pointe vers le binaire embarqué au lieu de compter sur le PATH système.
                deno_path = os.path.join(bin_dir, "deno.exe")
                if os.path.isfile(deno_path):
                    ydl_opts["js_runtimes"] = {"deno": {"path": deno_path}}

            postprocessors = []

            if fmt == "MP3":
                bitrate = quality.split()[0]  # "320" / "192" / "128"
                ydl_opts["format"] = "bestaudio/best"
                postprocessors.append({
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": bitrate,
                })
            else:
                if quality == QUALITY_BEST:
                    ydl_opts["format"] = "bestvideo+bestaudio/best"
                else:
                    # Filet de secours final (/bestvideo+bestaudio/best) : certains sites
                    # (Instagram Reels notamment) n'offrent que des résolutions au-dessus
                    # de tous nos paliers (ex. seulement 1280p/1920p disponibles) -- sans
                    # ce filet, le palier demandé ne matche jamais rien et le téléchargement
                    # échoue avec "Requested format is not available" au lieu de simplement
                    # prendre ce qu'il y a de mieux disponible.
                    height = quality.replace("p", "")
                    ydl_opts["format"] = (
                        f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
                        "/bestvideo+bestaudio/best"
                    )
                ydl_opts["merge_output_format"] = "mp4"
                # Préfère l'audio AAC à l'audio Opus quand les deux existent (sans exclure
                # Opus si c'est la seule option) : YouTube sert le plus souvent de l'Opus
                # en piste "bestaudio" (bitrate légèrement supérieur à l'AAC équivalent),
                # mais une fois fusionné dans un conteneur .mp4, l'audio Opus ne se lit pas
                # dans beaucoup de lecteurs (vidéo silencieuse) -- l'AAC, lui, est nativement
                # compatible MP4 partout.
                ydl_opts["format_sort"] = ["acodec:aac"]

            # Numéro de piste : n'a de sens qu'en playlist (playlist_index = position dans
            # la playlist telle que retournée par le site, ex. 1, 2, 3...). Doit être
            # inséré AVANT FFmpegMetadata dans la liste -- l'ordre des postprocessors est
            # celui d'exécution, et FFmpegMetadata lit track_number depuis info_dict pour
            # écrire le tag "track" (ID3 TRCK pour MP3, équivalent MP4 pour les vidéos) ;
            # sans ce parseur, track_number n'existe pas dans info_dict et rien n'est écrit.
            if want_track_number and is_playlist:
                postprocessors.append({
                    "key": "MetadataParser",
                    "actions": [(MetadataParserPP.Actions.INTERPRET, "playlist_index", "%(track_number)s")],
                })

            if want_metadata:
                # Titre / artiste / album (mappés depuis les infos dispo : titre vidéo,
                # chaîne/uploader en artiste si aucun artiste explicite, album si présent).
                ydl_opts["writethumbnail"] = True
                postprocessors.append({
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                    "add_chapters": False,
                    "add_infojson": False,
                })
                # Pochette embarquée dans le fichier ; le thumbnail temporaire est
                # supprimé une fois intégré (already_have_thumbnail=False).
                postprocessors.append({
                    "key": "EmbedThumbnail",
                    "already_have_thumbnail": False,
                })
            elif want_track_number and is_playlist:
                # Case "métadonnées" décochée mais numéro de piste demandé quand même :
                # il faut malgré tout un postprocessor qui écrive réellement le tag dans
                # le fichier (MetadataParser ne fait que préparer la valeur en mémoire).
                postprocessors.append({
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                    "add_chapters": False,
                    "add_infojson": False,
                })

            ydl_opts["postprocessors"] = postprocessors

            self._log(t("downloader.starting", url=url))

            # Certains sites (TikTok en particulier) échouent de façon intermittente à
            # cause de protections anti-bot dont le comportement varie d'une requête à
            # l'autre -- une simple nouvelle tentative réussit très souvent (constaté en
            # conditions réelles : ~1 échec sur 2 sur la même URL, sans aucun changement).
            max_attempts = 3
            current_url = url
            for attempt in range(1, max_attempts + 1):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        retcode = ydl.download([current_url])
                    # IMPORTANT : certaines erreurs (ex. le post-traitement -- renommage du
                    # fichier final -- qui échoue avec "[WinError 32] utilisé par un autre
                    # processus", souvent l'antivirus qui scanne le fichier fraîchement créé)
                    # sont interceptées EN INTERNE par yt-dlp (report_error() puis simple
                    # `return`, sans lever d'exception) : ydl.download() se termine alors
                    # normalement, SANS DownloadError, alors que le fichier final n'a jamais
                    # été créé. Sans cette vérification du code retour, on affichait "Terminé."
                    # à tort. yt-dlp signale ce cas via son code de retour (0 = succès).
                    if retcode != 0:
                        raise DownloadError(t("downloader.postprocessing_failed"))
                    break
                except DownloadError as e:
                    if attempt >= max_attempts:
                        raise

                    vimeo_match = _VIMEO_PLAIN_URL_RE.match(current_url)
                    if vimeo_match and "OAuth token" in str(e):
                        current_url = f"https://player.vimeo.com/video/{vimeo_match.group(1)}"
                        self._log(t("downloader.vimeo_retry", url=current_url))
                        continue

                    self._log(t("downloader.attempt_failed", attempt=attempt, total=max_attempts))
                    for _ in range(30):  # attente en petits pas pour rester réactif à Annuler
                        if self.cancel_flag.is_set():
                            raise DownloadCancelled(t("downloader.cancelled_by_user"))
                        time.sleep(0.1)

            self.log_queue.put(("status", t("common.done")))
            self._log(t("downloader.finished"))
            # Succès complet : l'archive n'a plus d'utilité (voir explication plus haut) --
            # on la supprime pour qu'un futur téléchargement de cette playlist reparte de
            # zéro (ex. si l'utilisateur a supprimé une piste manuellement entre-temps et
            # veut la retélécharger).
            if archive_path:
                try:
                    os.remove(archive_path)
                except OSError:
                    pass
        except (DownloadCancelled, DownloadError) as e:
            if archive_path and os.path.exists(archive_path):
                _hide_file_windows(archive_path)
            if is_playlist:
                # Grâce à l'archive ci-dessus, un échec ici signifie qu'au moins une piste
                # a échoué à TOUTES les tentatives (les autres, elles, ont bien été gardées
                # -- rien n'a été perdu). On le précise pour éviter de faire croire à un
                # échec total du téléchargement de la playlist.
                self.log_queue.put(("status", t("downloader.stopped_playlist_status")))
                self._log(t("downloader.stopped_playlist_log", error=e))
            else:
                self.log_queue.put(("status", t("downloader.stopped_status")))
                self._log(t("downloader.stopped_log", error=e))
        except Exception:
            if archive_path and os.path.exists(archive_path):
                _hide_file_windows(archive_path)
            self.log_queue.put(("status", t("common.unexpected_error_status")))
            self._log(t("common.unexpected_error_log") + "\n" + traceback.format_exc())
        finally:
            self.log_queue.put(("done", None))


class _YdlLogger:
    """Redirige les messages internes de yt-dlp vers la zone de log de l'UI."""

    def __init__(self, log_fn):
        self._log = log_fn

    def debug(self, msg):
        if msg.startswith("[debug] "):
            return
        self._log(msg)

    def info(self, msg):
        self._log(msg)

    def warning(self, msg):
        self._log("⚠ " + msg)

    def error(self, msg):
        self._log("✖ " + msg)
