<#
.SYNOPSIS
    Boot the ThinClient ISO in Hyper-V and report what happened.

.DESCRIPTION
    Creates a throwaway VM, boots the ISO, waits for the connection manager,
    captures a screenshot of the VM console, and tears the VM down again.

    Generation 2 is the real-world case: UEFI firmware with Secure Boot on,
    using the Microsoft UEFI CA that Hyper-V offers as a template. That is the
    exact path a modern office PC takes, and it exercises our signed shim.

    MUST BE RUN ELEVATED (Hyper-V management requires administrator rights, or
    membership of the local "Hyper-V Administrators" group).

.PARAMETER Generation
    2 = UEFI (default), 1 = legacy BIOS.

.PARAMETER SecureBoot
    Only meaningful for generation 2. On by default; -SecureBoot:$false disables it.

.PARAMETER Memory
    Startup memory. The client copies its root filesystem into RAM, so give it
    at least 2 GB.

.PARAMETER KeepVM
    Leave the VM in place afterwards instead of deleting it.

.EXAMPLE
    .\Test-HyperV.ps1
    .\Test-HyperV.ps1 -Generation 1
    .\Test-HyperV.ps1 -SecureBoot:$false -KeepVM
#>
[CmdletBinding()]
param(
    [ValidateSet(1, 2)][int]$Generation = 2,
    [bool]$SecureBoot = $true,
    [int64]$Memory = 3GB,
    [switch]$KeepVM,
    [string]$IsoPath,
    [int]$WaitSeconds = 90
)

$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------- preflight ----
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This script must be run from an elevated PowerShell (Run as Administrator)."
}

$repo = Split-Path -Parent $PSScriptRoot
if (-not $IsoPath) {
    $IsoPath = (Get-ChildItem (Join-Path $repo 'out') -Filter *.iso |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $IsoPath -or -not (Test-Path $IsoPath)) {
    throw "No ISO found. Build one first, or pass -IsoPath."
}

$vmName = "ThinClient-Test-Gen$Generation"
$workDir = Join-Path $repo 'out\hyperv'
$shotDir = Join-Path $workDir $vmName
New-Item -ItemType Directory -Force -Path $shotDir | Out-Null

Write-Host "ISO        : $IsoPath"
Write-Host "VM         : $vmName (generation $Generation)"
Write-Host "Memory     : $([math]::Round($Memory/1GB,1)) GB"
if ($Generation -eq 2) { Write-Host "Secure Boot: $SecureBoot" }
Write-Host ""

# ------------------------------------------------------------- old VM -------
$existing = Get-VM -Name $vmName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "removing the previous $vmName"
    if ($existing.State -ne 'Off') { Stop-VM -Name $vmName -TurnOff -Force }
    Remove-VM -Name $vmName -Force
}

# ------------------------------------------------------------- switch -------
# Prefer an external switch so the client gets a real DHCP lease; fall back to
# Default Switch (NAT), which every Hyper-V install has.
$switch = Get-VMSwitch -SwitchType External -ErrorAction SilentlyContinue |
          Select-Object -First 1
if (-not $switch) {
    $switch = Get-VMSwitch -Name 'Default Switch' -ErrorAction SilentlyContinue
}
if ($switch) { Write-Host "network    : $($switch.Name) ($($switch.SwitchType))" }
else { Write-Warning "no usable virtual switch - the VM will boot without a network" }

# ------------------------------------------------------------- create -------
$params = @{
    Name               = $vmName
    MemoryStartupBytes = $Memory
    Generation         = $Generation
    NoVHD              = $true
}
if ($switch) { $params.SwitchName = $switch.Name }
New-VM @params | Out-Null

Set-VM -Name $vmName -ProcessorCount 2 -AutomaticCheckpointsEnabled $false `
       -CheckpointType Disabled
Set-VMMemory -VMName $vmName -DynamicMemoryEnabled $false
Add-VMDvdDrive -VMName $vmName -Path $IsoPath

if ($Generation -eq 2) {
    $dvd = Get-VMDvdDrive -VMName $vmName
    Set-VMFirmware -VMName $vmName -FirstBootDevice $dvd
    if ($SecureBoot) {
        # MicrosoftUEFICertificateAuthority is the template that trusts the
        # third-party CA which signs Debian's shim.
        Set-VMFirmware -VMName $vmName -EnableSecureBoot On `
            -SecureBootTemplate MicrosoftUEFICertificateAuthority
    }
    else {
        Set-VMFirmware -VMName $vmName -EnableSecureBoot Off
    }
}

