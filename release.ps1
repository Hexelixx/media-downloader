<#
.SYNOPSIS
    Publie une nouvelle version de la Boite a outils media sur GitHub.

.DESCRIPTION
    Enchaine, dans l'ordre, tout ce qu'il faut pour qu'une nouvelle version arrive
    jusqu'au bouton "Verifier les mises a jour" de l'appli installee chez papa :

      1. calcule le numero de version (date du jour, + suffixe .N si une version a
         deja ete publiee aujourd'hui) ;
      2. reecrit APP_VERSION dans common.py -- la SOURCE UNIQUE du numero de version ;
      3. reconstruit dist\MediaDownloader.exe avec PyInstaller ;
      4. commit + tag git + push ;
      5. cree la release GitHub avec l'exe en piece jointe.

    Pourquoi un script plutot que des instructions dans le README : la seule etape
    qu'on oublie systematiquement a la main (bumper la version) est aussi celle qui
    casse silencieusement tout le mecanisme -- une release taguee v2026.09.01 dont
    l'exe se croit encore en 2026.08.15 proposerait sa propre mise a jour en boucle.

.PARAMETER Version
    Force un numero de version precis (ex. 2026.12.25). Par defaut : date du jour.

.PARAMETER Notes
    Notes de version affichees sur GitHub ET dans la boite de dialogue de l'appli.

.PARAMETER SkipBuild
    Reutilise dist\MediaDownloader.exe tel quel au lieu de le reconstruire
    (utile seulement si tu viens de le construire a la main).

.PARAMETER DryRun
    Affiche tout ce qui serait fait, sans rien modifier, commiter ni publier.

.EXAMPLE
    .\release.ps1 -Notes "Ajout du bouton de mise a jour automatique"

.EXAMPLE
    .\release.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$Notes,
    [switch]$SkipBuild,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# git et gh ne sont pas toujours dans le PATH de la session courante (installes apres
# son ouverture) : on le rafraichit depuis le registre, sinon le script echoue avec un
# "terme non reconnu" alors que les outils sont bien installes.
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("PATH", "User")

$CommonPy = Join-Path $PSScriptRoot "common.py"
$SpecFile = Join-Path $PSScriptRoot "MediaDownloader.spec"
$ExePath  = Join-Path $PSScriptRoot "dist\MediaDownloader.exe"
$PyInstaller = Join-Path $PSScriptRoot ".venv\Scripts\pyinstaller.exe"

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Info($message) { Write-Host "    $message" -ForegroundColor DarkGray }

# --------------------------------------------------------------- Verifications ---
Write-Step "Verifications prealables"

foreach ($tool in @("git", "gh")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool est introuvable. Installe-le puis relance ce script."
    }
}
if (-not (Test-Path -LiteralPath $CommonPy)) { throw "common.py introuvable." }
if (-not $SkipBuild -and -not (Test-Path -LiteralPath $PyInstaller)) {
    throw "PyInstaller introuvable dans .venv. Cree l'environnement virtuel (voir README)."
}

# Un depot sale ferait entrer dans la release des modifications non relues. On tolere
# uniquement common.py, que le script s'apprete justement a modifier lui-meme.
$dirty = @(git status --porcelain | Where-Object { $_ -and ($_ -notmatch "common\.py$") })
if ($dirty.Count -gt 0) {
    Write-Host "Modifications non commitees :" -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
    throw "Commit ou remise de cote ces changements avant de publier une version."
}

# ------------------------------------------------------ Numero de version ---
Write-Step "Numero de version"

