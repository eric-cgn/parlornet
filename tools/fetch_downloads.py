"""Fetch the docs site's downloads from the GitHub Releases that docsite/downloads.json pins, and check each one's
SHA-256, into build/downloads/ (git-ignored), where tools/build_docs.py finds them. The docs workflow runs it
before it builds the site.

    python3 tools/fetch_downloads.py              # into build/downloads (or PARLOR_DOWNLOADS)
    python3 tools/fetch_downloads.py --out DIR

A file already there with the pinned SHA-256 is kept. A file that cannot be fetched (most often a release that has
not been published yet) or whose SHA-256 differs is named with its repository, tag and address, nothing is written
for it, and the tool exits with status 1.
"""
import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from docsite import downloads  # noqa: E402

TIMEOUT = 60


def fetch(entries, out, opener=urllib.request.urlopen):
    """Fetch each entry into out. Returns the list of problems (empty when every file is there and checked)."""
    os.makedirs(out, exist_ok=True)
    problems = []
    for e in entries:
        path = os.path.join(out, e["file"])
        if os.path.isfile(path):
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == e["sha256"]:
                    continue
        url = downloads.release_url(e)
        try:
            with opener(url, timeout=TIMEOUT) as r:
                data = r.read()
        except urllib.error.HTTPError as err:
            err.close()
            if err.code == 404:
                problems.append("%s: not found (HTTP 404). The release %s of %s is not published, or it has no "
                                "file %s: publish that release (push the tag %s to github.com/%s and let its "
                                "release workflow finish) before the docs site is built"
                                % (url, e["tag"], e["repo"], e["file"], e["tag"], e["repo"]))
            else:
                problems.append("%s: HTTP %d fetching %s" % (url, err.code, downloads.describe(e)))
            continue
        except (urllib.error.URLError, OSError) as err:
            problems.append("%s: %s fetching %s" % (url, err, downloads.describe(e)))
            continue
        got = hashlib.sha256(data).hexdigest()
        if got != e["sha256"]:
            problems.append("%s: SHA-256 %s, but docsite/downloads.json pins %s for %s; not written"
                            % (url, got, e["sha256"], downloads.describe(e)))
            continue
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fetch_downloads", description="Fetch the docs site's pinned downloads")
    ap.add_argument("--out", default=downloads.folder(), help="the folder to write (default: build/downloads)")
    ap.add_argument("--manifest", default=downloads.manifest_path(), help="the manifest (docsite/downloads.json)")
    a = ap.parse_args(argv)
    entries = downloads.entries(a.manifest)
    problems = fetch(entries, a.out)
    for p in problems:
        print("fetch_downloads: " + p, file=sys.stderr)
    if problems:
        print("fetch_downloads: %d of %d files missing; the docs site is not built without them"
              % (len(problems), len(entries)), file=sys.stderr)
        return 1
    print("fetch_downloads: %d files in %s, each with its pinned SHA-256" % (len(entries), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
