"""
Media Downloader & Toolbox — appli avec menu latéral rétractable :
- Vidéos (YouTube, TikTok, +1800 sites via yt-dlp) : téléchargement MP3/MP4
  (playlists, métadonnées + pochette)
- Compresseur d'images
- Vidéo -> Audio (fichiers locaux)
- Compresseur de vidéo
- Créateur de GIF
- Convertisseur de texte (txt/md/html/docx/pdf, dans les deux sens)
- Convertisseur d'images (incl. PDF)

Usage prévu : contenu personnel, sous licence libre, ou dont tu as les droits.
Le téléchargement/traitement de contenus protégés par le droit d'auteur sans
autorisation peut violer les CGU des plateformes et la loi selon l'usage.
"""

import os
import subprocess
import sys

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from common import DEFAULT_OUTPUT_DIR, ensure_dir
from i18n import (
    LABEL_TO_LANGUAGE, LANGUAGE_LABELS, get_language, save_language, t,
)
from tools.youtube_tiktok import DownloaderTab
from tools.image_compressor import ImageCompressorTab
from tools.mp4_to_mp3 import Mp4ToMp3Tab
from tools.video_compressor import VideoCompressorTab
from tools.gif_maker import GifMakerTab
from tools.text_converter import TextConverterTab
from tools.image_converter import ImageConverterTab

ctk.set_default_color_theme("blue")

# (icône, clé i18n, classe d'onglet) -- l'icône est un simple emoji, pas besoin d'assets
# image séparés. La clé i18n sert AUSSI d'identifiant interne de l'outil (dict des
# frames, bouton actif...) : contrairement au nom affiché, elle ne change pas d'une
# langue à l'autre, donc rien à réécrire côté navigation.
TOOLS = [
    ("🎬", "tool.downloader", DownloaderTab),
    ("🖼️", "tool.image_compressor", ImageCompressorTab),
    ("🎵", "tool.audio", Mp4ToMp3Tab),
    ("🗜️", "tool.video_compressor", VideoCompressorTab),
    ("🎞️", "tool.gif", GifMakerTab),
    ("📝", "tool.text", TextConverterTab),
    ("🔄", "tool.image_converter", ImageConverterTab),
]

SIDEBAR_WIDTH_EXPANDED = 230
SIDEBAR_WIDTH_COLLAPSED = 48
# Calé sur le tiroir latéral de YouTube (mesuré image par image sur une vidéo de
# référence : ~110-120ms, easing "ease-out" -- rapide au début, doux à la fin).
# Nombre d'étapes volontairement modeste : Windows ne délivre de toute façon pas de
# after() en dessous d'~15ms (résolution des timers), et chaque étape redessine la
# barre de défilement du journal de l'outil actif (coûteux côté CustomTkinter) --
# plus d'étapes que ça n'ajoute pas de fluidité perçue, juste plus de travail.
SIDEBAR_ANIMATION_MS = 120
SIDEBAR_ANIMATION_STEPS = 9

# Le sélecteur de thème affiche des libellés traduits, mais CustomTkinter attend ses
# propres valeurs en anglais ("System"/"Light"/"Dark") -- d'où la table de correspondance
# construite à partir des libellés courants.
APPEARANCE_MODES = [t("app.theme_system"), t("app.theme_light"), t("app.theme_dark")]
_APPEARANCE_TO_CTK = dict(zip(APPEARANCE_MODES, ["System", "Light", "Dark"]))


class ToolboxApp(ctk.CTk, TkinterDnD.DnDWrapper):
    """Fenêtre racine : CTk pour l'apparence, + mixin TkinterDnD pour activer le
    glisser-déposer de fichiers Windows Explorer sur les widgets de tous les onglets."""

    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)
        self.title(t("app.title"))
        self.geometry("1050x680")
        self.minsize(820, 560)

        self._sidebar_expanded = True
        self._sidebar_width_now = SIDEBAR_WIDTH_EXPANDED
        self._sidebar_animating = False
        self._nav_buttons = {}
        self._active_key = None
        self._frozen_scrollbars = []
        # Levé par le sélecteur de langue ; lu après mainloop() pour relancer l'appli.
        self.restart_requested = False

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()
        self._build_content_area()

        # Sélectionne le premier outil par défaut.
        self._show_tool(TOOLS[0][1])

    # ----------------------------------------------------------- Sidebar ---
    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH_EXPANDED, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(1, weight=1)

        # Bouton hamburger : toujours visible, replie/déroule le reste du menu.
        toggle_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        toggle_row.grid(row=0, column=0, sticky="ew", padx=6, pady=8)
        self.toggle_btn = ctk.CTkButton(
            toggle_row, text="☰", width=32, height=32, command=self._toggle_sidebar)
        self.toggle_btn.pack(side="left")

        # Conteneur des boutons de navigation + sélecteur de thème -- caché en entier
        # quand le menu est replié (c'est ce conteneur que "déroule" le bouton hamburger).
        self.nav_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_container.grid(row=1, column=0, sticky="nsew", padx=6)
        self.nav_container.grid_columnconfigure(0, weight=1)

        for row, (icon, key, tab_class) in enumerate(TOOLS):
            btn = ctk.CTkButton(
                self.nav_container, text=f"{icon}  {t(key)}", anchor="w",
                fg_color="transparent", hover_color=("gray80", "gray28"),
                # CTkButton part du principe que fg_color est une couleur pleine (donc
                # texte blanc lisible dessus) -- avec fg_color="transparent", le texte
                # blanc par défaut devient invisible sur le fond clair du thème Clair.
                # Il faut donc fixer explicitement une couleur de texte adaptative.
                text_color=("gray10", "gray90"),
                command=lambda k=key: self._show_tool(k))
            btn.grid(row=row, column=0, sticky="ew", pady=2)
            self._nav_buttons[key] = btn

        # Ligne élastique vide : pousse les sélecteurs de réglages vers le bas du menu.
        self.nav_container.grid_rowconfigure(len(TOOLS), weight=1)

        # Les sélecteurs de thème et de langue vivent DANS nav_container (pas directement
        # sur self.sidebar) pour se cacher/réapparaître avec le reste du menu quand on
        # replie/déplie -- sinon ils restent affichés (coupés) même menu replié.
        appearance_frame = ctk.CTkFrame(self.nav_container, fg_color="transparent")
        appearance_frame.grid(row=len(TOOLS) + 1, column=0, sticky="sew", pady=(16, 4))
        ctk.CTkLabel(appearance_frame, text=t("app.theme_label"), anchor="w",
                     text_color=("gray40", "gray60")).pack(fill="x")
        self.appearance_var = ctk.StringVar(value=APPEARANCE_MODES[0])
        self.appearance_menu = ctk.CTkSegmentedButton(
            appearance_frame, values=APPEARANCE_MODES, variable=self.appearance_var,
            command=self._on_appearance_change)
        self.appearance_menu.pack(fill="x", pady=(4, 0))

        language_frame = ctk.CTkFrame(self.nav_container, fg_color="transparent")
        language_frame.grid(row=len(TOOLS) + 2, column=0, sticky="sew", pady=(8, 12))
        ctk.CTkLabel(language_frame, text=t("app.language_label"), anchor="w",
                     text_color=("gray40", "gray60")).pack(fill="x")
        self.language_var = ctk.StringVar(value=LANGUAGE_LABELS[get_language()])
        self.language_menu = ctk.CTkSegmentedButton(
            language_frame, values=list(LANGUAGE_LABELS.values()),
            variable=self.language_var, command=self._on_language_change)
        self.language_menu.pack(fill="x", pady=(4, 0))

    def _toggle_sidebar(self):
        if self._sidebar_animating:
            return  # ignore les clics pendant qu'une animation est déjà en cours
        self._sidebar_expanded = not self._sidebar_expanded
        target_width = SIDEBAR_WIDTH_EXPANDED if self._sidebar_expanded else SIDEBAR_WIDTH_COLLAPSED

        if self._sidebar_expanded:
            # Re-grid tout de suite : le contenu se révèle progressivement par le
            # rognage naturel du cadre (largeur fixe, grid_propagate(False)) pendant
            # que l'animation l'élargit -- effet de "glissement" sans code dédié.
            self.nav_container.grid(row=1, column=0, sticky="nsew", padx=6)

        self._freeze_active_scrollbars()
        self._sidebar_animating = True
        self._animate_sidebar_step(self._sidebar_width_now, target_width, step=0)

    def _animate_sidebar_step(self, start_width, target_width, step):
        progress = min(step / SIDEBAR_ANIMATION_STEPS, 1.0)
        eased = 1 - (1 - progress) ** 3  # ease-out cubique, proche de la courbe standard utilisée par YouTube
        current_width = round(start_width + (target_width - start_width) * eased)
        self.sidebar.configure(width=current_width)
        self._sidebar_width_now = current_width

        if progress >= 1.0:
            self._sidebar_animating = False
            if not self._sidebar_expanded:
                self.nav_container.grid_forget()
            self._unfreeze_active_scrollbars()
            return

        delay_ms = max(SIDEBAR_ANIMATION_MS // SIDEBAR_ANIMATION_STEPS, 1)
        self.after(delay_ms, lambda: self._animate_sidebar_step(start_width, target_width, step + 1))

    def _on_appearance_change(self, value):
        ctk.set_appearance_mode(_APPEARANCE_TO_CTK.get(value, "System"))

    def _on_language_change(self, value):
        """Sauvegarde la langue choisie puis relance l'appli.

        Pourquoi un redémarrage plutôt qu'une mise à jour en direct : CustomTkinter ne
        re-rend pas le texte des widgets déjà construits, il faudrait donc garder une
        référence sur CHAQUE label/bouton/placeholder/option des 7 onglets et les
        reconfigurer un par un -- du code fragile, à maintenir en double à chaque ajout
        de widget, et qui laisserait de toute façon des textes figés (messages déjà
        écrits dans les journaux, listes déroulantes...). Relancer le processus donne
        une UI 100 % cohérente pour un coût dérisoire ici (démarrage quasi instantané).
        """
        code = LABEL_TO_LANGUAGE.get(value)
        if not code or code == get_language():
            return

        save_language(code)
        # On ne relance PAS le nouveau processus ici : on ferme d'abord la fenêtre, et
        # c'est le bloc __main__ (après mainloop) qui lance le successeur. Sinon les deux
        # fenêtres coexistent une fraction de seconde, ce qui donne un clignotement peu
        # soigné et un focus qui part sur l'ancienne fenêtre en train de mourir.
        self.restart_requested = True
        self.destroy()

    # ------------------------------------------------------ Optimisation ---
    def _freeze_active_scrollbars(self):
        """Gèle temporairement le redessin des CTkScrollbar de l'outil actif pendant
        l'animation du menu. Chaque étape de l'animation redimensionne self.sidebar,
        ce qui pousse content (colonne à poids 1) à se redimensionner en cascade, ce
        qui déclenche un <Configure> sur tous ses widgets -- et le redessin d'une
        CTkScrollbar (coins arrondis recalculés en polygone) coûte ~15-20ms mesuré au
        profiler. Multiplié par le nombre d'étapes, ça suffit à rendre l'animation
        saccadée. Solution ciblée : on rend ce redessin silencieux pendant l'animation
        (l'utilisateur ne regarde pas le journal de toute façon à ce moment précis) et
        on force un redessin correct une fois l'animation terminée."""
        self._frozen_scrollbars = []
        active_frame = self.tool_frames.get(self._active_key)
        for attr in ("_y_scrollbar", "_x_scrollbar"):
            textbox = getattr(active_frame, "log_box", None)
            scrollbar = getattr(textbox, attr, None) if textbox is not None else None
            if scrollbar is not None and hasattr(scrollbar, "_draw"):
                self._frozen_scrollbars.append((scrollbar, scrollbar._draw))
                scrollbar._draw = lambda *a, **k: None

    def _unfreeze_active_scrollbars(self):
        for scrollbar, original_draw in getattr(self, "_frozen_scrollbars", []):
            scrollbar._draw = original_draw
            try:
                scrollbar._draw()  # un seul vrai redessin, une fois l'animation finie
            except Exception:
                pass
        self._frozen_scrollbars = []

    # -------------------------------------------------------- Contenu ---
    def _build_content_area(self):
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        # Tous les outils sont construits une seule fois (donc gardent leur état --
        # fichiers sélectionnés, opérations en fond -- même hors écran), mais SEUL
        # l'outil actif est réellement géré par la grille (.grid()) ; les autres sont
        # détachés (.grid_forget()) tant qu'ils ne sont pas affichés. IMPORTANT : les
        # garder tous "grid()-és" en permanence (empilés + tkraise) semblait plus simple,
        # mais chaque redimensionnement de fenêtre (notre animation de menu latéral,
        # justement) propage alors un événement <Configure> à TOUS les widgets de TOUS
        # les onglets, pas seulement celui affiché -- et les CTkScrollbar internes des
        # CTkTextbox de log sont coûteux à redessiner. Avec 7 onglets tous "vivants" en
        # permanence, ça multipliait le travail par 7 à chaque image d'animation et
        # provoquait des gels de 500ms+ (mesurés au profiler). Détacher les onglets
        # inactifs de la grille règle le problème sans perdre leur état.
        self.tool_frames = {}
        for icon, key, tab_class in TOOLS:
            frame = tab_class(self.content)
            self.tool_frames[key] = frame

    def _show_tool(self, key):
        if self._active_key is not None:
            self.tool_frames[self._active_key].grid_forget()
            self._nav_buttons[self._active_key].configure(fg_color="transparent")
        self.tool_frames[key].grid(row=0, column=0, sticky="nsew")
        self._nav_buttons[key].configure(fg_color=("gray75", "gray30"))
        self._active_key = key


def relaunch_self():
    """Relance l'appli dans un nouveau processus, en dev comme une fois packagée.

    Pourquoi subprocess.Popen et pas os.execv : sous Windows, os.exec* ne remplace pas
    réellement le processus (c'est une émulation qui crée un nouveau PID et tue l'ancien),
    ce qui se marie très mal avec le bootloader --onefile -- celui-ci attend son processus
    enfant pour nettoyer son dossier temporaire _MEIxxxx, et se retrouverait à surveiller
    un processus déjà remplacé.

    En dev (`python app.py`), il n'y a rien de plus à faire : on relance simplement
    l'interpréteur sur le script, le démarrage est instantané.

    En packagé (--onefile), relancer l'exe naïvement NE MARCHE PAS : le successeur plante
    au démarrage ("Failed to load Python DLL ...\\_MEIxxxxxx\\python312.dll", ou un import
    qui échoue plus loin comme lxml). Trois précautions distinctes sont nécessaires, chacune
    détaillée à l'endroit du code concerné :

    1. environnement nettoyé (clean_env) -- LA cause racine du plantage ;
    2. flux standard neutres (silent_io) -- sans quoi le lanceur intermédiaire meurt
       aussitôt, silencieusement, et l'appli ne revient jamais ;
    3. attente de la fin réelle de l'ancienne instance avant de démarrer la nouvelle, pour
       ne pas faire se chevaucher l'extraction de ~190 Mo du successeur avec la suppression
       de ~190 Mo de son prédécesseur (précaution de confort, pas la cause du bug).

    Le point 3 impose un petit lanceur PowerShell intermédiaire : seul un tiers peut
    observer notre propre mort. Coût pour l'utilisateur : environ une seconde de plus, sur
    une action volontairement rare. Validé par 6 redémarrages packagés enchaînés sans échec.
    """
    if not getattr(sys, "frozen", False):
        script = os.path.abspath(__file__)
        subprocess.Popen([sys.executable, script], cwd=os.path.dirname(script), close_fds=True)
        return

    # IMPORTANT (mode packagé) : une appli PyInstaller fenêtrée n'a AUCUN flux standard
    # valide -- sys.stdin/stdout/stderr valent None et les handles Windows correspondants
    # sont invalides. Un processus enfant hérite de ces handles cassés et peut mourir
    # aussitôt sans le moindre message : c'est exactement ce qui se passait ici, le
    # lanceur PowerShell ne démarrait jamais et l'appli ne revenait pas. On lui donne
    # donc explicitement des flux neutres. (Même raison que le capture_output=True
    # systématique des appels ffmpeg ailleurs dans le projet.)
    silent_io = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    # PIÈGE CENTRAL de la relance d'un exe --onefile (cause du "Failed to load Python DLL
    # ...\_MEIxxxxxx\python312.dll" observé en test) : le bootloader communique à son
    # processus Python, PAR VARIABLES D'ENVIRONNEMENT (_MEIPASS2 sur les anciennes
    # versions, _PYI_* sur les récentes), le dossier temporaire où il a déjà tout extrait.
    # Or nous héritons de ces variables, et tout processus que NOUS lançons en hérite à son
    # tour. Le successeur croit alors que son extraction est déjà faite et va chercher son
    # python312.dll dans le dossier de l'instance PRÉCÉDENTE... que celle-ci vient
    # justement de supprimer en se fermant. Il faut donc lui donner un environnement
    # nettoyé, pour qu'il se comporte exactement comme un lancement depuis l'Explorateur
    # et procède à sa propre extraction.
    clean_env = {
        key: value for key, value in os.environ.items()
        if key != "_MEIPASS2" and not key.startswith("_PYI")
    }

    exe = sys.executable
    work_dir = os.path.dirname(exe)

    # Processus dont il faut attendre la fin : le nôtre, plus le bootloader --onefile
    # qui nous a lancés (c'est LUI qui supprime le dossier temporaire, après notre sortie).
    pids = [str(os.getpid())]
    try:
        parent_pid = os.getppid()
        if parent_pid > 0:
            pids.append(str(parent_pid))
    except OSError:
        pass

    # Les apostrophes se doublent dans une chaîne PowerShell (chemin type C:\Users\O'Neil).
    ps_exe = exe.replace("'", "''")
    ps_dir = work_dir.replace("'", "''")
    command = (
        "Wait-Process -Id {pids} -Timeout 30 -ErrorAction SilentlyContinue; "
        # Petite marge après la mort du bootloader : la suppression du _MEIxxxx est
        # lancée juste avant qu'il rende la main, elle peut traîner un instant.
        "Start-Sleep -Milliseconds 700; "
        "Start-Process -FilePath '{exe}' -WorkingDirectory '{dir}'"
    ).format(pids=",".join(pids), exe=ps_exe, dir=ps_dir)

    # Chemin absolu de PowerShell plutôt que "powershell" tout court : le PATH vu par un
    # exe --onefile n'est pas celui d'un shell classique, on ne s'y fie pas.
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    if not os.path.isfile(powershell):
        powershell = "powershell"

    try:
        subprocess.Popen(
            [powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command", command],
            creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
            env=clean_env, **silent_io)
    except OSError:
        # PowerShell introuvable ou bloqué par une stratégie de sécurité : on relance
        # directement plutôt que de laisser l'utilisateur devant une appli qui s'est
        # fermée sans jamais revenir. Le risque de chevauchement décrit plus haut
        # réapparaît, mais mieux vaut ça que rien du tout.
        subprocess.Popen([exe], cwd=work_dir, close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS,
                         env=clean_env, **silent_io)


if __name__ == "__main__":
    ensure_dir(DEFAULT_OUTPUT_DIR)
    app = ToolboxApp()
    app.mainloop()

    # Changement de langue : la fenêtre est maintenant bien fermée, on peut lancer le
    # successeur puis rendre la main proprement.
    if app.restart_requested:
        relaunch_self()
        sys.exit(0)
