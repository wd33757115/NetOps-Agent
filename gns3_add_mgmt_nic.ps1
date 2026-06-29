$ErrorActionPreference = 'Continue'
$log = Join-Path $env:TEMP 'gns3-add-nic.log'
Remove-Item $log -ErrorAction SilentlyContinue

try {
    Add-VMNetworkAdapter -VMName 'GNS3 VM' -SwitchName 'GNS3-MGMT' -Name 'GNS3-MGMT' -ErrorAction Stop
    'ADD OK' | Out-File $log -Append
} catch {
    ('ADD ERR: ' + $_.Exception.Message) | Out-File $log -Append
}

try {
    Set-VMNetworkAdapter -VMName 'GNS3 VM' -Name 'GNS3-MGMT' -MacAddressSpoofing On -ErrorAction Stop
    'SPOOF MGMT OK' | Out-File $log -Append
} catch {
    ('SPOOF MGMT ERR: ' + $_.Exception.Message) | Out-File $log -Append
}

try {
    Set-VMNetworkAdapter -VMName 'GNS3 VM' -Name '网络适配器' -MacAddressSpoofing On -ErrorAction Stop
    'SPOOF PRIMARY OK' | Out-File $log -Append
} catch {
    ('SPOOF PRIMARY ERR: ' + $_.Exception.Message) | Out-File $log -Append
}

Get-VMNetworkAdapter -VMName 'GNS3 VM' |
    Format-Table Name,SwitchName,MacAddressSpoofing,MacAddress,Status |
    Out-String |
    Out-File $log -Append
