"""
Mise à jour automatique de l'appli depuis les Releases GitHub du dépôt public.

Ce module ne contient AUCUN code d'interface : il expose des fonctions pures
(comparaison de versions, appel API, téléchargement, remplacement de l'exe) que
app.py pilote depuis un thread. Ça permet de le tester en ligne de commande sans
ouvrir de fenêtre, ce qui est précieux pour un mécanisme fragile par nature.

Aucun jeton d'authentification n'est stocké ni requis : le dépôt est public, et
l'API GitHub sert les releases d'un dépôt public en anonyme (limite : 60 requêtes
par heure et par adresse IP, largement suffisant pour un clic manuel).
"""

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

from common import (
    APP_VERSION, ps_quote, run_detached_powershell, wait_targets,
)
from i18n import t

GITHUB_OWNER = "Hexelixx"
GITHUB_REPO = "media-downloader"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

# Nom de l'exe publié en asset de release ET nom du fichier local à remplacer.
EXE_NAME = "MediaDownloader.exe"
# Nom temporaire du téléchargement, à côté de l'exe actuel. Sous Windows un exe en
# cours d'exécution est verrouillé : impossible de l'écraser, on ne peut que
# préparer son remplaçant à côté puis basculer une fois l'appli fermée.
NEW_EXE_NAME = "MediaDownloader_new.exe"
# Suffixe utilisé PENDANT le téléchargement : tant qu'il est là, le fichier est
# incomplet. On ne le renomme en .exe qu'une fois les octets tous reçus, pour
# qu'un téléchargement interrompu (coupure réseau, appli fermée) ne laisse jamais
# un exe tronqué que le script de remplacement installerait joyeusement.
PARTIAL_SUFFIX = ".part"

# GitHub renvoie 403 aux requêtes sans User-Agent : ce n'est pas optionnel.
USER_AGENT = f"MediaDownloader/{APP_VERSION} (+{RELEASES_PAGE})"

NETWORK_TIMEOUT = 15  # secondes, pour la requête API (petite réponse JSON)
DOWNLOAD_TIMEOUT = 60  # plus généreux : ~75 Mo à télécharger
CHUNK_SIZE = 256 * 1024


class UpdateError(Exception):
    """Erreur déjà formulée dans la langue de l'utilisateur, affichable telle quelle.

    Tout ce qui remonte à l'interface passe par ici : le code appelant n'a jamais à
    traduire ni à interpréter une exception réseau brute (dont le message est en
    anglais technique et souvent illisible pour l'utilisateur final).
    """


class UpdateCancelled(Exception):
    """Annulation demandée par l'utilisateur : pas une erreur, aucun message à afficher.
    Volontairement distincte d'UpdateError, pour que l'interface ne présente jamais un
    choix délibéré de l'utilisateur comme un échec."""


# ------------------------------------------------------------- Versions ---
_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)\s*$", re.IGNORECASE)


def parse_version(text):
    """Convertit "v2026.08.15" / "2026.8.15.2" en tuple d'entiers comparable.

    Retourne None si ce n'est pas un numéro de version reconnaissable -- on préfère
    ne rien proposer plutôt que de comparer n'importe quoi et pousser une "mise à
    jour" vers une release taguée à la main de travers.

    Le passage par des ENTIERS est essentiel : en comparaison de chaînes, "2026.9.1"
    serait considéré comme antérieur à "2026.10.1" (car "1" < "9" caractère par
    caractère), et une mise à jour d'octobre ne serait jamais détectée.
    """
    if not text:
        return None
    match = _VERSION_RE.match(str(text))
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(remote_version, local_version=APP_VERSION):
    """Vrai si `remote_version` est strictement postérieure à la version locale.

    Deux tuples de longueurs différentes se comparent naturellement en Python :
    (2026, 8, 15) < (2026, 8, 15, 2), ce qui donne exactement le comportement voulu
    pour le suffixe « Nième release du jour ».
    """
    remote = parse_version(remote_version)
    local = parse_version(local_version)
    if remote is None or local is None:
        return False
    return remote > local


