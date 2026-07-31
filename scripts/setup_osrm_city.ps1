<#
.SYNOPSIS
    Prepares OSRM datasets for GLIMS.

.DESCRIPTION
    For each configured city and transport profile:
      - Creates the required directories.
      - Creates a hard link to the city PBF.
      - Runs:
            osrm-extract
            osrm-partition
            osrm-customize
      - Starts the OSRM server.

.NOTES
    Expected directory layout:

    data/osmr/
        madrid.osm.pbf
        barcelona.osm.pbf
        valencia.osm.pbf

        madrid/
        barcelona/
        valencia/
#>

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

$Root = Resolve-Path "$PSScriptRoot\..\data\osmr"

$Cities = @(
    "madrid",
    "barcelona",
    "valencia"
)

$Profiles = @(
    @{
        Name = "driving"
        Lua  = "/opt/car.lua"
        Port = 5000
    },
    @{
        Name = "cycling"
        Lua  = "/opt/bicycle.lua"
        Port = 5001
    },
    @{
        Name = "walking"
        Lua  = "/opt/foot.lua"
        Port = 5002
    }
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

function Invoke-Docker {

    param(
        [string[]]$Arguments,
        [string]$Description
    )

    Write-Host ""
    Write-Host "====================================================="
    Write-Host $Description
    Write-Host "====================================================="

    docker @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed."
    }
}

function Process-Profile {

    param(
        [string]$City,
        [hashtable]$Profile
    )

    $profileName = $Profile.Name

    $cityFolder = Join-Path $Root $City
    $profileFolder = Join-Path $cityFolder $profileName

    New-Item -ItemType Directory -Force $profileFolder | Out-Null

    $sourcePbf = Join-Path $Root "$City.osm.pbf"

    if (!(Test-Path $sourcePbf)) {
        throw "Missing PBF: $sourcePbf"
    }

    $linkedPbf = Join-Path $profileFolder "$City-$profileName.osm.pbf"

    if (!(Test-Path $linkedPbf)) {

        New-Item `
            -ItemType HardLink `
            -Path $linkedPbf `
            -Target $sourcePbf `
            | Out-Null
    }

    $containerPath = "/data/$City/$profileName"

    Invoke-Docker `
        -Description "$City - $profileName - extract" `
        -Arguments @(
            "run","--rm",
            "-t",
            "-v","${Root}:/data",
            "osrm/osrm-backend:latest",
            "osrm-extract",
            "-p",$Profile.Lua,
            "$containerPath/$City-$profileName.osm.pbf"
        )

    Invoke-Docker `
        -Description "$City - $profileName - partition" `
        -Arguments @(
            "run","--rm",
            "-t",
            "-v","${Root}:/data",
            "osrm/osrm-backend:latest",
            "osrm-partition",
            "$containerPath/$City-$profileName.osrm"
        )

    Invoke-Docker `
        -Description "$City - $profileName - customize" `
        -Arguments @(
            "run","--rm",
            "-t",
            "-v","${Root}:/data",
            "osrm/osrm-backend:latest",
            "osrm-customize",
            "$containerPath/$City-$profileName.osrm"
        )
}

function Start-Server {

    param(
        [string]$City,
        [hashtable]$Profile,
        [int]$CityOffset
    )

    $hostPort = $Profile.Port + $CityOffset

    $containerName = "osrm-$City-$($Profile.Name)"

    $existingContainer = docker ps `
        -aq `
        --filter "name=^/${containerName}$"

    if ($existingContainer) {
        docker rm -f $containerName | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Unable to remove existing container: $containerName"
        }
    }

    docker run `
        -d `
        --restart unless-stopped `
        --name $containerName `
        -p "${hostPort}:5000" `
        -v "${Root}\$City\$($Profile.Name):/data" `
        osrm/osrm-backend:latest `
        osrm-routed `
        --algorithm mld `
        --max-table-size 1000 `
        /data/$City-$($Profile.Name).osrm

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to start $containerName"
    }

    Write-Host "Started $containerName on localhost:$hostPort"
}

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

Write-Host ""
Write-Host "Preparing OSRM datasets..."
Write-Host ""

foreach ($city in $Cities) {

    Write-Host ""
    Write-Host "###############################################"
    Write-Host "CITY: $city"
    Write-Host "###############################################"

    foreach ($profile in $Profiles) {

        Process-Profile `
            -City $city `
            -Profile $profile
    }
}

Write-Host ""
Write-Host "All datasets generated."
Write-Host ""

$offset = 0

foreach ($city in $Cities) {

    foreach ($profile in $Profiles) {

        Start-Server `
            -City $city `
            -Profile $profile `
            -CityOffset $offset
    }

    $offset += 10
}

Write-Host ""
Write-Host "Done."