param(
    [Parameter(Mandatory=$true, HelpMessage="Chemin du dossier source")]
    [string]$source,
    
    [Parameter(Mandatory=$true, HelpMessage="Chemin du dossier destination")]
    [string]$destination,
    
    [Parameter(Mandatory=$false, HelpMessage="Remplacer les fichiers existants")]
    [switch]$replace
)

# Normaliser les chemins (enlever les \ finaux)
$source = $source.TrimEnd('\')
$destination = $destination.TrimEnd('\')

# Vérifier que le dossier source existe
if (!(Test-Path -LiteralPath $source)) {
    Write-Host "ERREUR : Le dossier source n'existe pas : $source" -ForegroundColor Red
    exit 1
}

# Vérifier que le dossier destination existe
if (!(Test-Path -LiteralPath $destination)) {
    Write-Host "ATTENTION : Le dossier destination n'existe pas : $destination" -ForegroundColor Yellow
    $createDest = Read-Host "Voulez-vous le créer ? (O/N)"
    if ($createDest.ToUpper() -eq 'O') {
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Write-Host "Dossier destination créé." -ForegroundColor Green
    } else {
        Write-Host "Opération annulée." -ForegroundColor Red
        exit 1
    }
}

# Extensions de métadonnées tinyMediaManager
$extensions = @('*.nfo', '*.jpg', '*.png', '*.xml', '*.tbn')

# Dictionnaire pour mémoriser les choix de l'utilisateur par dossier parent
$folderDecisions = @{}

# Compteurs
$copiedCount = 0
$skippedCount = 0

Write-Host "`nDébut de la copie des métadonnées..." -ForegroundColor Cyan
Write-Host "Source : $source" -ForegroundColor Cyan
Write-Host "Destination : $destination" -ForegroundColor Cyan
Write-Host "Mode remplacement : $($replace -eq $true)`n" -ForegroundColor Cyan

Get-ChildItem -Path $source -Recurse -Include $extensions | ForEach-Object {
    # Obtenir le chemin relatif depuis la source
    $relativePath = $_.FullName.Substring($source.Length).TrimStart('\')
    $destPath = Join-Path $destination $relativePath
    $destDir = Split-Path $destPath -Parent
    
    if (!(Test-Path -LiteralPath $destDir)) {
        # Vérifier si on a déjà pris une décision pour ce dossier
        if ($folderDecisions.ContainsKey($destDir)) {
            $create = $folderDecisions[$destDir]
        } else {
            Write-Host "`nLe dossier n'existe pas : $destDir" -ForegroundColor Yellow
            Write-Host "Fichier à copier : $($_.Name)" -ForegroundColor Cyan
            
            $response = Read-Host "Voulez-vous créer ce dossier ? (O/N/T pour Tous)"
            
            switch ($response.ToUpper()) {
                'O' { $create = $true }
                'T' { 
                    $create = $true
                    # Créer automatiquement tous les dossiers suivants
                    $folderDecisions['*'] = $true
                }
                default { $create = $false }
            }
            
            # Mémoriser la décision sauf si "Tous" a été choisi
            if ($response.ToUpper() -ne 'T') {
                $folderDecisions[$destDir] = $create
            }
        }
        
        if ($create -or $folderDecisions.ContainsKey('*')) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            Write-Host "Dossier créé : $destDir" -ForegroundColor Green
        } else {
            Write-Host "Fichier ignoré : $($_.FullName)" -ForegroundColor Gray
            $skippedCount++
            return
        }
    }
    
    # Vérifier si le fichier existe déjà dans la destination
    if ((Test-Path -LiteralPath $destPath) -and !$replace) {
        Write-Host "Fichier déjà existant (ignoré) : $($_.Name)" -ForegroundColor DarkGray
        $skippedCount++
        return
    }
    
    Copy-Item -LiteralPath $_.FullName -Destination $destPath -Force
    if ($replace -and (Test-Path -LiteralPath $destPath)) {
        Write-Host "Remplacé : $($_.Name)" -ForegroundColor Yellow
    } else {
        Write-Host "Copié : $($_.Name)" -ForegroundColor Green
    }
    $copiedCount++
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Copie terminée !" -ForegroundColor Green
Write-Host "Fichiers copiés/remplacés : $copiedCount" -ForegroundColor Green
Write-Host "Fichiers ignorés : $skippedCount" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan