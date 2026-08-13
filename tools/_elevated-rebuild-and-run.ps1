<#
    One elevated pass: stop the VM, rebuild the image, restart the VM on it.

    The VM has to be stopped for the rebuild, because Windows will not let the
    ISO be replaced while a running machine has it mounted - the build fails
    with "permission denied" on a file the user plainly owns.
#>
param([string]$VMName = 'thinclient')

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repo 'out\hyperv'
New-Item -ItemType Directory -Force -Path $out | Out-Null
Start-Transcript -Path (Join-Path $out 'rebuild.log') -Force | Out-Null

try {
    $vm = Get-VM -Name $VMName -ErrorAction Stop
    if ($vm.State -ne 'Off') {
        Write-Host "=== stopping $VMName so the ISO can be replaced ==="
        Stop-VM -Name $VMName -TurnOff -Force
        Start-Sleep -Seconds 3
    }
    else { Write-Host "$VMName is already off" }

    Write-Host ""
    Write-Host "=== rebuilding the image ==="
    $iso = Get-ChildItem (Join-Path $repo 'out') -Filter *.iso -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $before = if ($iso) { $iso.LastWriteTime } else { [datetime]::MinValue }

    # Convert the path inside bash. Passing a Windows path through PowerShell
    # to `wsl wslpath` and back returned an empty string, and `cd ""` succeeds
    # in the home directory - so the build silently ran nowhere.
    $quoted = "'" + $repo + "'"
    $cmd = 'cd "$(wslpath -a ' + $quoted + ')" && bash build/build.sh 2>&1 | tail -8'
    wsl -d Debian -u root -- bash -lc $cmd
    if ($LASTEXITCODE -ne 0) { throw "the build failed (exit $LASTEXITCODE)" }

    # Prove it actually produced something, rather than trusting an exit code
    # from the end of a pipeline.
    $after = (Get-ChildItem (Join-Path $repo 'out') -Filter *.iso |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    if ($after -le $before) { throw "the ISO was not rebuilt (still $after)" }
    Write-Host "  ISO rebuilt: $after"

    Write-Host ""
    Write-Host "=== restarting the VM on the new image ==="
    & (Join-Path $PSScriptRoot 'Repair-HyperVBoot.ps1') -VMName $VMName -SwitchName 'LAN-External'
}
catch {
    Write-Host "FAILED: $($_.Exception.Message)"
}
finally { Stop-Transcript | Out-Null }
