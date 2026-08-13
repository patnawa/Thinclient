<#
.SYNOPSIS
    Add the TCCONF settings partition to a stick that already holds the ISO.

.DESCRIPTION
    Use this if Write-UsbImage.ps1 wrote the image successfully but could not
    create the persistence partition.

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
set -u
# Find the disk we were just handed: an isohybrid ISO reports its iso9660
# label on the whole device, and ours is THINCLIENT.
DEV=""
for d in /dev/sd?; do
    [ -b "$d" ] || continue
    if [ "$(blkid -o value -s LABEL "$d" 2>/dev/null)" = "THINCLIENT" ]; then
        DEV="$d"; break
    fi
done
[ -n "$DEV" ] || { echo "FAIL: could not find the ThinClient disk in WSL"; exit 1; }
echo "found $DEV"

if blkid -L TCCONF >/dev/null 2>&1; then
    echo "a TCCONF partition already exists; nothing to do"
    exit 0
fi

# Move the GPT backup header to the true end of the device, otherwise there is
# no usable free space as far as the partition table is concerned.
sgdisk -e "$DEV" >/dev/null 2>&1

NEXT=$(sgdisk -p "$DEV" 2>/dev/null | awk '/^ *[0-9]+ /{n=$1} END{print n+1}')
[ -n "$NEXT" ] || NEXT=3
echo "creating partition $NEXT"
sgdisk -n "$NEXT:0:0" -t "$NEXT:0700" -c "$NEXT:TCCONF" "$DEV" || exit 1
partprobe "$DEV" 2>/dev/null
sleep 2

PART="${DEV}${NEXT}"
[ -b "$PART" ] || PART="${DEV}p${NEXT}"
[ -b "$PART" ] || { echo "FAIL: $PART did not appear"; exit 1; }

mkfs.vfat -F 32 -n TCCONF "$PART" >/dev/null || exit 1
mkdir -p /mnt/tcconf
mount "$PART" /mnt/tcconf || exit 1
mkdir -p /mnt/tcconf/ca-certificates
if [ -f /tmp/tc-seed-config.json ]; then
    cp /tmp/tc-seed-config.json /mnt/tcconf/config.json
    echo "seeded config.json"
fi
sync
umount /mnt/tcconf
echo "TCCONF_OK"
'@

$tempScript = Join-Path $env:TEMP 'tc-add-tcconf.sh'
$script -replace "`r`n", "`n" | Set-Content -Path $tempScript -NoNewline -Encoding utf8

Write-Host "handing disk $DiskNumber to WSL ($Distro)"
wsl --mount --bare $devicePath
try {
    if ($seed -and (Test-Path $seed)) {
        $wslSeed = wsl -d $Distro -- wslpath -a $seed
        wsl -d $Distro -u root -- cp $wslSeed /tmp/tc-seed-config.json
    }
    $wslScript = wsl -d $Distro -- wslpath -a $tempScript
    $output = wsl -d $Distro -u root -- bash $wslScript
    $output | ForEach-Object { "  $_" }
}
finally {
    wsl --unmount $devicePath | Out-Null
}

if ($output -match 'TCCONF_OK') {
    Write-Host "`nTCCONF created. Reconnect the stick and it will appear in Explorer." -ForegroundColor Green
    Write-Host "Edit config.json on it to set your servers."
}
else {
    Write-Host "`nDid not complete. The stick still boots; settings just will not persist." -ForegroundColor Yellow
}
