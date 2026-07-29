param(
    [string]$DataRoot = "E:\UPV\Proyectos\GLIMS\data\osmr\madrid"
)

$servers = @(
    @{ Name="osrm-madrid-driving";   City="madrid";   Folder="driving";  Dataset="madrid-driving.osrm";   Port=5000 },
    @{ Name="osrm-madrid-cycling";   City="madrid";   Folder="cycling";  Dataset="madrid-cycling.osrm";   Port=5001 },
    @{ Name="osrm-madrid-walking";   City="madrid";   Folder="walking";  Dataset="madrid-walking.osrm";   Port=5002 },

    @{ Name="osrm-barcelona-driving"; City="barcelona"; Folder="driving"; Dataset="barcelona-driving.osrm"; Port=5010 },
    @{ Name="osrm-barcelona-cycling"; City="barcelona"; Folder="cycling"; Dataset="barcelona-cycling.osrm"; Port=5011 },
    @{ Name="osrm-barcelona-walking"; City="barcelona"; Folder="walking"; Dataset="barcelona-walking.osrm"; Port=5012 },

    @{ Name="osrm-valencia-driving"; City="valencia"; Folder="driving"; Dataset="valencia-driving.osrm"; Port=5020 },
    @{ Name="osrm-valencia-cycling"; City="valencia"; Folder="cycling"; Dataset="valencia-cycling.osrm"; Port=5021 },
    @{ Name="osrm-valencia-walking"; City="valencia"; Folder="walking"; Dataset="valencia-walking.osrm"; Port=5022 }
)

foreach ($server in $servers) {

    $profilePath = Join-Path $PSScriptRoot "..\data\osmr\$($server.City)\$($server.Folder)"
    $profilePath = (Resolve-Path $profilePath).Path
   
    if (-not (Test-Path $profilePath)) {
        throw "No existe la carpeta: $profilePath"
    }

    docker rm -f $server.Name 2>$null | Out-Null

    docker run -d `
        --name $server.Name `
        -p "$($server.Port):5000" `
        -v "${profilePath}:/data" `
        ghcr.io/project-osrm/osrm-backend:latest  `
        osrm-routed `
            --algorithm mld `
            --max-table-size 1000 `
            "/data/$($server.Dataset)"

    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo iniciar $($server.Name)"
    }

    Write-Host "$($server.Name) iniciado en el puerto $($server.Port)"
}