# ---------------------------------------------------------- API GitHub ---
def _urlopen(url, timeout):
    """urlopen avec User-Agent, et messages d'erreur traduits pour tous les modes de
    défaillance réalistes (pas de connexion, GitHub en panne, quota dépassé...)."""
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 403 or e.code == 429:
            # Quota anonyme épuisé (60 requêtes/h/IP) : cas le plus probable d'un 403
            # sur un dépôt public, et le seul où réessayer plus tard suffit.
            raise UpdateError(t("update.error_rate_limit")) from e
        if e.code == 404:
            # Dépôt sans aucune release publiée : ce n'est pas une panne, c'est juste
            # qu'il n'y a rien à comparer.
            raise UpdateError(t("update.error_no_release")) from e
        raise UpdateError(t("update.error_http", code=e.code)) from e
    except urllib.error.URLError as e:
        # Englobe l'absence de connexion, le DNS qui ne résout pas, le timeout et les
        # erreurs de certificat.
        if isinstance(e.reason, ssl.SSLError):
            raise UpdateError(t("update.error_ssl")) from e
        raise UpdateError(t("update.error_network")) from e
    except OSError as e:
        raise UpdateError(t("update.error_network")) from e


def fetch_latest_release(timeout=NETWORK_TIMEOUT):
    """Interroge l'API GitHub et retourne un dict décrivant la dernière release :
    {version, tag, name, url, asset_url, asset_name, asset_size}.

    Lève UpdateError (message traduit) en cas de souci réseau, de release sans exe
    attaché, ou de tag illisible.
    """
    with _urlopen(LATEST_RELEASE_API, timeout) as response:
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise UpdateError(t("update.error_bad_response")) from e

    tag = payload.get("tag_name") or ""
    version = parse_version(tag)
    if version is None:
        raise UpdateError(t("update.error_bad_tag", tag=tag or "?"))

    # On cherche l'exe parmi les assets. `browser_download_url` est une URL publique
    # directe (redirigée vers le CDN), utilisable sans authentification.
    # MediaDownloader.exe est privilégié s'il est présent, mais n'importe quel .exe
    # fait l'affaire en repli : une release publiée à la main avec un nom légèrement
    # différent reste ainsi installable.
    assets = payload.get("assets") or []
    asset = next(
        (a for a in assets if (a.get("name") or "").lower() == EXE_NAME.lower()),
        None,
    ) or next(
        (a for a in assets if (a.get("name") or "").lower().endswith(".exe")),
        None,
    )

    return {
        "tag": tag,
        # On renvoie la version NORMALISÉE (sans le "v" du tag) : c'est elle qu'on
        # affiche et qu'on compare, le "v" n'est qu'une convention de nommage de tag.
        "version": ".".join(str(part) for part in version),
        "name": payload.get("name") or tag,
        "notes": (payload.get("body") or "").strip(),
        "url": payload.get("html_url") or RELEASES_PAGE,
        "asset_url": (asset or {}).get("browser_download_url"),
        "asset_name": (asset or {}).get("name"),
        "asset_size": (asset or {}).get("size") or 0,
    }


def check_for_update(timeout=NETWORK_TIMEOUT):
    """Retourne (update_disponible, infos_release). Ne lève que des UpdateError."""
    release = fetch_latest_release(timeout=timeout)
    return is_newer(release["version"]), release


# ------------------------------------------------------ Téléchargement ---
def target_paths():
    """(exe actuel, exe temporaire à télécharger à côté) -- uniquement pertinent en
    mode packagé, voir can_self_update()."""
    current = os.path.abspath(sys.executable)
    return current, os.path.join(os.path.dirname(current), NEW_EXE_NAME)


def can_self_update():
    """Le remplacement d'exe n'a de sens que sur une appli packagée. Lancée depuis les
    sources (python app.py), `sys.executable` est l'interpréteur Python : le remplacer
    par MediaDownloader.exe serait catastrophique. On refuse donc explicitement."""
    return bool(getattr(sys, "frozen", False))


def _writable_dir(path):
    """Teste réellement le droit d'écriture dans `path` (créer/supprimer un fichier),
    au lieu de se fier à os.access() qui ignore les ACL Windows et répond souvent
    « oui » à tort dans Program Files."""
    probe = os.path.join(path, ".mediadownloader_write_test")
    try:
        with open(probe, "wb"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def download_update(asset_url, expected_size=0, progress_cb=None, cancel_event=None):
    """Télécharge l'exe de la release à côté de l'exe actuel et retourne son chemin.

    `progress_cb(octets_reçus, octets_total)` est appelé régulièrement (depuis le
    thread appelant, donc l'UI doit passer par sa file d'attente habituelle).
    `cancel_event` (threading.Event) permet d'interrompre proprement.
    """
    if not asset_url:
        raise UpdateError(t("update.error_no_asset"))

    current_exe, new_exe = target_paths()
    folder = os.path.dirname(current_exe)
    if not _writable_dir(folder):
        # Détecté AVANT de télécharger 75 Mo pour rien : si le dossier n'est pas
        # inscriptible, le remplacement final échouerait de toute façon.
        raise UpdateError(t("update.error_folder_readonly", folder=folder))

    partial = new_exe + PARTIAL_SUFFIX
    for stale in (partial, new_exe):
        # Reste d'une tentative précédente interrompue : on repart propre.
        try:
            if os.path.exists(stale):
                os.remove(stale)
        except OSError as e:
            raise UpdateError(t("update.error_cleanup", path=stale, error=e)) from e

    downloaded = 0
    try:
        with _urlopen(asset_url, DOWNLOAD_TIMEOUT) as response:
            total = expected_size or int(response.headers.get("Content-Length") or 0)
            with open(partial, "wb") as out:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateCancelled()
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        progress_cb(downloaded, total)
    except UpdateCancelled:
        _silent_remove(partial)
        raise
    except UpdateError:
        _silent_remove(partial)
        raise
    except OSError as e:
        _silent_remove(partial)
        raise UpdateError(t("update.error_download", error=e)) from e

    # Garde-fou : un serveur qui coupe la connexion en cours de route donne un fichier
    # tronqué SANS lever d'exception. Comparer à la taille annoncée est le seul moyen
    # de le détecter -- et installer un exe tronqué rendrait l'appli non lançable.
    if expected_size and downloaded != expected_size:
        _silent_remove(partial)
        raise UpdateError(t("update.error_truncated"))

    try:
        os.replace(partial, new_exe)
    except OSError as e:
        _silent_remove(partial)
        raise UpdateError(t("update.error_download", error=e)) from e
    return new_exe


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ------------------------------------ Remplacement de l'exe + relance ---
def apply_update(new_exe=None):
    """Programme le remplacement de l'exe puis la relance, et rend la main aussitôt.

    L'appelant doit ensuite fermer l'appli SANS TARDER (la fenêtre puis le processus) :
    tout se joue dans un PowerShell détaché qui nous survit et qui, dans l'ordre :

      1. attend la fin réelle de notre processus ET du bootloader --onefile qui nous a
         lancés (sous Windows, un exe en cours d'exécution est verrouillé par le
         système : impossible de l'écraser tant qu'il vit) ;
      2. remplace MediaDownloader.exe par MediaDownloader_new.exe, EN RÉESSAYANT --
         c'est le point critique : le verrou du fichier n'est pas toujours relâché à
         l'instant précis où le processus meurt (l'antivirus temps réel garde couramment
         le handle une poignée de secondes après coup, et le bootloader supprime encore
         son dossier temporaire de ~190 Mo). Un Move-Item unique échouerait donc de
         façon INTERMITTENTE, exactement le genre de bug déjà rencontré sur ce projet ;
      3. relance l'appli -- le nouvel exe si le remplacement a réussi, l'ancien sinon,
         pour ne jamais laisser l'utilisateur devant une appli qui s'est fermée et
         n'est jamais revenue.

    Pourquoi PowerShell et pas du Python : seul un TIERS peut observer notre propre
    mort. Un thread à nous mourrait avec nous ; un enfant Python ferait le travail mais
    imposerait d'embarquer un second exe. PowerShell est déjà utilisé pour la relance
    au changement de langue, c'est la même brique éprouvée.
    """
    current_exe, default_new = target_paths()
    new_exe = new_exe or default_new
    if not os.path.isfile(new_exe):
        raise UpdateError(t("update.error_missing_new_exe", path=new_exe))

    work_dir = os.path.dirname(current_exe)
    pids = ",".join(wait_targets())

    # 40 tentatives x 500 ms = jusqu'à 20 s d'attente du déverrouillage : très large
    # au regard des ~1-3 s réellement observées, et invisible pour l'utilisateur (tout
    # ceci se passe fenêtre déjà fermée).
    command = (
        "Wait-Process -Id {pids} -Timeout 60 -ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 700; "
        "$ok = $false; "
        "for ($i = 0; $i -lt 40; $i++) {{ "
        "  try {{ "
        "    Move-Item -LiteralPath '{new}' -Destination '{cur}' -Force -ErrorAction Stop; "
        "    $ok = $true; break "
        "  }} catch {{ Start-Sleep -Milliseconds 500 }} "
        "}}; "
        "if ($ok) {{ Start-Process -FilePath '{cur}' -WorkingDirectory '{dir}' }} "
        "else {{ Start-Process -FilePath '{new}' -WorkingDirectory '{dir}' }}"
    ).format(pids=pids, new=ps_quote(new_exe), cur=ps_quote(current_exe),
             dir=ps_quote(work_dir))

    try:
        run_detached_powershell(command)
    except OSError as e:
        raise UpdateError(t("update.error_relaunch", error=e)) from e
