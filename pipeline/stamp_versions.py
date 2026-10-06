"""stamp_versions.py — rewrites the ?v=... cache-busting suffixes in docs/ to content hashes, so browsers refetch exactly the files that changed."""
import hashlib
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT_DIR, "docs")


def file_hash(name):
    path = os.path.join(DOCS_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:8]


# Rewrites `name?v=...` for each named file inside one docs/ file; returns True if it changed.
def restamp(target, names):
    path = os.path.join(DOCS_DIR, target)
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = text
    for name in names:
        digest = file_hash(name)
        if digest:
            new = re.sub(r"(?<![\w.-])" + re.escape(name) + r"\?v=[A-Za-z0-9]+", f"{name}?v={digest}", new)
    if new == text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return True


# Data files are stamped into the scripts first, because the scripts must be hashed after they embed those versions.
def stamp():
    changed = [js for js in ("app.js", "heatmaps.js") if restamp(js, ["data.json", "us-states.json"])]
    changed += [page for page in ("index.html", "heatmaps.html")
                if restamp(page, ["app.js", "style.css", "heatmaps.js", "heatmaps.css"])]
    print("Cache versions updated in: " + ", ".join(changed) if changed else "Cache versions already current.")


if __name__ == "__main__":
    stamp()
