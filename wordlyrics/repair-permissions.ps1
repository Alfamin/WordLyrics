# Fixed helper run only after the user sees the targets and accepts Windows UAC.
# $requestPath is supplied as a quoted data value; all filesystem operations use LiteralPath.
$ErrorActionPreference = 'Stop'
$result = @{ ok=$false; message='Permission repair was not completed. The job is saved.' }
try {
    $file = Get-Item -LiteralPath $requestPath
    if ($file.Length -gt 524288 -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid permission request' }
    $requestBytes = [IO.File]::ReadAllBytes($requestPath)
    $requestHash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($requestBytes)).Replace('-','')
    if ($expectedRequestHash -and $requestHash -ine $expectedRequestHash) { throw 'Permission request changed after approval was requested' }
    $request = [Text.Encoding]::UTF8.GetString($requestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
    $sid = New-Object Security.Principal.SecurityIdentifier ([string]$request.sid)
    if ($sid.Value -notmatch '^(S-1-5-21-(\d+-){2}\d+-\d+|S-1-12-1(-\d+){4})$') { throw 'Invalid requesting user' }
    $source = [IO.Path]::GetFullPath([string]$request.source).TrimEnd('\')
    $blocked = @($env:SystemRoot,$env:ProgramFiles,${env:ProgramFiles(x86)},$env:PROGRAMDATA) | Where-Object { $_ }
    foreach ($given in $request.paths) {
        $path = [IO.Path]::GetFullPath([string]$given)
        $profiles = [string]$request.profile_container
        if ($profiles -and ($path -ieq $profiles -or [IO.Path]::GetDirectoryName($path) -ieq $profiles)) { throw 'Whole user profiles are protected; choose a specific music folder' }
        if ($path.StartsWith('\\') -or $path.TrimEnd('\') -eq [IO.Path]::GetPathRoot($path).TrimEnd('\') -or
            -not ($path -ieq $source -or $path.StartsWith($source+'\',[StringComparison]::OrdinalIgnoreCase))) { throw 'Protected or out-of-scope target' }
        foreach ($systemPath in $blocked) {
            if ($path -ieq $systemPath -or $path.StartsWith($systemPath.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'System paths are protected' }
        }
        $current = $path
        while ($current -and $current -ne [IO.Path]::GetPathRoot($current)) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked paths are protected' }
            $current = [IO.Path]::GetDirectoryName($current)
        }
        $acl = Get-Acl -LiteralPath $path
        if (@($acl.Access | Where-Object { $_.AccessControlType -eq 'Deny' }).Count) { throw 'An explicit deny rule is present. Ask the folder owner; deny rules are not removed automatically.' }
        # Preserve ownership and other users' rules. Grant Modify only to the requesting user,
        # not to the administrator account that may have approved UAC.
        $target = Get-Item -LiteralPath $path
        $inherit = if ($target.PSIsContainer) { [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
        $rights = if (-not $target.PSIsContainer -and $target.Extension -match '^\.(mp3|flac|m4a|mp4|ogg|oga|opus|wav|aiff|aif|aifc|wma|aac|ape|wv|alac|asf|dsf|dff)$') { [Security.AccessControl.FileSystemRights]::ReadAndExecute } else { [Security.AccessControl.FileSystemRights]::Modify }
        $rule = New-Object Security.AccessControl.FileSystemAccessRule ($sid,$rights,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $path -AclObject $acl
        if (-not $target.PSIsContainer -and $target.Extension -ieq '.elrc' -and ($target.Attributes -band [IO.FileAttributes]::ReadOnly)) {
            $target.Attributes = $target.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
        }
    }
    $result.ok=$true
    $result.message='Permission rule added for the original Windows user. Choose Retry affected songs; completed songs stay complete.'
} catch { $result.message='PERMISSION_REPAIR_FAILED: '+$_.Exception.Message+'. The job is saved; ask the folder owner for access.' }
if ($request -and [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath([string]$request.result)) -eq [IO.Path]::GetDirectoryName($requestPath)) {
    $parent = Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($requestPath)) -Force
    if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { exit 1 }
    if (Test-Path -LiteralPath ([string]$request.result)) { exit 1 }
    $result | ConvertTo-Json | Set-Content -LiteralPath ([string]$request.result) -Encoding UTF8
}
