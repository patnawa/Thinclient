<#
.SYNOPSIS
    Configure Windows Server DHCP to PXE-boot ThinClient machines.

.DESCRIPTION
    Creates two DHCP policies on a scope so BIOS and UEFI clients each get the
    right boot file, sets next-server (option 66) and boot file (option 67), and
    optionally publishes the ThinClient configuration URL as option 224.

    Run this on the Windows Server 2025 box that holds the DHCP role, elevated.

    You still need something serving the files. Windows Server has no built-in
    TFTP service, so either:
      * install the WDS role and use its TFTP server with -TftpRoot, or
      * run dnsmasq/tftpd on any Linux host (see dnsmasq-thinclient.conf), or
      * use a standalone TFTP daemon such as Tftpd64.
    HTTP can be plain IIS pointed at the out\pxe folder.

.PARAMETER ScopeId
    The DHCP scope to configure, e.g. 192.168.1.0

.PARAMETER BootServer
    IP address of the TFTP server holding the PXE tree.

.PARAMETER ConfigUrl
    Optional. HTTP URL of the central ThinClient config.json, published as
    DHCP option 224.

.PARAMETER SecureBoot
    Use the signed shim chain for UEFI clients so Secure Boot can stay enabled.

.EXAMPLE
    .\Set-WindowsDhcpPxe.ps1 -ScopeId 192.168.1.0 -BootServer 192.168.1.5 `
        -ConfigUrl http://192.168.1.5/thinclient/config.json
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$ScopeId,
    [Parameter(Mandatory = $true)][string]$BootServer,
    [string]$ConfigUrl,
    [switch]$SecureBoot
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Module -ListAvailable -Name DhcpServer)) {
    throw "The DhcpServer PowerShell module is not available. Run this on the DHCP server."
}
Import-Module DhcpServer

$uefiBootFile = if ($SecureBoot) { 'bootx64.efi' } else { 'grub/x86_64-efi/core.efi' }
$biosBootFile = 'pxelinux.0'

Write-Host "Scope        : $ScopeId"
Write-Host "Boot server  : $BootServer"
Write-Host "BIOS file    : $biosBootFile"
Write-Host "UEFI file    : $uefiBootFile"
if ($ConfigUrl) { Write-Host "Config URL   : $ConfigUrl (option 224)" }
Write-Host ""

# --- option 66 / 67 at scope level: the fallback for anything unmatched ------
if ($PSCmdlet.ShouldProcess($ScopeId, "Set option 66/67")) {
    Set-DhcpServerv4OptionValue -ScopeId $ScopeId -OptionId 66 -Value $BootServer
    Set-DhcpServerv4OptionValue -ScopeId $ScopeId -OptionId 67 -Value $biosBootFile
    Write-Host "  scope options set (BIOS default)" -ForegroundColor Green
}

# --- policies so UEFI clients get a loader their firmware can actually run ---
# The PXE client advertises its architecture in vendor class option 60:
#   PXEClient:Arch:00000  BIOS
#   PXEClient:Arch:00007  UEFI x64
#   PXEClient:Arch:00009  UEFI x64 (newer firmware)
$policies = @(
    @{ Name = 'ThinClient-UEFI-x64';  Arch = @('PXEClient:Arch:00007', 'PXEClient:Arch:00009'); File = $uefiBootFile },
    @{ Name = 'ThinClient-BIOS';      Arch = @('PXEClient:Arch:00000');                          File = $biosBootFile }
)

foreach ($policy in $policies) {
    $name = $policy.Name
    $existing = Get-DhcpServerv4Policy -ScopeId $ScopeId -Name $name -ErrorAction SilentlyContinue

    if ($PSCmdlet.ShouldProcess($name, "Create/update DHCP policy")) {
        if ($existing) {
            Set-DhcpServerv4Policy -ScopeId $ScopeId -Name $name `
                -VendorClass EQ $policy.Arch -Enabled $true
            Write-Host "  updated policy $name" -ForegroundColor Green
        }
        else {
            Add-DhcpServerv4Policy -ScopeId $ScopeId -Name $name `
                -Condition OR -VendorClass EQ $policy.Arch -Enabled $true
            Write-Host "  created policy $name" -ForegroundColor Green
        }
        Set-DhcpServerv4OptionValue -ScopeId $ScopeId -PolicyName $name -OptionId 66 -Value $BootServer
        Set-DhcpServerv4OptionValue -ScopeId $ScopeId -PolicyName $name -OptionId 67 -Value $policy.File
    }
}

# --- option 224: where the clients pull their configuration from -------------
if ($ConfigUrl) {
    $option = Get-DhcpServerv4OptionDefinition -OptionId 224 -ErrorAction SilentlyContinue
    if (-not $option) {
        if ($PSCmdlet.ShouldProcess('224', "Define DHCP option")) {
            Add-DhcpServerv4OptionDefinition -OptionId 224 -Name 'ThinClientConfigUrl' `
                -Type String -Description 'ThinClient central configuration URL'
            Write-Host "  defined option 224" -ForegroundColor Green
        }
    }
    if ($PSCmdlet.ShouldProcess($ScopeId, "Set option 224")) {
        Set-DhcpServerv4OptionValue -ScopeId $ScopeId -OptionId 224 -Value $ConfigUrl
        Write-Host "  option 224 set" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Done. Verify with:" -ForegroundColor Cyan
Write-Host "  Get-DhcpServerv4OptionValue -ScopeId $ScopeId"
Write-Host "  Get-DhcpServerv4Policy      -ScopeId $ScopeId"
