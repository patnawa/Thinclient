<#
.SYNOPSIS
    Add TCCONF to a stick made from an older/custom ISO without persistence.

.DESCRIPTION
    Current ThinClient images already embed TCCONF. Use this legacy helper only
    if Write-UsbImage.ps1 reports that an older/custom image has no embedded
    settings partition and its automatic fallback could not create one.

    An isohybrid image leaves the GPT backup header at the end of the *image*
    rather than the end of the disk, which Windows' partition tools do not
    always cope with. sgdisk does: it relocates the backup header first, then
    creates the partition in the free space. So this hands the disk to WSL for
    the partitioning and gives it straight back.

    MUST BE RUN ELEVATED. Mounting a physical disk into WSL needs administrator
    rights, as does raw disk access generally.

.PARAMETER DiskNumber
    The physical disk holding the ThinClient ISO.

.PARAMETER Distro
    WSL distribution to borrow the disk tools from. Defaults to Debian.

.EXAMPLE
    .\Add-TcconfPartition.ps1 -DiskNumber 4
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)][int]$DiskNumber,
    [string]$Distro = 'Debian'
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated PowerShell."
}

$drive = Get-CimInstance Win32_DiskDrive | Where-Object Index -eq $DiskNumber
if (-not $drive) { throw "There is no physical disk $DiskNumber." }
if ($drive.MediaType -notlike '*Removable*') {
    throw "Disk $DiskNumber is not removable. Refusing."
}

Write-Host "Disk $DiskNumber : $($drive.Model), $([math]::Round($drive.Size/1GB,2)) GB"
if (-not $PSCmdlet.ShouldProcess("disk $DiskNumber", "add a TCCONF partition in the free space")) {
    return
}

$devicePath = "\\.\PHYSICALDRIVE$DiskNumber"

# The script that does the work, written to a temp file so no quoting from
# PowerShell has to survive the trip into bash.
$repo = Split-Path -Parent $PSScriptRoot
# The build exports the configuration with the real server address substituted.
# overlay\ holds the template with placeholders and must never be seeded.
$seed = Join-Path $repo 'out\config.json'
if (-not (Test-Path $seed)) {
    Write-Warning "out\config.json is missing - rebuild first. Creating TCCONF without a config."
    $seed = $null
}
$script = @'
#!/bin/bash
set -euo pipefail
DEV="${1:-}"
SEED="${2:-}"
[ -b "$DEV" ] || { echo "FAIL: selected WSL device is not a disk: $DEV"; exit 1; }
[ "$(blkid -o value -s LABEL "$DEV" 2>/dev/null)" = "THINCLIENT" ] \
    || { echo "FAIL: $DEV is not labelled THINCLIENT"; exit 1; }
echo "found $DEV"

if lsblk -nrpo LABEL "$DEV" | grep -Fxq TCCONF; then
    echo "a TCCONF partition already exists; nothing to do"
    echo "TCCONF_OK"
    exit 0
fi

# Move the GPT backup header to the true end of the device, otherwise there is
# no usable free space as far as the partition table is concerned.
sgdisk -e "$DEV" >/dev/null 2>&1

NEXT=$(sgdisk -p "$DEV" 2>/dev/null | awk '/^ *[0-9]+ /{n=$1} END{print n+1}')
[ -n "$NEXT" ] || NEXT=3
echo "creating partition $NEXT"
sgdisk -n "$NEXT:0:0" -t "$NEXT:0700" -c "$NEXT:TCCONF" "$DEV"
partprobe "$DEV" 2>/dev/null || true
sleep 2

PART="${DEV}${NEXT}"
[ -b "$PART" ] || PART="${DEV}p${NEXT}"
[ -b "$PART" ] || { echo "FAIL: $PART did not appear"; exit 1; }

