<#
    Screenshot a running Hyper-V VM console. Does not stop, start or reconfigure
    anything - the VM is left exactly as it is, so whatever is on screen at the
    moment of capture is preserved.
#>
param([string]$VMName = 'thinclient')

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repo 'out\hyperv'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$log = Join-Path $out 'screenshot.log'
Start-Transcript -Path $log -Force | Out-Null

try {
    Add-Type -AssemblyName System.Drawing
    $vm = Get-VM -Name $VMName
    Write-Host "VM state: $($vm.State), uptime $($vm.Uptime)"

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
        $shot = Join-Path $out "$VMName-now.png"
        $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        Write-Host "saved $shot (${w}x${h})"
    }
    else { Write-Host "no image data" }
}
catch { Write-Host "FAILED: $($_.Exception.Message)" }
finally { Stop-Transcript | Out-Null }
