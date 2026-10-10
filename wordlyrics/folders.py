"""Read Noctis library folders without opening its database or changing its settings."""
import json
import os
from pathlib import Path

from . import library as L


def usable(path):
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isdir(path):
        return path, "Folder is missing, disconnected, or inaccessible."
    if os.path.dirname(path) == path:
        return path, "Choose a music folder rather than an entire drive."
    if os.path.islink(path) or L._is_junction(path):
        return path, "Linked folders are protected; choose the original folder."
    return path, ""


def unique(paths):
    """Remove duplicate/nested roots so an all-folders run never processes a song twice."""
    result = []
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if any(key == os.path.normcase(p) or key.startswith(os.path.normcase(p) + os.sep) for p in result):
            continue
        result = [p for p in result if not os.path.normcase(p).startswith(key + os.sep)]
        result.append(os.path.abspath(path))
    return result


def discover(settings=None):
    path = settings or os.environ.get("WORDLYRICS_NOCTIS_SETTINGS")
    if not path:
        base = os.environ.get("NOCTIS_DATA_DIR") or os.path.join(os.environ.get("APPDATA", ""), "Noctis")
        path = os.path.join(base, "settings.json")
    problems = []
    try:
        with open(path, "rb") as f:
            raw = f.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("settings file is too large")
        data = json.loads(raw.decode("utf-8-sig"))
        values = data.get("musicFolders", [])
        if not isinstance(values, list):
            raise ValueError("musicFolders is not a list")
    except FileNotFoundError:
        return [], ["No Noctis library settings found. You can choose a music folder here."]
    except (OSError, ValueError, AttributeError, UnicodeError) as e:
        return [], ["Cannot read Noctis library settings (%s). Choose a folder here, or check Noctis Settings > Library." % type(e).__name__]
    roots = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        root, error = usable(value)
        if error:
            problems.append("%s: %s" % (root, error))
        else:
            roots.append(root)
    return unique(roots), problems
