"""Build ParlorNet Docs, the docs site (http://docs.parlornet.org/), into a folder: docsite/build.py says what is in it.

    python3 tools/build_docs.py                 # into build/docs-site (git-ignored)
    python3 tools/build_docs.py --out DIR       # an empty folder, or one this tool wrote before

To look at it, open DIR/index.html in a browser, or serve the folder (python3 -m http.server -d DIR).
GitHub Actions builds and publishes it on every push to main (.github/workflows/docs.yml).

The downloads of Eclipse 68k Fix and Install ParlorNet Rulebooks come from build/downloads/, where
tools/fetch_downloads.py puts the release files docsite/downloads.json pins, as the workflow does before it builds
the site. Without them the site is built without the downloads, and their pages say so; with
--require-downloads (as the workflow builds it) the build fails instead, naming each missing file and its release.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from docsite import build, downloads  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_docs", description="Build the ParlorNet docs site")
    ap.add_argument("--out", default=build.OUT, help="the folder to write (default: build/docs-site)")
    ap.add_argument("--require-downloads", action="store_true",
                    help="fail if any download pinned by docsite/downloads.json was not fetched")
    a = ap.parse_args(argv)
    try:
        missing = downloads.missing()
    except downloads.DownloadError as e:
        print("docs site: not built: %s" % e, file=sys.stderr)
        return 1
    if missing and a.require_downloads:
        for m in missing:
            print("docs site: missing download: %s" % m, file=sys.stderr)
        print("docs site: not built: %d downloads are missing from %s; tools/fetch_downloads.py fetches them"
              % (len(missing), downloads.folder()), file=sys.stderr)
        return 1
    site = build.build(a.out)
    pages = sum(1 for n in site if n.endswith(".html"))
    print("docs site: %d pages, %d files, %d bytes in %s" % (pages, len(site), sum(map(len, site.values())), a.out))
    if missing:
        print("docs site: built without %d downloads, not fetched into %s (tools/fetch_downloads.py fetches "
              "them); their pages say so" % (len(missing), downloads.folder()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
