"""Connectivity/setup checks suitable for sharing: no credentials, phone numbers, or lyrics."""
import os
from pathlib import Path
import platform
import json

from . import __version__, folders, models, network
from .sources import settings


def check(home):
    print("\nWORDLYRICS CONNECTION & SETUP CHECK - " + __version__)
    roots, problems = folders.discover()
    print("Noctis folders: %d" % len(roots))
    for problem in problems:
        print("  " + problem)
    need = models.missing(os.path.join(home, "models"))
    print("Models: " + ("downloaded (size check; Advanced > Check installation tests loading)" if not need else "%d still need downloading (choose Setup in Advanced)" % len(need)))
    config = settings(os.path.join(home, "lyrics-providers.json"))
    if config.get("configuration_error"):
        print("PROVIDER_CONFIG_INVALID: lyrics-providers.json has invalid syntax or values.")
    print("Testing lyric providers (this can take up to about 8 seconds)...", flush=True)
    rows = network.check(config.get("genius", True) is not False)
    for row in rows:
        print("  %s [%s]: %s" % (row["provider"], row["code"], row["message"]))
    if config.get("genius", True) is False:
        print("  Genius [DISABLED]: switched off in lyrics-providers.json.")
    print("This checks access, not whether a specific song's lyrics exist.")
    data={"wordlyrics":__version__,"system":platform.system(),"architecture":platform.machine(),
          "noctis_folders_detected":len(roots),"models_missing":[m["file"] for m in need],"providers":rows,
          "provider_config_invalid":bool(config.get("configuration_error"))}
    target=Path(home)/"diagnostics.json"
    try:
        if target.is_symlink() or target.with_suffix(".tmp").is_symlink():raise OSError("linked diagnostic file")
        target.parent.mkdir(parents=True,exist_ok=True)
        target.with_suffix(".tmp").write_text(json.dumps(data,indent=2),encoding="utf-8")
        os.replace(target.with_suffix(".tmp"),target)
        print("Shareable diagnosis (no credentials, lyrics or music paths): "+str(target))
    except OSError:print("DIAGNOSTICS_SAVE_FAILED: copy the messages above instead.")
    return 0 if all(row["ok"] for row in rows) and not config.get("configuration_error") else 4
