"""Before importing the model backend, repair a damaged private environment once."""
import importlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import json
import uuid

from .recovery import computer_supported

MODULES=("numpy","av","onnxruntime","mutagen","unidecode")


def probe(python):
    code="import importlib,json\nbroken=[]\nfor name in "+repr(MODULES)+":\n try: importlib.import_module(name)\n except Exception: broken.append(name)\nprint(json.dumps(broken))"
    result=subprocess.run([python,"-X","utf8","-c",code],capture_output=True,text=True,errors="replace")
    try:return json.loads(result.stdout) if result.returncode==0 else list(MODULES)
    except ValueError:return list(MODULES)


def ensure(home,*,importer=None,repair=None,python=None):
    python=python or sys.executable
    broken=probe(python) if importer is None else [module for module in MODULES if not _works(module,importer)]
    if not broken:return True
    private=Path(home)/".python"
    if private.is_symlink() or (hasattr(private,"is_junction") and private.is_junction()):
        print("ENVIRONMENT_REPAIR_REFUSED: linked Python environments are protected. Set up WordLyrics in your own app folder.")
        return False
    if Path(python).parent.resolve()!= (Path(home)/".python").resolve():
        print("ENVIRONMENT_DAMAGED: "+", ".join(broken)+" could not load. Automatic repair only changes WordLyrics' private Python; run its installer/setup.")
        return False
    print("ENVIRONMENT_REPAIRING: repairing private packages once: "+", ".join(broken),flush=True)
    for marker in (Path(home)/".python").glob("ready-*.txt"):
        if not marker.is_symlink():marker.rename(marker.with_name("repair-needed-"+uuid.uuid4().hex+".txt"))
    if repair is None:
        env=dict(os.environ,WORDLYRICS_REPAIR_PACKAGES="1")
        code=subprocess.call([python,str(Path(home)/"wordlyrics"/"firststart.py"),"1"],env=env)
    else:code=repair()
    if code:return False
    return not probe(python) if importer is None else all(_works(module,importer) for module in MODULES)


def _works(module,importer):
    try:importer(module);return True
    except Exception:return False


def main(argv=None):
    home=os.environ.get("WORDLYRICS_HOME") or str(Path(__file__).resolve().parent.parent)
    if os.name=="nt":
        version=sys.getwindowsversion()
        problem=computer_supported(bits=struct.calcsize("P")*8,version=(version.major,version.minor,version.build))
        if problem:print(problem);return 1
    if not ensure(home):
        print("ENVIRONMENT_REPAIR_FAILED: completed work is kept. Open Set up WordLyrics, then resume the affected songs.")
        save_failed_startup(home,argv,"ENVIRONMENT_REPAIR_FAILED: private dependencies could not load or be repaired.")
        return 70
    from .__main__ import main as run
    return run(argv)


def save_failed_startup(home,argv,error):
    """A broken model package must not prevent a resumable request from being recorded."""
    from . import __main__ as cli,resume
    from datetime import datetime
    from types import SimpleNamespace
    import hashlib
    args=list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("menu","launch","resume","check","setup","diagnose","providers","source","undo"):return
    try:
        options=cli.parser().parse_args(args)
        if len(options.folder)!=1:return
        source=os.path.abspath(options.folder[0])
        options.speed=options.speed or "full"
        paths=Path(options.songs_from).read_text(encoding="utf-8-sig").splitlines() if options.songs_from else None
        tag=hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
        folder=Path(home)/"runs"/("startup-"+tag)/("run "+datetime.now().strftime("%Y-%m-%d %H.%M.%S.%f"))
        folder.mkdir(parents=True)
        request=SimpleNamespace(source=source,opts=options,only_songs=paths,songs=[],run_dir=str(folder),not_usable=[],problems=[error],check=None)
        resume.save(request)
        cli.write_result(str(folder/"outcome.json"),request,70,error)
        if options.result:cli.write_result(options.result,request,70,error)
    except (OSError,ValueError):print("STARTUP_JOB_SAVE_FAILED: fix the WordLyrics app folder before trying again.")


if __name__=="__main__":sys.exit(main())
