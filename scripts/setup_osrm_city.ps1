<#
.SYNOPSIS
    Downloads missing OSM PBF extracts, prepares OSRM datasets, and starts servers.

.DESCRIPTION
    Cross-platform PowerShell script for Windows and Linux.
    Data-source URLs are read from configs/osrm_sources.csv.

.PARAMETER ForceDownload
    Re-download PBF files even when they already exist.

.PARAMETER SkipBuild
    Skip extract/partition/customize and only start servers.

.PARAMETER SkipStart
    Build datasets but do not start servers.
#>

param(
    [switch]$ForceDownload,
    [switch]$SkipBuild,
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..")
)

$Root = Join-Path -Path $ProjectRoot -ChildPath "data"
$Root = Join-Path -Path $Root -ChildPath "osrm"

$SourcesFile = Join-Path -Path $ProjectRoot -ChildPath "configs"
$SourcesFile = Join-Path -Path $SourcesFile -ChildPath "osrm_sources.csv"

$Image = "osrm/osrm-backend:latest"

New-Item -ItemType Directory -Force -Path $Root | Out-Null

if (!(Test-Path $SourcesFile)) {
    throw "Missing OSRM sources configuration: $SourcesFile"
}

$Cities = Import-Csv $SourcesFile
if (!$Cities) {
    throw "No cities configured in $SourcesFile"
}

$Profiles = @(
    @{ Name = "driving"; Lua = "/opt/car.lua";     Port = 5000 },
    @{ Name = "cycling"; Lua = "/opt/bicycle.lua"; Port = 5001 },
    @{ Name = "walking"; Lua = "/opt/foot.lua";    Port = 5002 }
)

function Assert-Command {
    param([string]$Name)
    if (!(Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Invoke-Docker {
    param([string[]]$Arguments, [string]$Description)

    Write-Host ""
    Write-Host "====================================================="
    Write-Host $Description
    Write-Host "====================================================="

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: $Description"
    }
}

function Download-Pbf {
    param([pscustomobject]$CityConfig)

    $city = $CityConfig.city.Trim().ToLowerInvariant()
    $url = $CityConfig.pbf_url.Trim()
    $destination = Join-Path $Root "$city.osm.pbf"

    if ((Test-Path $destination) -and !$ForceDownload) {
        Write-Host "Using existing PBF: $destination"
        return
    }

    if ([string]::IsNullOrWhiteSpace($url)) {
        throw "No pbf_url configured for city '$city' in $SourcesFile"
    }

    $partial = "$destination.part"
    Remove-Item $partial -Force -ErrorAction SilentlyContinue

    Write-Host "Downloading $city from $url"
    Invoke-WebRequest -Uri $url -OutFile $partial -UseBasicParsing

    if (!(Test-Path $partial) -or (Get-Item $partial).Length -eq 0) {
        throw "Download failed or produced an empty file: $url"
    }

    Move-Item -Force $partial $destination
    Write-Host "Downloaded: $destination"
}

function Ensure-LinkedPbf {
    param([string]$Source, [string]$Destination)

    if (Test-Path $Destination) {
        return
    }

    try {
        New-Item -ItemType HardLink -Path $Destination -Target $Source | Out-Null
    }
    catch {
        Write-Warning "Hard link failed; copying PBF instead: $Destination"
        Copy-Item -Path $Source -Destination $Destination
    }
}

function Process-Profile {
    param([string]$City, [hashtable]$Profile)

    $profileName = $Profile.Name
    $cityFolder = Join-Path $Root $City
    $profileFolder = Join-Path $cityFolder $profileName
    New-Item -ItemType Directory -Force -Path $profileFolder | Out-Null

    $sourcePbf = Join-Path $Root "$City.osm.pbf"
    if (!(Test-Path $sourcePbf)) {
        throw "Missing PBF after download step: $sourcePbf"
    }

    $linkedPbf = Join-Path $profileFolder "$City-$profileName.osm.pbf"
    Ensure-LinkedPbf -Source $sourcePbf -Destination $linkedPbf

    $containerPath = "/data/$City/$profileName"
    $mount = "${Root}:/data"

    Invoke-Docker -Description "$City - $profileName - extract" -Arguments @(
        "run", "--rm", "-t",
        "-v", $mount,
        $Image,
        "osrm-extract", "-p", $Profile.Lua,
        "$containerPath/$City-$profileName.osm.pbf"
    )

    Invoke-Docker -Description "$City - $profileName - partition" -Arguments @(
        "run", "--rm", "-t",
        "-v", $mount,
        $Image,
        "osrm-partition",
        "$containerPath/$City-$profileName.osrm"
    )

    Invoke-Docker -Description "$City - $profileName - customize" -Arguments @(
        "run", "--rm", "-t",
        "-v", $mount,
        $Image,
        "osrm-customize",
        "$containerPath/$City-$profileName.osrm"
    )
}

function Start-Server {
    param([pscustomobject]$CityConfig, [hashtable]$Profile)

    $city = $CityConfig.city.Trim().ToLowerInvariant()
    $offset = [int]$CityConfig.port_offset
    $hostPort = [int]$Profile.Port + $offset
    $containerName = "osrm-$city-$($Profile.Name)"
    $profileFolder = Join-Path (Join-Path $Root $city) $Profile.Name
    $mount = "${profileFolder}:/data"

    $existingContainer = & docker ps -aq --filter "name=^/${containerName}$"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker containers"
    }

    if ($existingContainer) {
        & docker rm -f $containerName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to remove existing container: $containerName"
        }
    }

    & docker run -d `
        --restart unless-stopped `
        --name $containerName `
        -p "${hostPort}:5000" `
        -v $mount `
        $Image `
        osrm-routed `
        --algorithm mld `
        --max-table-size 1000 `
        "/data/$city-$($Profile.Name).osrm"

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to start $containerName"
    }

    Write-Host "Started $containerName on localhost:$hostPort"
}

Assert-Command docker
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but the daemon is unavailable or your user lacks permission."
}

Write-Host "OSRM data root: $Root"

foreach ($cityConfig in $Cities) {
    Download-Pbf -CityConfig $cityConfig
}

if (!$SkipBuild) {
    foreach ($cityConfig in $Cities) {
        $city = $cityConfig.city.Trim().ToLowerInvariant()
        foreach ($profile in $Profiles) {
            Process-Profile -City $city -Profile $profile
        }
    }
    Write-Host "All datasets generated."
}

if (!$SkipStart) {
    foreach ($cityConfig in $Cities) {
        foreach ($profile in $Profiles) {
            Start-Server -CityConfig $cityConfig -Profile $profile
        }
    }
}

Write-Host "Done."
