<#
.SYNOPSIS
    Write the ThinClient ISO to a USB stick, safely.

.DESCRIPTION
    The ISO is isohybrid: it is simultaneously a CD image and a disk image, so
    it must be written raw to the whole device, not copied onto a formatted
    volume. This script does that, and refuses to do it to the wrong disk.

    Guards, in order:
      * the target must be removable, unless you pass -AllowFixedDisk
      * the target must not hold any drive letter that looks like a system disk
      * the disk model and size are printed and must be confirmed
      * the write is verified by reading the bytes back and comparing hashes

    Current images already contain a small FAT32 TCCONF settings partition. The
    script exposes that volume in Windows after writing; older images fall back
    to creating it in the unused space on the stick.

    MUST BE RUN ELEVATED. Raw access to a physical disk requires administrator
    rights on Windows.

.PARAMETER DiskNumber
    The physical disk number to overwrite. Run without it to list candidates.

.PARAMETER IsoPath
    ISO to write. Defaults to the newest one in out\.

.PARAMETER SkipVerify
    Skip the read-back verification (faster, but you lose the one check that
    proves the stick actually holds what you think it does).

.PARAMETER NoPersistence
    Do not create a settings partition when writing an older image that does
    not contain one. It cannot remove TCCONF from a current embedded image.

.EXAMPLE
    .\Write-UsbImage.ps1                 # list removable disks, write nothing
    .\Write-UsbImage.ps1 -DiskNumber 4
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [int]$DiskNumber = -1,
    [string]$IsoPath,
    [switch]$AllowFixedDisk,
    [switch]$SkipVerify,
    [switch]$NoPersistence
)

$ErrorActionPreference = 'Stop'

function Get-Candidates {
    # Map physical disk index -> drive letters. Win32_LogicalDiskToPartition
    # names its antecedent "Disk #4, Partition #0", which is far easier to read
    # than building ASSOCIATORS queries out of escaped device paths.
    $letters = @{}
    foreach ($link in Get-CimInstance Win32_LogicalDiskToPartition) {
        if ("$($link.Antecedent.DeviceID)" -match 'Disk #(\d+)') {
            $index = [int]$Matches[1]
            if (-not $letters.ContainsKey($index)) { $letters[$index] = @() }
            $letters[$index] += "$($link.Dependent.DeviceID)"
        }
    }

    Get-CimInstance Win32_DiskDrive | ForEach-Object {
        [pscustomobject]@{
            Number    = $_.Index
            Model     = $_.Model
            Interface = $_.InterfaceType
            Removable = ($_.MediaType -like '*Removable*')
            SizeGB    = [math]::Round($_.Size / 1GB, 2)
            Letters   = (($letters[[int]$_.Index] | Sort-Object -Unique) -join ' ')
        }
    } | Sort-Object Number
}

# ------------------------------------------------------------------ listing --
$candidates = Get-Candidates
if ($DiskNumber -lt 0) {
    Write-Host "Physical disks on this machine:`n" -ForegroundColor Cyan
    $candidates | Format-Table -AutoSize
    Write-Host "Nothing was written. Re-run with -DiskNumber <n> to write." -ForegroundColor Yellow
    return
}

# ---------------------------------------------------------------- preflight --
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated PowerShell - raw disk access needs administrator rights."
}

$target = $candidates | Where-Object Number -eq $DiskNumber
if (-not $target) { throw "There is no physical disk $DiskNumber." }

if (-not $target.Removable -and -not $AllowFixedDisk) {
    throw ("Disk $DiskNumber ($($target.Model), $($target.SizeGB) GB) is a FIXED disk, " +
           "not removable. Refusing. Pass -AllowFixedDisk only if you are certain.")
}
if ($target.Letters -match '\bC:') {
    throw "Disk $DiskNumber holds C:. Refusing outright."
}

