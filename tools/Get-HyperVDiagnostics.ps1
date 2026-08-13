<#
.SYNOPSIS
    Collect everything needed to diagnose a ThinClient VM on Hyper-V.

.DESCRIPTION
    Dumps the VM's generation, firmware and Secure Boot template, which virtual
    switch it is attached to and what kind of switch that is, plus a screenshot
    of its console. Writes a transcript to out\hyperv\ so it can be shared.

    Run this ELEVATED, with the VM running.

.EXAMPLE
    .\Get-HyperVDiagnostics.ps1 -VMName thinclient
#>
[CmdletBinding()]
param(
    [string]$VMName = 'thinclient'
)

$ErrorActionPreference = 'Continue'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated PowerShell."
}

$repo = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repo 'out\hyperv'
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "=== VM ===" -ForegroundColor Cyan
$vm = Get-VM -Name $VMName -ErrorAction Stop
$vm | Select-Object Name, State, Generation, ProcessorCount,
    @{n='MemoryGB';e={[math]::Round($_.MemoryStartup/1GB,2)}}, Uptime |
    Format-List | Out-String | Write-Host

Write-Host "=== Firmware ===" -ForegroundColor Cyan
if ($vm.Generation -eq 2) {
    $fw = Get-VMFirmware -VMName $VMName
    $fw | Select-Object SecureBoot, SecureBootTemplate | Format-List | Out-String | Write-Host
    if ($fw.SecureBoot -eq 'On' -and $fw.SecureBootTemplate -ne 'MicrosoftUEFICertificateAuthority') {
        Write-Host "  PROBLEM: Secure Boot is on with the '$($fw.SecureBootTemplate)' template." -ForegroundColor Red
        Write-Host "  That template does not trust the third-party UEFI CA that signs" -ForegroundColor Red
        Write-Host "  Debian's shim, so this image cannot boot. Fix with:" -ForegroundColor Red
        Write-Host "    Set-VMFirmware -VMName $VMName -SecureBootTemplate MicrosoftUEFICertificateAuthority" -ForegroundColor Yellow
        Write-Host "  (or -EnableSecureBoot Off)" -ForegroundColor Yellow
    }
    Write-Host "  Boot order:"
    $fw.BootOrder | ForEach-Object { "    $($_.BootType)  $($_.Device)" } | Write-Host
}
else { Write-Host "  Generation 1 (legacy BIOS)`n" }

Write-Host "=== Network ===" -ForegroundColor Cyan
foreach ($adapter in Get-VMNetworkAdapter -VMName $VMName) {
    $switch = Get-VMSwitch -Name $adapter.SwitchName -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Adapter     = $adapter.Name
        Switch      = $adapter.SwitchName
        SwitchType  = if ($switch) { $switch.SwitchType } else { 'NOT CONNECTED' }
        MacAddress  = $adapter.MacAddress
        Status      = ($adapter.Status -join ',')
        IPAddresses = ($adapter.IPAddresses -join ', ')
    } | Format-List | Out-String | Write-Host

    if (-not $switch) {
        Write-Host "  PROBLEM: the adapter is not attached to a switch." -ForegroundColor Red
    }
    elseif ($switch.SwitchType -ne 'External') {
        Write-Host "  NOTE: '$($switch.Name)' is $($switch.SwitchType), not External." -ForegroundColor Yellow
        Write-Host "  An External switch puts the client on your real LAN alongside" -ForegroundColor Yellow
        Write-Host "  the RDP server, which is what you want. Available External switches:" -ForegroundColor Yellow
        Get-VMSwitch -SwitchType External | ForEach-Object { "    $($_.Name)" } | Write-Host
        Write-Host "  Attach with:" -ForegroundColor Yellow
        Write-Host "    Connect-VMNetworkAdapter -VMName $VMName -SwitchName '<name>'" -ForegroundColor Yellow
    }
}

Write-Host "=== Media ===" -ForegroundColor Cyan
Get-VMDvdDrive -VMName $VMName | Select-Object ControllerNumber, DvdMediaType, Path |
    Format-List | Out-String | Write-Host
Get-VMHardDiskDrive -VMName $VMName | Select-Object ControllerType, Path |
    Format-List | Out-String | Write-Host

# ------------------------------------------------------------- screenshot ---
Write-Host "=== Console screenshot ===" -ForegroundColor Cyan
try {
    Add-Type -AssemblyName System.Drawing
    $wmiVm = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem `
                -Filter "ElementName='$VMName'"
    $service = Get-CimInstance -Namespace root\virtualization\v2 `
                -ClassName Msvm_VirtualSystemManagementService
    $video = Get-CimAssociatedInstance -InputObject $wmiVm -ResultClassName Msvm_VideoHead |
             Select-Object -First 1
    $width = if ($video.CurrentHorizontalResolution) { [int]$video.CurrentHorizontalResolution } else { 1024 }
    $height = if ($video.CurrentVerticalResolution) { [int]$video.CurrentVerticalResolution } else { 768 }
    Write-Host "  console resolution: ${width}x${height}"

    $result = Invoke-CimMethod -InputObject $service -MethodName GetVirtualSystemThumbnailImage `
                -Arguments @{ TargetSystem = $wmiVm; WidthPixels = $width; HeightPixels = $height }
    if ($result.ImageData) {
        $bmp = New-Object System.Drawing.Bitmap($width, $height)
        $data = $result.ImageData
        for ($y = 0; $y -lt $height; $y++) {
            for ($x = 0; $x -lt $width; $x++) {
                $i = ($y * $width + $x) * 2
                if ($i + 1 -ge $data.Length) { break }
                $px = [int]$data[$i] -bor ([int]$data[$i + 1] -shl 8)
                $bmp.SetPixel($x, $y, [System.Drawing.Color]::FromArgb(
                    (($px -shr 11) -band 0x1F) * 255 / 31,
                    (($px -shr 5) -band 0x3F) * 255 / 63,
                    ($px -band 0x1F) * 255 / 31))
            }
        }
        $shot = Join-Path $out "$VMName-console.png"
        $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        Write-Host "  saved: $shot" -ForegroundColor Green
    }
    else { Write-Host "  no image data (is the VM running?)" -ForegroundColor Yellow }
}
catch { Write-Host "  screenshot failed: $($_.Exception.Message)" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Done. Share the output above and out\hyperv\$VMName-console.png" -ForegroundColor Cyan
