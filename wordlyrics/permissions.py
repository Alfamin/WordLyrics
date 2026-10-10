"""Targeted, explicit permission repair. Never elevate the lyric model or change system ACLs."""
import base64
import json
import os
from pathlib import Path
import subprocess
import uuid

from . import backup,library as L


def plan(paths,source,blocked=None):
    blocked=blocked or [p for p in (os.environ.get("SystemRoot"),os.environ.get("ProgramFiles"),os.environ.get("ProgramFiles(x86)"),os.environ.get("PROGRAMDATA")) if p]
    targets=[]
    profile=Path(os.environ.get("USERPROFILE",str(Path.home())))
    profile_container=os.path.normcase(os.path.abspath(profile.parent))
    for value in dict.fromkeys(paths):
        path=os.path.abspath(value)
        if os.path.normcase(path)==profile_container or os.path.normcase(os.path.dirname(path))==profile_container:
            raise ValueError("PERMISSION_REPAIR_REFUSED: whole user profiles are protected. Choose the specific music folder, not the Users directory or an entire profile.")
        if os.name=="nt":
            import ctypes
            drive=os.path.splitdrive(path)[0]+os.sep
            if ctypes.windll.kernel32.GetDriveTypeW(drive)==4:raise ValueError("REMOTE_PERMISSION_REQUIRED: mapped network drives need their server/share owner's permissions; local UAC cannot fix them.")
        if path.startswith('\\\\') or os.path.dirname(path)==path or not backup.inside(path,source) or any(backup.inside(path,p) for p in blocked):
            raise ValueError("PERMISSION_REPAIR_REFUSED: choose a local music file/folder outside Windows/system folders; network shares need their owner's permissions.")
        current=Path(path)
        while current!=current.parent:
            if current.is_symlink() or L._is_junction(str(current)):raise ValueError("PERMISSION_REPAIR_REFUSED: linked paths are protected.")
            current=current.parent
        try:os.lstat(path)
        except PermissionError:pass  # the elevated helper can check the denied target
        except FileNotFoundError:raise ValueError("PERMISSION_TARGET_MISSING: reconnect the drive or restore the folder first.")
        targets.append(path)
    return targets


def repair(home,source,paths,launch=None,detailed=False):
    def answer(ok,message):return {"ok":ok,"message":message} if detailed else message
    targets=plan(paths,source)
    if os.name!="nt":return answer(False,"Permission repair requires Windows. Ask the folder owner to grant access.")
    system=Path(os.environ.get("SystemRoot","C:\\Windows"))/"System32"
    shell=str(system/"WindowsPowerShell"/"v1.0"/"powershell.exe")
    sid=subprocess.check_output([str(system/"whoami.exe"),"/user","/fo","csv","/nh"],text=True,encoding="utf-8",errors="replace").strip().split(',')[-1].strip('" ')
    import re
    if not re.fullmatch(r"(?:S-1-5-21-(?:\d+-){2}\d+-\d+|S-1-12-1(?:-\d+){4})",sid):raise ValueError("CURRENT_USER_UNKNOWN: unable to identify the requesting Windows user.")
    area=Path(home)/"permission-repairs"/uuid.uuid4().hex;area.mkdir(parents=True)
    request=area/"request.json";result=area/"result.json"
    data=json.dumps({"sid":sid,"source":source,"paths":targets,"result":str(result),"profile_container":str(Path(os.environ.get("USERPROFILE",str(Path.home()))).parent)})
    request.write_text(data,encoding="utf-8")
    script=Path(__file__).with_name("repair-permissions.ps1").read_text(encoding="utf-8")
    import hashlib
    digest=hashlib.sha256(request.read_bytes()).hexdigest()
    script="$requestPath='"+str(request).replace("'","''")+"'; $expectedRequestHash='"+digest+"'\n"+script
    encoded=base64.b64encode(script.encode("utf-16le")).decode("ascii")
    if launch is None:
        # RunAs is used only for this fixed ACL helper. Its command contains no user-supplied code.
        command="$p=Start-Process -FilePath ([IO.Path]::Combine($env:SystemRoot,'System32\\WindowsPowerShell\\v1.0\\powershell.exe')) -Verb RunAs -ArgumentList @('-NoProfile','-EncodedCommand','"+encoded+"') -PassThru -Wait -WindowStyle Hidden; exit $p.ExitCode"
        process=subprocess.run([shell,"-NoProfile","-Command",command],capture_output=True,text=True,errors="replace")
        if process.returncode:return answer(False,"Administrator approval was canceled or the helper could not run. The job is saved; nothing was restarted.")
    else:launch(request,result)
    try:
        data=json.loads(result.read_text(encoding="utf-8-sig"));return answer(bool(data.get("ok")),data["message"])
    except (OSError,ValueError,KeyError):return answer(False,"Permission repair did not return a result. The job is saved; ask the folder owner for access.")
