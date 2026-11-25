"""The downloads of ParlorNet's classic Mac software on the docs site: Eclipse 68k Fix, and Install ParlorNet
Rulebooks with the READMEs of the ParlorNet rulebook and the game rulebooks. Each is built and released in its own
repository (REPOS), not here. docsite/downloads.json pins each file: the repository, the release tag, the file
name and its SHA-256. tools/fetch_downloads.py fetches them from the GitHub Releases into build/downloads/ (the
docs workflow runs it before it builds the site), checking each one's SHA-256, and the site serves them over
plain HTTP at download/<file>, which period browsers can fetch where GitHub's HTTPS addresses are out of reach.

A build of the site without them leaves the downloads out, and each page says so and links its README in the
repository instead. A file whose SHA-256 is not the manifest's is never served. PARLOR_DOWNLOADS names another
folder of fetched files, and PARLOR_DOWNLOADS_MANIFEST another manifest (the tests use both).

Each page lists its files with the size and MD5 of the files served."""
import hashlib
import json
import os

from parlord import PROJECT_URL
from adapters.webstyle import a, data_table, esc, p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "docsite", "downloads.json")
FETCH_SCRIPT = "tools/fetch_downloads.py"
ECLIPSE_REPO = "eric-cgn/parlornet-eclipse-fix"
GAMEMASTER_REPO = "eric-cgn/parlornet-gamemaster"
REPOS = (ECLIPSE_REPO, GAMEMASTER_REPO)
EXTENSIONS = ("hqx", "bin", "dsk")
INSTALLER = "parlornet-rulebooks-installer"        # an application made by EUP: no disk image
# The site's downloads, as the pages offer them: {file name: (repository, path of the file in the repository
# when it is a source file there, for the link of a site built without the downloads)}
FILES = {"eclipse-68k-fix.%s" % e: (ECLIPSE_REPO, None) for e in EXTENSIONS}
FILES["eclipse-68k-fix-readme.txt"] = (ECLIPSE_REPO, "release/eclipse-68k-fix-readme.txt")
FILES.update({"%s.%s" % (INSTALLER, e): (GAMEMASTER_REPO, None) for e in EXTENSIONS[:2]})
FILES["parlornet-rulebook-readme.txt"] = (GAMEMASTER_REPO, "release/parlornet-rulebook-readme.txt")
FILES["gamemaster-rulebooks-readme.txt"] = (GAMEMASTER_REPO, "release/gamemaster-rulebooks-readme.txt")
_cache = {}


class DownloadError(Exception):
    pass


def manifest_path():
    return os.environ.get("PARLOR_DOWNLOADS_MANIFEST") or MANIFEST


def folder():
    return os.environ.get("PARLOR_DOWNLOADS") or os.path.join(ROOT, "build", "downloads")


def entries(path=None):
    """The manifest: [{repo, tag, file, sha256}], one for each file of FILES."""
    with open(path or manifest_path()) as f:
        out = json.load(f)["files"]
    for e in out:
        if set(e) != {"repo", "tag", "file", "sha256"}:
            raise DownloadError("%s: an entry needs repo, tag, file and sha256: %r" % (path or manifest_path(), e))
    return out


def entry(name):
    for e in entries():
        if e["file"] == name:
            return e
    raise DownloadError("%s does not pin %s" % (manifest_path(), name))


def release_url(e):
    return "https://github.com/%s/releases/download/%s/%s" % (e["repo"], e["tag"], e["file"])


def repo_url(repo):
    return "https://github.com/%s" % repo


def describe(e):
    return "%s from the %s release of %s (%s)" % (e["file"], e["tag"], e["repo"], release_url(e))


def get(name):
    """The bytes of a download as fetched, or None when it was not fetched. A file whose SHA-256 is not the
    manifest's raises DownloadError: it is never served."""
    key = (manifest_path(), folder(), name)
    if key not in _cache:
        e = entry(name)
        path = os.path.join(folder(), name)
        data = None
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = f.read()
            got = hashlib.sha256(data).hexdigest()
            if got != e["sha256"]:
                raise DownloadError("%s has SHA-256 %s, not %s as %s pins it for %s; fetch it again with %s"
                                    % (path, got, e["sha256"], manifest_path(), describe(e), FETCH_SCRIPT))
        _cache[key] = data
    return _cache[key]


def extensions(slug):
    return EXTENSIONS[:2] if slug == INSTALLER else EXTENSIONS


def fetched(slug):
    """{extension: bytes} of a program's .hqx, .bin and .dsk as fetched (slug: eclipse-68k-fix, for example), or
    None if any of them is missing."""
    out = {}
    for ext in extensions(slug):
        data = get("%s.%s" % (slug, ext))
        if data is None:
            return None
        out[ext] = data
    return out


def url(name):
    return "/download/" + name


def files(*names):
    """{site path: bytes} of the named downloads that were fetched."""
    out = {}
    for name in names:
        data = get(name)
        if data is not None:
            out[url(name).lstrip("/")] = data
    return out


def program_files(slug):
    """{site path: bytes} of a program's downloads, or {} when they were not fetched."""
    got = fetched(slug) or {}
    return {url("%s.%s" % (slug, ext)).lstrip("/"): data for ext, data in got.items()}


def readme_link(name, text="README.txt"):
    """A link to a README on the site, or to it in its repository when this build of the site has no
    downloads."""
    if get(name) is not None:
        return a(url(name), text)
    repo, path = FILES[name]
    return a("%s/blob/main/%s" % (repo_url(repo), path), text)


def downloads_html(slug, readme):
    """The Download part of a program's page: the table of files with sizes and MD5s, or a note that this
    build of the site has none."""
    repo = FILES["%s.%s" % (slug, EXTENSIONS[0])][0]
    if not fetched(slug):
        return p("The downloads come from the releases of %s, which %s fetches when the docs site is built. "
                 "This copy of the site was built without them; the %s describes them."
                 % (a(repo_url(repo), repo), a(PROJECT_URL + "/blob/main/" + FETCH_SCRIPT, FETCH_SCRIPT),
                    readme_link(readme)))
    return (downloads_table(slug) +
            p("The %s has this page&#39;s text and the license." % readme_link(readme)))


def downloads_table(slug):
    """The table of a program's files with sizes and MD5s ("" when they were not fetched)."""
    got = fetched(slug)
    if not got:
        return ""
    rows = []
    for ext in extensions(slug):
        data = got[ext]
        u = url("%s.%s" % (slug, ext))
        rows.append([a(u, "<b>%s</b>" % esc(u.rsplit("/", 1)[1])), kb(len(data)),
                     "<tt>%s</tt>" % hashlib.md5(data).hexdigest()])
    return data_table(["File", "Size", "MD5"], rows, None, (None, "right"))


def kb(n):
    """A file's size as the Finder shows it: whole K, rounded up."""
    return "{:,} KB".format(-(-n // 1024))


def missing():
    """The manifest's files that were not fetched, each described (for tools/build_docs.py to say so)."""
    return [describe(e) for e in entries() if get(e["file"]) is None]
