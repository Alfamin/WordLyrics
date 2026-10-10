# Execute the fixed helper with intercepted Set-Acl. Never elevate or change a real ACL.
$ErrorActionPreference='Stop'
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('wordlyrics-permission-tests-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$global:WordLyricsPermissionFixture=@{ Case=''; Writes=[Collections.Generic.List[object]]::new() }
function Get-Acl {
    param($LiteralPath)
    $acl=Microsoft.PowerShell.Security\Get-Acl -LiteralPath $LiteralPath
    if ($global:WordLyricsPermissionFixture.Case -eq 'denied') {
        $deny=New-Object Security.AccessControl.FileSystemAccessRule ('S-1-5-21-9-8-7-1002','Read','Deny')
        $acl.AddAccessRule($deny)
    }
    return $acl
}
function Set-Acl {
    param($LiteralPath,$AclObject)
    $global:WordLyricsPermissionFixture.Writes.Add(@{ Path=$LiteralPath; Acl=$AclObject })
}
$passed=0
function Assert-Permission($condition,$message){if(-not $condition){throw $message};$script:passed++}
try {
    foreach($case in @('folder','audio-file','denied','out-of-scope','request-changed')) {
        $global:WordLyricsPermissionFixture.Case=$case;$global:WordLyricsPermissionFixture.Writes.Clear()
        $area=Join-Path $testRoot $case;$music=Join-Path $area 'music';New-Item -ItemType Directory -Path $music | Out-Null
        $song=Join-Path $music 'song.mp3';'audio unchanged' | Set-Content -LiteralPath $song -Encoding ascii
        $before=(Get-FileHash -LiteralPath $song).Hash
        $owner=(Microsoft.PowerShell.Security\Get-Acl -LiteralPath $music).Owner
        $target=if($case -eq 'audio-file'){$song}elseif($case -eq 'out-of-scope'){$area}else{$music}
        $requestPath=Join-Path $area 'request.json';$resultPath=Join-Path $area 'result.json'
        $request=@{sid='S-1-5-21-1-2-3-1001';source=$music;paths=@($target);result=$resultPath}
        $request | ConvertTo-Json | Set-Content -LiteralPath $requestPath -Encoding utf8
        $expectedRequestHash=$null
        if($case -eq 'request-changed') {
            $expectedRequestHash=(Get-FileHash -LiteralPath $requestPath -Algorithm SHA256).Hash
            ' ' | Add-Content -LiteralPath $requestPath
        }
        & (Join-Path (Split-Path $PSScriptRoot) 'wordlyrics\repair-permissions.ps1')
        $result=Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
        Assert-Permission ((Get-FileHash -LiteralPath $song).Hash -eq $before) "$case`: audio untouched"
        if($case -in @('folder','audio-file')) {
            Assert-Permission $result.ok "$case`: permission plan succeeds"
            Assert-Permission ($global:WordLyricsPermissionFixture.Writes.Count -eq 1) "$case`: exactly one target"
            $acl=$global:WordLyricsPermissionFixture.Writes[0].Acl
            Assert-Permission ($acl.Owner -eq $owner) "$case`: ownership preserved"
            $rules=@($acl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]) | Where-Object {$_.IdentityReference.Value -eq 'S-1-5-21-1-2-3-1001'})
            Assert-Permission ($rules.Count -eq 1) "$case`: original user granted, not another administrator"
            Assert-Permission (-not ($rules[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::ChangePermissions)) "$case`: no FullControl"
            if($case -eq 'audio-file'){Assert-Permission (-not ($rules[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::WriteData)) 'original audio needs read only'}
        } else {
            Assert-Permission (-not $result.ok) "$case`: refused"
            Assert-Permission ($global:WordLyricsPermissionFixture.Writes.Count -eq 0) "$case`: no ACL writes"
        }
    }
    "PASS: $passed permission-helper assertions; no elevation or actual ACL changes."
} finally {Remove-Variable -Name WordLyricsPermissionFixture -Scope Global}