# ------------------------------------------------------------- boot ---------
Write-Host ""
Write-Host "starting the VM..."
Start-VM -Name $vmName

$result = [ordered]@{
    VM = $vmName; Generation = $Generation
    SecureBoot = ($Generation -eq 2 -and $SecureBoot)
    ReachedManager = $false; Screenshot = $null; Heartbeat = $null; IPAddresses = @()
}

# Take a screenshot of the VM console through WMI.
function Save-VMScreenshot {
    param([string]$Name, [string]$Path, [int]$Width = 1280, [int]$Height = 800)
    try {
        $vm = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem `
                -Filter "ElementName='$Name'"
        $svc = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_VirtualSystemManagementService
        $video = Get-CimAssociatedInstance -InputObject $vm -ResultClassName Msvm_VideoHead |
                 Select-Object -First 1
        if ($video -and $video.CurrentHorizontalResolution) {
            $Width = [int]$video.CurrentHorizontalResolution
            $Height = [int]$video.CurrentVerticalResolution
        }
        $out = Invoke-CimMethod -InputObject $svc -MethodName GetVirtualSystemThumbnailImage `
                 -Arguments @{ TargetSystem = $vm; WidthPixels = $Width; HeightPixels = $Height }
        if (-not $out.ImageData) { return $null }

        # The thumbnail comes back as raw RGB565.
        $bmp = New-Object System.Drawing.Bitmap($Width, $Height)
        $data = $out.ImageData
        for ($y = 0; $y -lt $Height; $y++) {
            for ($x = 0; $x -lt $Width; $x++) {
                $i = ($y * $Width + $x) * 2
                if ($i + 1 -ge $data.Length) { break }
                $pixel = [int]$data[$i] -bor ([int]$data[$i + 1] -shl 8)
                $r = (($pixel -shr 11) -band 0x1F) * 255 / 31
                $g = (($pixel -shr 5) -band 0x3F) * 255 / 63
                $b = ($pixel -band 0x1F) * 255 / 31
                $bmp.SetPixel($x, $y, [System.Drawing.Color]::FromArgb($r, $g, $b))
            }
        }
        $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        return $Path
    }
    catch { Write-Warning "screenshot failed: $($_.Exception.Message)"; return $null }
}

Add-Type -AssemblyName System.Drawing

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$shotIndex = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15
    $shotIndex++
    $path = Join-Path $shotDir ("t{0:d3}.png" -f ($shotIndex * 15))
    Save-VMScreenshot -Name $vmName -Path $path | Out-Null
    if (Test-Path $path) { Write-Host "  captured $(Split-Path -Leaf $path)" }

    $vm = Get-VM -Name $vmName
    if ($vm.State -ne 'Running') { Write-Warning "VM stopped unexpectedly"; break }
}

$result.Screenshot = (Get-ChildItem $shotDir -Filter *.png |
                      Sort-Object Name -Descending | Select-Object -First 1).FullName

# Integration services are not installed in the image, so heartbeat/IP will be
# absent. Report them anyway - their absence is expected, not a failure.
$vm = Get-VM -Name $vmName
$result.Heartbeat = ($vm | Get-VMIntegrationService -Name Heartbeat -ErrorAction SilentlyContinue).PrimaryStatusDescription
try { $result.IPAddresses = (Get-VMNetworkAdapter -VMName $vmName).IPAddresses } catch {}

Write-Host ""
Write-Host "=== result ===" -ForegroundColor Cyan
$result.GetEnumerator() | ForEach-Object { "  {0,-15} {1}" -f $_.Key, ($_.Value -join ', ') }
Write-Host ""
Write-Host "Open the last screenshot to confirm the connection manager is on screen:"
Write-Host "  $($result.Screenshot)"

if (-not $KeepVM) {
    Write-Host ""
    Write-Host "cleaning up"
    Stop-VM -Name $vmName -TurnOff -Force -ErrorAction SilentlyContinue
    Remove-VM -Name $vmName -Force -ErrorAction SilentlyContinue
}
else {
    Write-Host ""
    Write-Host "VM kept. Connect with:  vmconnect.exe localhost $vmName"
}