$currentVersion = (Select-String -LiteralPath $CommonPy -Pattern '^APP_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Info "Version actuelle dans common.py : $currentVersion"

git fetch --tags --quiet
$existingTags = @(git tag --list)

if (-not $Version) {
    # Versionnage par date : AAAA.MM.JJ, + .2, .3... si on publie plusieurs fois le
    # meme jour. Les zeros de tete sont conserves (2026.08.15 et non 2026.8.15) pour
    # que le numero se lise comme une date ; l'appli compare de toute facon en entiers.
    $base = Get-Date -Format "yyyy.MM.dd"
    $Version = $base
    $n = 2
    while ($existingTags -contains "v$Version") {
        $Version = "$base.$n"
        $n++
    }
}

if ($existingTags -contains "v$Version") {
    throw "Le tag v$Version existe deja. Passe -Version avec un autre numero."
}
Write-Info "Nouvelle version : $Version  (tag v$Version)"

if (-not $Notes) { $Notes = "Version $Version de la Boite a outils media." }

if ($DryRun) {
    Write-Step "DRY RUN -- rien n'a ete modifie"
    Write-Info "Aurait ecrit APP_VERSION = `"$Version`" dans common.py"
    Write-Info "Aurait reconstruit $ExePath"
    Write-Info "Aurait commite, tague v$Version et pousse sur origin"
    Write-Info "Aurait cree la release GitHub v$Version avec l'exe en piece jointe"
    return
}

# --------------------------------------------------- Ecriture de la version ---
Write-Step "Mise a jour de common.py"

$content = [System.IO.File]::ReadAllText($CommonPy)
$updated = [regex]::Replace($content, '(?m)^APP_VERSION\s*=\s*"[^"]*"', "APP_VERSION = `"$Version`"")
if ($updated -eq $content -and $currentVersion -ne $Version) {
    throw "La ligne APP_VERSION n'a pas pu etre mise a jour dans common.py."
}
# UTF8 SANS BOM : Set-Content -Encoding utf8 en ajouterait un sous PowerShell 5.1, ce
# qui salit inutilement un fichier source Python suivi par git.
[System.IO.File]::WriteAllText($CommonPy, $updated, (New-Object System.Text.UTF8Encoding($false)))
Write-Info "APP_VERSION = `"$Version`""

# ---------------------------------------------------------------- Build ---
if ($SkipBuild) {
    Write-Step "Build ignore (-SkipBuild)"
    if (-not (Test-Path -LiteralPath $ExePath)) { throw "dist\MediaDownloader.exe est absent : impossible d'ignorer le build." }
} else {
    Write-Step "Construction de l'executable (plusieurs minutes)"
    # Le fichier .spec et PAS la ligne de commande "--onefile --windowed" : la spec
    # contient les hiddenimports lxml sans lesquels le convertisseur de texte plante
    # une fois packagE.
    & $PyInstaller --noconfirm $SpecFile
    if ($LASTEXITCODE -ne 0) { throw "La construction de l'executable a echoue." }
}
if (-not (Test-Path -LiteralPath $ExePath)) { throw "dist\MediaDownloader.exe est introuvable apres le build." }
$sizeMb = [math]::Round((Get-Item -LiteralPath $ExePath).Length / 1MB, 1)
Write-Info "$ExePath ($sizeMb Mo)"

# ------------------------------------------------------------ Git + GitHub ---
Write-Step "Commit et tag"
git add -- common.py
# --allow-empty : si seule la version change et qu'elle etait deja a jour (rejeu d'une
# publication interrompue), on veut quand meme le commit qui porte le tag.
git commit --allow-empty -m "Version $Version"
if ($LASTEXITCODE -ne 0) { throw "Le commit a echoue." }
git tag "v$Version"
if ($LASTEXITCODE -ne 0) { throw "La creation du tag a echoue." }

Write-Step "Push vers GitHub"
git push origin HEAD
if ($LASTEXITCODE -ne 0) { throw "Le push a echoue." }
git push origin "v$Version"
if ($LASTEXITCODE -ne 0) { throw "Le push du tag a echoue." }

Write-Step "Creation de la release GitHub"
gh release create "v$Version" $ExePath --title "v$Version" --notes $Notes
if ($LASTEXITCODE -ne 0) { throw "La creation de la release a echoue." }

Write-Step "Termine"
Write-Host "Version $Version publiee." -ForegroundColor Green
Write-Info (gh release view "v$Version" --json url --jq .url)
Write-Info "Les applis deja installees la verront via le bouton 'Verifier les mises a jour'."