if (-not $IsoPath) {
    $repo = Split-Path -Parent $PSScriptRoot
    $IsoPath = (Get-ChildItem (Join-Path $repo 'out') -Filter *.iso -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $IsoPath -or -not (Test-Path $IsoPath)) { throw "No ISO found. Pass -IsoPath." }

$iso = Get-Item $IsoPath
$isoBytes = $iso.Length
if ($isoBytes -gt ($target.SizeGB * 1GB)) {
    throw "The ISO ($([math]::Round($isoBytes/1MB)) MB) is larger than disk $DiskNumber."
}

Write-Host ""
Write-Host "  ISO    : $($iso.FullName)"
Write-Host "  Size   : $([math]::Round($isoBytes/1MB,1)) MB"
Write-Host "  TARGET : disk $DiskNumber - $($target.Model), $($target.SizeGB) GB" -ForegroundColor Yellow
if ($target.Letters) { Write-Host "  Letters: $($target.Letters)" -ForegroundColor Yellow }
Write-Host ""
Write-Host "  EVERYTHING ON THIS DISK WILL BE DESTROYED." -ForegroundColor Red
Write-Host ""

if (-not $PSCmdlet.ShouldProcess("disk $DiskNumber ($($target.Model))", "OVERWRITE with $($iso.Name)")) {
    Write-Host "Nothing was written." -ForegroundColor Yellow
    return
}

# -------------------------------------------------------------------- write --
# Taking the disk offline releases the volume locks that would otherwise make
# Windows refuse or silently corrupt a raw write.
$originalDisk = Get-Disk -Number $DiskNumber
$originalOffline = [bool]$originalDisk.IsOffline
$originalReadOnly = [bool]$originalDisk.IsReadOnly
$source = $null
$dest = $null
$check = $null
$sha = $null

try {
    Write-Host "taking disk $DiskNumber offline"
    Set-Disk -Number $DiskNumber -IsOffline $true
    try { Set-Disk -Number $DiskNumber -IsReadOnly $false } catch {}

$sector = 512
$bufferSize = 4MB
$written = 0
$sw = [Diagnostics.Stopwatch]::StartNew()

try {
    $source = [IO.File]::OpenRead($iso.FullName)
    $dest = New-Object IO.FileStream("\\.\PhysicalDrive$DiskNumber",
              [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $buffer = New-Object byte[] $bufferSize

    while ($true) {
        $read = $source.Read($buffer, 0, $bufferSize)
        if ($read -le 0) { break }
        # A raw device only accepts whole sectors; pad the tail.
        $toWrite = $read
        if ($toWrite % $sector -ne 0) {
            $toWrite = ([math]::Ceiling($read / $sector)) * $sector
            [Array]::Clear($buffer, $read, $toWrite - $read)
        }
        $dest.Write($buffer, 0, $toWrite)
        $written += $read
        $pct = [math]::Round(($written / $isoBytes) * 100)
        Write-Progress -Activity "Writing $($iso.Name) to disk $DiskNumber" `
            -Status "$([math]::Round($written/1MB)) / $([math]::Round($isoBytes/1MB)) MB" `
            -PercentComplete $pct
    }
    $dest.Flush()
}
finally {
    if ($dest) { $dest.Dispose() }
    if ($source) { $source.Dispose() }
    Write-Progress -Activity "Writing" -Completed
}

$sw.Stop()
Write-Host ("written $([math]::Round($written/1MB,1)) MB in {0:n0}s ({1:n1} MB/s)" -f `
    $sw.Elapsed.TotalSeconds, ($written/1MB/$sw.Elapsed.TotalSeconds)) -ForegroundColor Green

# ------------------------------------------------------------------ verify ---
if (-not $SkipVerify) {
    Write-Host "verifying (reading it back)..."
    $sha = [Security.Cryptography.SHA256]::Create()
    $isoHash = (Get-FileHash -Path $iso.FullName -Algorithm SHA256).Hash

    $check = New-Object IO.FileStream("\\.\PhysicalDrive$DiskNumber",
               [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
    try {
        $buffer = New-Object byte[] $bufferSize
        $remaining = $isoBytes
        while ($remaining -gt 0) {
            $want = [math]::Min($bufferSize, $remaining)
            # Reads must stay sector aligned, so read whole buffers and hash
            # only the bytes the ISO actually occupies.
            $aligned = ([math]::Ceiling($want / $sector)) * $sector
            $got = $check.Read($buffer, 0, $aligned)
            if ($got -le 0) { break }
            $count = [math]::Min($want, $got)
            $sha.TransformBlock($buffer, 0, $count, $null, 0) | Out-Null
            $remaining -= $count
        }
        $sha.TransformFinalBlock((New-Object byte[] 0), 0, 0) | Out-Null
        $diskHash = ($sha.Hash | ForEach-Object { $_.ToString("x2") }) -join ''
    }
    finally { $check.Dispose(); $sha.Dispose() }

    if ($diskHash -eq $isoHash.ToLower()) {
        Write-Host "verified: the stick matches the ISO byte for byte" -ForegroundColor Green
    }
    else {
        Write-Host "VERIFY FAILED - the stick does not match the ISO" -ForegroundColor Red
        Write-Host "  iso  $($isoHash.ToLower())"
        Write-Host "  disk $diskHash"
        throw "Verification failed: disk $DiskNumber does not match $($iso.Name)."
    }
}

Set-Disk -Number $DiskNumber -IsOffline $false -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# ------------------------------------------------- persistence partition -----
# Current images contain a ready-to-use FAT32 TCCONF partition. Detect it on the
# selected disk (never by label globally), give it a drive letter if possible,
# and avoid creating a duplicate. The creation path remains for older/custom
# images and is capped below Windows' FAT32 formatting limit on large sticks.
if (-not $NoPersistence) {
    Write-Host ""
    Write-Host "preparing the TCCONF settings partition"
    try {
        Update-Disk -Number $DiskNumber -ErrorAction SilentlyContinue
        $disk = Get-Disk -Number $DiskNumber
        if ($disk.IsReadOnly) { Set-Disk -Number $DiskNumber -IsReadOnly $false }

        $tcconfPartition = $null
        foreach ($candidate in @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)) {
            try {
                $candidateVolume = Get-Volume -Partition $candidate -ErrorAction Stop
                if ($candidateVolume.FileSystemLabel -eq 'TCCONF') {
                    $tcconfPartition = $candidate
                    break
                }
            }
            catch { continue }
        }

        if ($tcconfPartition) {
            if (-not $tcconfPartition.DriveLetter) {
                Add-PartitionAccessPath -DiskNumber $DiskNumber `
                    -PartitionNumber $tcconfPartition.PartitionNumber `
                    -AssignDriveLetter -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
                $tcconfPartition = Get-Partition -DiskNumber $DiskNumber `
                    -PartitionNumber $tcconfPartition.PartitionNumber
            }
            $letter = $tcconfPartition.DriveLetter
            $location = if ($letter) { "${letter}:" } else { "partition $($tcconfPartition.PartitionNumber)" }
            Write-Host ("  embedded TCCONF ready at {0} ({1} MB)" -f $location,
                        [math]::Round($tcconfPartition.Size/1MB)) -ForegroundColor Green
            if ($letter) {
                Write-Host "  edit ${letter}:\config.json to change what the client connects to"
            }
        }
        else {
        Write-Host "  this older image has no embedded TCCONF; creating one"

        $freeBytes = $disk.LargestFreeExtent
        if ($freeBytes -lt 64MB) {
            throw "only $([math]::Round($freeBytes/1MB)) MB free - too small to be useful"
        }

        # Windows' built-in formatter refuses FAT32 volumes above roughly
        # 32 GB. Configuration needs very little space, so cap the partition
        # instead of making persistence fail on common 64/128 GB sticks.
        $maxFat32Bytes = 31GB
        if ($freeBytes -gt $maxFat32Bytes) {
            $part = New-Partition -DiskNumber $DiskNumber -Size $maxFat32Bytes `
                        -AssignDriveLetter
        }
        else {
            $part = New-Partition -DiskNumber $DiskNumber -UseMaximumSize `
                        -AssignDriveLetter
        }
        Start-Sleep -Seconds 2
        Format-Volume -Partition $part -FileSystem FAT32 -NewFileSystemLabel TCCONF `
            -Confirm:$false -Force | Out-Null
        $letter = (Get-Partition -DiskNumber $DiskNumber -PartitionNumber $part.PartitionNumber).DriveLetter
        Write-Host ("  TCCONF created on {0}: ({1} GB)" -f $letter,
                    [math]::Round($part.Size/1GB, 1)) -ForegroundColor Green

        # Seed it with the image's factory configuration so it can be edited
        # from Windows without booting a client first.
        #
        # This MUST come from out\config.json, which the build exports after
        # substituting the real server address. The copy in overlay\ is the
        # template and still holds placeholder addresses; seeding that would
        # override the correct address in the image with a dead one, and the
        # client would report a transport failure that looks like a network
        # fault rather than a configuration mistake.
        $repo = Split-Path -Parent $PSScriptRoot
        $seed = Join-Path $repo 'out\config.json'
        if (-not (Test-Path $seed)) {
            Write-Host "  out\config.json is missing - rebuild so the correct server" -ForegroundColor Yellow
            Write-Host "  address is exported; not seeding a placeholder config." -ForegroundColor Yellow
            $seed = $null
        }
        if ($letter -and $seed -and (Test-Path $seed)) {
            Copy-Item $seed "${letter}:\config.json" -Force
            New-Item -ItemType Directory -Force -Path "${letter}:\ca-certificates" | Out-Null
            $server = (Get-Content $seed -Raw | ConvertFrom-Json).connections[0].host
            Write-Host "  seeded ${letter}:\config.json  (server: $server)"
            Write-Host "  edit that file here to change what the client connects to"
        }
        }
    }
    catch {
        Write-Host "  could not prepare the persistence partition: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "  The stick still boots. Add it later in Disk Management:" -ForegroundColor Yellow
        Write-Host "    new simple volume in the free space, FAT32, volume label TCCONF"
    }
}
elseif ($NoPersistence) {
    Write-Host "Note: -NoPersistence skips legacy partition creation; an embedded" -ForegroundColor Yellow
    Write-Host "TCCONF partition already present in the ISO remains on the stick." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Boot the target PC from this stick." -ForegroundColor Cyan
Write-Host "Windows may offer to format the boot partition - always say no; that layout"
Write-Host "is Linux, not NTFS. Only the TCCONF volume is meant to be readable here."
}
finally {
    # FileStream construction itself can fail, before the narrower cleanup
    # blocks above are entered. Dispose defensively, then always restore the
    # physical disk state recorded before the destructive workflow began.
    if ($check) { $check.Dispose() }
    if ($sha) { $sha.Dispose() }
    if ($dest) { $dest.Dispose() }
    if ($source) { $source.Dispose() }
    Write-Progress -Activity "Writing" -Completed

    Set-Disk -Number $DiskNumber -IsOffline $false -ErrorAction SilentlyContinue
    Set-Disk -Number $DiskNumber -IsReadOnly $originalReadOnly -ErrorAction SilentlyContinue
    Set-Disk -Number $DiskNumber -IsOffline $originalOffline -ErrorAction SilentlyContinue
}
