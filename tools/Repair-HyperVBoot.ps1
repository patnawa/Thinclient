<#
.SYNOPSIS
    Make an existing Hyper-V VM boot the ThinClient ISO.

.DESCRIPTION
    A Generation 2 VM that "falls through to PXE" has usually rejected the
    bootloader rather than failed to find it. Hyper-V's default Secure Boot
    template is MicrosoftWindows, which trusts only Microsoft's Windows CA;
    Debian's shim - which this image uses so that Secure Boot can stay on - is
    signed by the third-party "Microsoft UEFI Certificate Authority". Different
    key, so the firmware refuses it and moves to the next boot device.

    This script:
      * attaches the ISO if no DVD drive has it
      * switches Gen 2 VMs to the UEFI CA template (or turns Secure Boot off)
      * makes the DVD the first boot device
      * optionally moves the adapter to an external switch, so the client lands
        on the same LAN as the RDP server
      * starts the VM and screenshots the console

    Run ELEVATED.

.EXAMPLE
    .\Repair-HyperVBoot.ps1 -VMName thinclient
    .\Repair-HyperVBoot.ps1 -VMName thinclient -SwitchName "LAN-External"
    .\Repair-HyperVBoot.ps1 -VMName thinclient -DisableSecureBoot
#>
[CmdletBinding()]
param(
    [string]$VMName = 'thinclient',
    [string]$IsoPath,
    [string]$SwitchName,
    [switch]$DisableSecureBoot
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated PowerShell."
}

$repo = Split-Path -Parent $PSScriptRoot
if (-not $IsoPath) {
    $IsoPath = (Get-ChildItem (Join-Path $repo 'out') -Filter *.iso -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $IsoPath -or -not (Test-Path $IsoPath)) { throw "No ISO found. Pass -IsoPath." }

$vm = Get-VM -Name $VMName
Write-Host "VM         : $VMName (generation $($vm.Generation), $($vm.State))"
Write-Host "ISO        : $IsoPath"

if ($vm.State -ne 'Off') {
    Write-Host "stopping the VM"
    Stop-VM -Name $VMName -TurnOff -Force
}

# ------------------------------------------------------------------ media ---
$dvd = Get-VMDvdDrive -VMName $VMName | Select-Object -First 1
if (-not $dvd) {
    Write-Host "adding a DVD drive"
    Add-VMDvdDrive -VMName $VMName -Path $IsoPath
    $dvd = Get-VMDvdDrive -VMName $VMName | Select-Object -First 1
}
elseif ($dvd.Path -ne $IsoPath) {
    Write-Host "attaching the ISO (was: $(if ($dvd.Path) { $dvd.Path } else { 'empty' }))"
    Set-VMDvdDrive -VMName $VMName -ControllerNumber $dvd.ControllerNumber `
        -ControllerLocation $dvd.ControllerLocation -Path $IsoPath
    $dvd = Get-VMDvdDrive -VMName $VMName | Select-Object -First 1
}
else { Write-Host "ISO already attached" }

# --------------------------------------------------------------- firmware ---
if ($vm.Generation -eq 2) {
    $fw = Get-VMFirmware -VMName $VMName
    Write-Host "Secure Boot: $($fw.SecureBoot) / $($fw.SecureBootTemplate)"

    if ($DisableSecureBoot) {
        Set-VMFirmware -VMName $VMName -EnableSecureBoot Off
        Write-Host "  Secure Boot turned OFF" -ForegroundColor Yellow
    }
    else {
        # The template that trusts the third-party CA which signs Debian's shim.
        Set-VMFirmware -VMName $VMName -EnableSecureBoot On `
            -SecureBootTemplate MicrosoftUEFICertificateAuthority
        Write-Host "  Secure Boot template set to MicrosoftUEFICertificateAuthority" -ForegroundColor Green
    }

    Set-VMFirmware -VMName $VMName -FirstBootDevice $dvd
    Write-Host "  DVD set as the first boot device" -ForegroundColor Green
}
else {
    # Generation 1 boots BIOS; the ISO's isolinux handles that path.
    Set-VMBios -VMName $VMName -StartupOrder @('CD', 'IDE', 'LegacyNetworkAdapter', 'Floppy')
    Write-Host "boot order set to CD first" -ForegroundColor Green
}

