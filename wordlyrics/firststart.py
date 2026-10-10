"""First start, part two. WordLyrics.bat has unpacked a portable Python into the ".python" folder and runs this
file with it. This turns that Python into WordLyrics' private environment:

  1. pip (the package installer) is put in,
  2. the packages from requirements.txt are downloaded and installed, each one checked against its fingerprint,
  3. everything is tried once.

Each download has backup servers (see links.py). Nothing is written outside the WordLyrics folder. Running it
again continues where it stopped. Uses nothing but what Python itself brings.
"""
import glob
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
from wordlyrics import links                                    # noqa: E402  (only standard library inside)

PY = sys.executable
PYDIR = os.path.dirname(os.path.abspath(PY))
CACHE = os.path.join(PYDIR, "download-cache")
REQ = os.path.join(APP, "requirements.txt")
NEEDS = ("numpy", "av", "onnxruntime", "mutagen", "unidecode")


def say(text=""):
    print(" " + text if text else "", flush=True)


def write_pth():
    """Tell the portable Python where its packages and the WordLyrics code are (it reads nothing else,
    so nothing on this computer can get mixed in)."""
    found = glob.glob(os.path.join(PYDIR, "python*._pth"))
    if not found:
        raise SystemExit(" The portable Python in %s is not complete. Delete that folder and start again." % PYDIR)
    zips = [os.path.basename(z) for z in glob.glob(os.path.join(PYDIR, "python*.zip"))]
    with open(found[0], "w", encoding="ascii", newline="\r\n") as fh:
        fh.write("\n".join(zips + [".", "..", "Lib\\site-packages", "import site"]) + "\n")
    os.makedirs(os.path.join(PYDIR, "Lib", "site-packages"), exist_ok=True)


def works(code):
    return subprocess.run([PY, "-c", code], capture_output=True, text=True, errors="replace")


def fetch(urls, dest):
    last = ""
    for u in urls:
        say("   from %s ..." % links.host(u))
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "WordLyrics-setup"})
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            if len(data) < 100000 or b"pip" not in data[:4000]:
                raise ValueError("that is not the expected file")
            with open(dest + ".part", "wb") as fh:
                fh.write(data)
            os.replace(dest + ".part", dest)
            return True
        except Exception as e:
            from wordlyrics.network import explain
            code,message=explain(e)
            last = "[%s] %s" % (code,message)
            say("   did not work (%s)" % last)
    return False


def with_each_index(make_cmd, what):
    """Run a pip command against the normal package index, then against each backup."""
    idx = links.indexes()
    for i, u in enumerate(idx):
        say("   from %s%s ..." % (links.host(u), "" if i == 0 else "  (backup server)"))
        cmd = make_cmd(u) + ["--index-url", u, "--retries", "2", "--timeout", "30", "--cache-dir", CACHE,
                             "--disable-pip-version-check", "--no-warn-script-location"]
        if u.lower().startswith("http://"):
            cmd += ["--trusted-host", links.host(u)]
        if subprocess.run(cmd).returncode == 0:
            return True
        say("   that did not work%s" % ("" if i + 1 == len(idx) else ", trying the next server"))
    say()
    say("%s could not be downloaded from any of the %d servers." % (what, len(idx)))
    say("Check the internet connection and start WordLyrics again: it continues where it stopped.")
    say("Another server can be added in extra-links.txt (next to WordLyrics.bat).")
    return False


def versions_present():
    """Exact pinned distributions can be checked locally, without contacting any index."""
    expected={}
    with open(REQ,encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#"):continue
            pin=line.split()[0]
            name,version=pin.split("==",1)
            expected[name]=version
    code="import importlib.metadata as m\nexpected="+repr(expected)+"\ntry:\n ok=all(m.version(k)==v for k,v in expected.items())\nexcept m.PackageNotFoundError:\n ok=False\nraise SystemExit(0 if ok else 1)"
    return bool(expected) and works(code).returncode==0


def main():
    env_version = sys.argv[1] if len(sys.argv) > 1 else "1"
    t0 = time.time()
    if os.path.realpath(PYDIR)!=os.path.realpath(os.path.join(APP,".python")):
        say("ENVIRONMENT_REPAIR_REFUSED: setup changes only WordLyrics' private Python.")
        return 1
    write_pth()
    damaged=works("import "+", ".join(NEEDS)).returncode!=0
    force=["--force-reinstall"] if os.environ.get("WORDLYRICS_REPAIR_PACKAGES")=="1" or damaged else []
    reuse=not force and versions_present()

    say("Step 2 of 3: the package installer (pip, about 2 MB)")
    if not reuse and works("import pip").returncode != 0:
        getpip = os.path.join(PYDIR, "get-pip.py")
        if not os.path.exists(getpip) and not fetch(links.GET_PIP, getpip):
            say()
            say("The installer could not be downloaded. Check the internet connection and start WordLyrics again.")
            return 1
        if not with_each_index(lambda u: [PY, getpip, "--quiet"], "The package installer"):
            return 1
    say("   done")
    say()

    say("Step 3 of 3: the packages that read audio and run the models (about 80 MB to download)")
    if reuse:say("   Exact installed package versions verified locally; package downloads skipped.")
    elif not with_each_index(lambda u: [PY, "-m", "pip", "install", *force, "--require-hashes", "--only-binary", ":all:", "-r", REQ],
                           "The packages"):
        return 1
    r = works("import %s; import onnxruntime as o; print(o.__version__, 'DmlExecutionProvider' in o.get_available_providers())"
              % ", ".join(NEEDS))
    if r.returncode != 0:
        say()
        say("The packages were installed but do not start on this computer:")
        say("SETUP_IMPORT_FAILED: Python exit code %s. Check supported Windows/architecture, security-software quarantine, and available memory." % r.returncode)
        for ln in (r.stderr or r.stdout).strip().splitlines()[-3:]:
            say("   " + ln)
        say("WordLyrics needs 64-bit Windows 10 (version 1903 or newer) or Windows 11.")
        return 1
    shutil.rmtree(CACHE, ignore_errors=True)                     # the downloaded package files are not needed again
    for f in ("get-pip.py",):
        try:
            os.remove(os.path.join(PYDIR, f))
        except OSError:
            pass
    with open(os.path.join(PYDIR, "ready-%s.txt" % env_version), "w", encoding="utf-8") as fh:
        fh.write("WordLyrics environment %s, set up %s, onnxruntime %s\n" % (env_version, time.strftime("%Y-%m-%d %H:%M"), r.stdout.strip()))
    say("   done")
    say()
    say("Setup finished in %d min %02d s. This was needed only once." % divmod(int(time.time() - t0), 60))
    say()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n Stopped. Start WordLyrics again to continue the setup.")
        sys.exit(1)
