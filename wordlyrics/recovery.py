"""Bounded repairs of app-owned caches. Never change security settings, drivers, or music."""
import hashlib
import os
from pathlib import Path
import uuid


def computer_supported(system="Windows",bits=64,version=(10,0,18362)):
    if system!="Windows":return "UNSUPPORTED_SYSTEM: this installer requires Windows 10 or 11."
    if bits!=64:return "UNSUPPORTED_ARCHITECTURE: use 64-bit Windows/Python."
    if tuple(version)<(10,0,18362):return "WINDOWS_UPDATE_REQUIRED: Windows 10 version 1903 or newer is required."
    return ""


def repair_models(folder,known,download,*,say=lambda _:None,stop=None):
    root=Path(folder)
    if root.is_symlink() or (hasattr(root,"is_junction") and root.is_junction()):raise OSError("MODEL_REPAIR_REFUSED: linked model folder")
    repaired=[]
    for model in known:
        name=model["file"]
        if Path(name).name!=name:raise OSError("Unsafe model filename")
        path=root/name
        if path.is_symlink():raise OSError("MODEL_REPAIR_REFUSED: linked model file")
        if stop is not None and stop.is_set():raise InterruptedError("stopped")
        if path.exists():
            digest=hashlib.sha256()
            with path.open("rb") as f:
                for block in iter(lambda:f.read(4*1024*1024),b""):digest.update(block)
            if digest.hexdigest()==model["sha256"]:continue
            saved=root/(name+".damaged-"+uuid.uuid4().hex)
            path.rename(saved)
            say("MODEL_REPAIR: preserved damaged "+name+" and downloading its verified replacement.")
        else:say("MODEL_REPAIR: restoring missing "+name)
        download(model,str(root))
        repaired.append(name)
    return repaired


def permission_error(error):
    current=error
    for _ in range(6):
        if isinstance(current,PermissionError) or getattr(current,"errno",None) in (1,13) or getattr(current,"winerror",None)==5:return True
        current=getattr(current,"__cause__",None) or getattr(current,"__context__",None)
        if current is None:break
    return False