# ---------------------------------------------------------------- network ---
if ($SwitchName) {
    Connect-VMNetworkAdapter -VMName $VMName -SwitchName $SwitchName
    Write-Host "adapter connected to '$SwitchName'" -ForegroundColor Green
}
else {
    $adapter = Get-VMNetworkAdapter -VMName $VMName | Select-Object -First 1
    $switch = Get-VMSwitch -Name $adapter.SwitchName -ErrorAction SilentlyContinue
    if ($switch -and $switch.SwitchType -ne 'External') {
        Write-Host "note: adapter is on '$($switch.Name)' ($($switch.SwitchType))." -ForegroundColor Yellow
        Write-Host "      For the client to sit on the same LAN as your RDP server, re-run with:" -ForegroundColor Yellow
        Get-VMSwitch -SwitchType External | ForEach-Object {
            Write-Host "        -SwitchName '$($_.Name)'" -ForegroundColor Yellow }
    }
}

# -------------------------------------------------------------------- ACL ---
# Each VM runs under its own identity, NT VIRTUAL MACHINE\<vm-guid>, and needs
# an explicit read ACE on any file it opens. Attaching an ISO through Hyper-V
# Manager adds that automatically - but the ACE belongs to the file, so
# rebuilding the image produces a new file without it and the VM then fails to
# start with "Access is denied". Grant it on the file, and mark the containing
# folder inheritable so the next rebuild is already covered.
$vmId = (Get-VM -Name $VMName).VMId.Guid
$principal = "NT VIRTUAL MACHINE\$vmId"
Write-Host ""
Write-Host "granting $principal read access to the ISO"
& icacls $IsoPath /grant "${principal}:(R)" /Q 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  icacls on the file failed (exit $LASTEXITCODE)" -ForegroundColor Yellow
}
$isoDir = Split-Path -Parent $IsoPath
& icacls $isoDir /grant "${principal}:(OI)(CI)(R)" /Q 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  folder marked inheritable, so rebuilt ISOs keep working" -ForegroundColor Green
}

# ------------------------------------------------------------------ start ---
Write-Host ""
Write-Host "starting the VM"
Start-VM -Name $VMName
Start-Sleep -Seconds 45

$out = Join-Path $repo 'out\hyperv'
New-Item -ItemType Directory -Force -Path $out | Out-Null
try {
    Add-Type -AssemblyName System.Drawing
    $wmiVm = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem `
                -Filter "ElementName='$VMName'"
    $service = Get-CimInstance -Namespace root\virtualization\v2 `
                -ClassName Msvm_VirtualSystemManagementService
    $video = Get-CimAssociatedInstance -InputObject $wmiVm -ResultClassName Msvm_VideoHead |
             Select-Object -First 1
    $w = if ($video.CurrentHorizontalResolution) { [int]$video.CurrentHorizontalResolution } else { 1024 }
    $h = if ($video.CurrentVerticalResolution) { [int]$video.CurrentVerticalResolution } else { 768 }
    $result = Invoke-CimMethod -InputObject $service -MethodName GetVirtualSystemThumbnailImage `
                -Arguments @{ TargetSystem = $wmiVm; WidthPixels = $w; HeightPixels = $h }
    if ($result.ImageData) {
        $bmp = New-Object System.Drawing.Bitmap($w, $h)
        for ($y = 0; $y -lt $h; $y++) {
            for ($x = 0; $x -lt $w; $x++) {
                $i = ($y * $w + $x) * 2
                if ($i + 1 -ge $result.ImageData.Length) { break }
                $px = [int]$result.ImageData[$i] -bor ([int]$result.ImageData[$i + 1] -shl 8)
                $bmp.SetPixel($x, $y, [System.Drawing.Color]::FromArgb(
                    (($px -shr 11) -band 0x1F) * 255 / 31,
                    (($px -shr 5) -band 0x3F) * 255 / 63,
                    ($px -band 0x1F) * 255 / 31))
            }
        }
        $shot = Join-Path $out "$VMName-console.png"
        $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        Write-Host "console screenshot: $shot" -ForegroundColor Cyan
    }
}
catch { Write-Host "screenshot failed: $($_.Exception.Message)" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Open the console to watch it:  vmconnect.exe localhost $VMName" -ForegroundColor Cyan
