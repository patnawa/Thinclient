<#
    Runs elevated (launched via UAC). Does two things:

      1. Grants this user standing Hyper-V access, so no further UAC prompts
         are needed once they sign out and back in.
      2. Repairs and starts the ThinClient VM.

    Everything is transcripted to out\hyperv\elevated.log so the calling
    session can read what happened.
#>
$ErrorActionPreference = 'Continue'

$repo = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repo 'out\hyperv'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$log = Join-Path $out 'elevated.log'

Start-Transcript -Path $log -Force | Out-Null

try {
    Write-Host "=== granting standing Hyper-V access ==="
    # The caller's identity, not this elevated process's.
    $target = "$env:USERDOMAIN\$env:USERNAME"
    try {
        $existing = Get-LocalGroupMember -Group 'Hyper-V Administrators' -ErrorAction Stop |
                    Where-Object { $_.Name -eq $target }
        if ($existing) {
            Write-Host "  $target is already a member"
        }
        else {
            Add-LocalGroupMember -Group 'Hyper-V Administrators' -Member $target -ErrorAction Stop
            Write-Host "  added $target to Hyper-V Administrators"
            Write-Host "  (takes effect after a sign out and back in)"
        }
    }
    catch {
        Write-Host "  could not update the group: $($_.Exception.Message)"
    }

    Write-Host ""
    Write-Host "=== repairing and starting the VM ==="
    $repair = Join-Path $PSScriptRoot 'Repair-HyperVBoot.ps1'
    & $repair -VMName 'thinclient' -SwitchName 'LAN-External'
}
catch {
    Write-Host "FAILED: $($_.Exception.Message)"
    Write-Host $_.ScriptStackTrace
}
finally {
    Stop-Transcript | Out-Null
}