mkfs.vfat -F 32 -n TCCONF "$PART" >/dev/null
MOUNTPOINT=$(mktemp -d /run/tcconf.XXXXXX)
MOUNTED=0
cleanup() {
    [ "$MOUNTED" -eq 0 ] || umount "$MOUNTPOINT" 2>/dev/null || true
    rmdir "$MOUNTPOINT" 2>/dev/null || true
}
trap cleanup EXIT
mount "$PART" "$MOUNTPOINT"
MOUNTED=1
mkdir -p "$MOUNTPOINT/ca-certificates"
if [ -n "$SEED" ] && [ -f "$SEED" ]; then
    cp -- "$SEED" "$MOUNTPOINT/config.json"
    echo "seeded config.json"
fi
sync
umount "$MOUNTPOINT"
MOUNTED=0
echo "TCCONF_OK"
'@

$token = [guid]::NewGuid().ToString('N')
$tempScript = Join-Path $env:TEMP "tc-add-tcconf-$token.sh"
$wslSeedPath = "/tmp/tc-seed-config-$token.json"
$script -replace "`r`n", "`n" | Set-Content -Path $tempScript -NoNewline -Encoding ascii

Write-Host "handing disk $DiskNumber to WSL ($Distro)"
$attached = $false
$output = @()
try {
    # Snapshot whole disks before attaching the selected Windows drive. The one
    # new name afterwards is the only safe mapping; scanning by label can select
    # another ThinClient stick when several are connected.
    wsl -d $Distro -u root -e true
    if ($LASTEXITCODE -ne 0) { throw "Could not start WSL distribution '$Distro'." }
    $before = @(wsl -d $Distro -u root -e lsblk -dn -o NAME)
    if ($LASTEXITCODE -ne 0) { throw "Could not list WSL disks before attach." }
    $before = @($before | ForEach-Object { "$($_)".Trim() } | Where-Object { $_ })

    wsl --mount --bare $devicePath
    if ($LASTEXITCODE -ne 0) { throw "WSL could not attach disk $DiskNumber." }
    $attached = $true
    Start-Sleep -Seconds 2

    $after = @(wsl -d $Distro -u root -e lsblk -dn -o NAME)
    if ($LASTEXITCODE -ne 0) { throw "Could not list WSL disks after attach." }
    $after = @($after | ForEach-Object { "$($_)".Trim() } | Where-Object { $_ })
    $newDevices = @($after | Where-Object { $_ -notin $before })
    if ($newDevices.Count -ne 1) {
        throw "Expected one newly attached WSL disk; found $($newDevices.Count)."
    }
    $linuxDevice = "/dev/$($newDevices[0])"

    $seedArgument = '-'
    if ($seed -and (Test-Path $seed)) {
        $wslSeed = wsl -d $Distro -u root -e wslpath -a $seed
        if ($LASTEXITCODE -ne 0 -or -not $wslSeed) {
            throw "Could not translate the seed configuration path for WSL."
        }
        wsl -d $Distro -u root -e cp -- $wslSeed $wslSeedPath
        if ($LASTEXITCODE -ne 0) { throw "Could not copy config.json into WSL." }
        $seedArgument = $wslSeedPath
    }
    $wslScript = wsl -d $Distro -u root -e wslpath -a $tempScript
    if ($LASTEXITCODE -ne 0 -or -not $wslScript) {
        throw "Could not translate the helper script path for WSL."
    }
    $output = @(wsl -d $Distro -u root -e bash $wslScript $linuxDevice $seedArgument 2>&1)
    $helperStatus = $LASTEXITCODE
    $output | ForEach-Object { "  $_" }
    if ($helperStatus -ne 0) { throw "The WSL partition helper failed." }
    if (-not ($output -match 'TCCONF_OK')) {
        throw "The WSL helper did not report success."
    }
}
finally {
    wsl -d $Distro -u root -e rm -f -- $wslSeedPath 2>$null
    if ($attached) {
        wsl --unmount $devicePath | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "WSL could not detach $devicePath; run: wsl --unmount $devicePath"
        }
    }
    Remove-Item -LiteralPath $tempScript -Force -ErrorAction SilentlyContinue
}

Write-Host "`nTCCONF created. Reconnect the stick and it will appear in Explorer." -ForegroundColor Green
Write-Host "Edit config.json on it to set your servers."
