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

# Compteurs globaux
$totalCopied = 0
$totalSkipped = 0
$totalProcessed = 0

# Compteurs par dossier parent
$parentFolderCopied = 0
$parentFolderSkipped = 0
$parentFolderProcessed = 0

$currentParentFolder = ""
$lastLineLength = 0

# Fonction pour obtenir le dossier parent (premier niveau sous la source)
function Get-ParentFolder {
    param([string]$fullPath)
    
    $relativePath = $fullPath.Substring($source.Length).TrimStart('\')
    $firstSeparator = $relativePath.IndexOf('\')
    
    if ($firstSeparator -gt 0) {
        return Join-Path $source $relativePath.Substring(0, $firstSeparator)
    } else {
        return $null  # Fichier directement dans le dossier source
    }
}

Clear-Host
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Copie des métadonnées tinyMediaManager" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Source : $source" -ForegroundColor White
Write-Host "Destination : $destination" -ForegroundColor White
Write-Host "Mode remplacement : $($replace -eq $true)" -ForegroundColor White
Write-Host "========================================`n" -ForegroundColor Cyan

Get-ChildItem -Path $source -Recurse -Include $extensions | ForEach-Object {
    $file = $_
    
    # Obtenir le chemin relatif depuis la source
    $relativePath = $file.FullName.Substring($source.Length).TrimStart('\')
    $destPath = Join-Path $destination $relativePath
    $destDir = Split-Path $destPath -Parent
    
    # Obtenir le dossier parent (premier niveau)
    $parentFolder = Get-ParentFolder -fullPath $file.DirectoryName
    
    # Ignorer les fichiers directement dans le dossier source (pas dans un sous-dossier)
    if ($null -eq $parentFolder) {
        # Traiter quand même le fichier, mais sans l'afficher dans une ligne séparée
        if (!(Test-Path -LiteralPath $destDir)) {
            if ($folderDecisions.ContainsKey($destDir)) {
                $create = $folderDecisions[$destDir]
            } else {
                Write-Host "`r$(' ' * $lastLineLength)`r" -NoNewline
                Write-Host "`nLe dossier n'existe pas : $destDir" -ForegroundColor Yellow
                Write-Host "Fichier à copier : $($file.Name)" -ForegroundColor Cyan
                
                $response = Read-Host "Voulez-vous créer ce dossier ? (O/N/T pour Tous)"
                
                switch ($response.ToUpper()) {
                    'O' { $create = $true }
                    'T' { 
                        $create = $true
                        $folderDecisions['*'] = $true
                    }
                    default { $create = $false }
                }
                
                if ($response.ToUpper() -ne 'T') {
                    $folderDecisions[$destDir] = $create
                }
            }
            
            if ($create -or $folderDecisions.ContainsKey('*')) {
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
                Write-Host "Dossier créé : $destDir" -ForegroundColor Green
            } else {
                $totalSkipped++
                $totalProcessed++
                return
            }
        }
        
        if ((Test-Path -LiteralPath $destPath) -and !$replace) {
            $totalSkipped++
        } else {
            Copy-Item -LiteralPath $file.FullName -Destination $destPath -Force
            $totalCopied++
        }
        $totalProcessed++
        return
    }
    
    # Détecter si on change de dossier parent
    if ($parentFolder -ne $currentParentFolder) {
        # Si ce n'est pas le premier dossier, passer à la ligne
        if ($currentParentFolder -ne "") {
            Write-Host ""
        }
        
        $currentParentFolder = $parentFolder
        $parentFolderCopied = 0
        $parentFolderSkipped = 0
        $parentFolderProcessed = 0
    }
    
    # Traiter le fichier
    $fileProcessed = $false
    
    if (!(Test-Path -LiteralPath $destDir)) {
        # Effacer la ligne actuelle avant d'afficher le prompt
        Write-Host "`r$(' ' * $lastLineLength)`r" -NoNewline
        
        # Vérifier si on a déjà pris une décision pour ce dossier
        if ($folderDecisions.ContainsKey($destDir)) {
            $create = $folderDecisions[$destDir]
        } else {
            Write-Host "`nLe dossier n'existe pas : $destDir" -ForegroundColor Yellow
            Write-Host "Fichier à copier : $($file.Name)" -ForegroundColor Cyan
            
            $response = Read-Host "Voulez-vous créer ce dossier ? (O/N/T pour Tous)"
            
            switch ($response.ToUpper()) {
                'O' { $create = $true }
                'T' { 
                    $create = $true
                    $folderDecisions['*'] = $true
                }
                default { $create = $false }
            }
            
            if ($response.ToUpper() -ne 'T') {
                $folderDecisions[$destDir] = $create
            }
        }
        
        if ($create -or $folderDecisions.ContainsKey('*')) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            Write-Host "Dossier créé : $destDir" -ForegroundColor Green
        } else {
            $fileProcessed = $true
            $parentFolderSkipped++
            $totalSkipped++
        }
    }
    
    if (!$fileProcessed) {
        # Vérifier si le fichier existe déjà dans la destination
        if ((Test-Path -LiteralPath $destPath) -and !$replace) {
            $parentFolderSkipped++
            $totalSkipped++
        } else {
            Copy-Item -LiteralPath $file.FullName -Destination $destPath -Force
            $parentFolderCopied++
            $totalCopied++
        }
        $fileProcessed = $true
    }
    
    if ($fileProcessed) {
        $parentFolderProcessed++
        $totalProcessed++
    }
    
    # Afficher la ligne de progression avec padding de 3 digits
    $statusLine = "[copiés : {0,3} | ignorés : {1,3} | traités : {2,3}] {3}" -f $parentFolderCopied, $parentFolderSkipped, $parentFolderProcessed, $currentParentFolder
    $padding = if ($statusLine.Length -lt $lastLineLength) { ' ' * ($lastLineLength - $statusLine.Length) } else { '' }
    Write-Host "`r$statusLine$padding" -NoNewline -ForegroundColor Cyan
    $lastLineLength = $statusLine.Length
}

# Passer à la ligne après le dernier dossier
if ($currentParentFolder -ne "") {
    Write-Host ""
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Copie terminée !" -ForegroundColor Green
Write-Host "Total fichiers traités : $totalProcessed" -ForegroundColor White
Write-Host "Total fichiers copiés/remplacés : $totalCopied" -ForegroundColor Green
Write-Host "Total fichiers ignorés : $totalSkipped" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan