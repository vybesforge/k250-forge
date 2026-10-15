#!/usr/bin/env python3
"""Build and attach the portable Windows ZIP for a k250-forge release.

Usage:
    tools/publish.py                    # current git tag
    tools/publish.py --tag v3.7.4       # a specific tag
    tools/publish.py --tag v3.7.4 --force   # rebuild+re-upload even if attached

Does two things:
  1. Builds k250-forge-windows-<tag>.zip from `git archive` AT THE TAG (not main),
     plus the one-click launcher files (Start-K250.cmd / .ps1 / quickstart) if they
     exist at that tag. Building from the tag means the zip always matches the
     release exactly -- the old helper cloned main instead, so its zip could carry
     code newer than the tag it was attached to.
  2. Attaches it to the release for that tag (creating the release if missing).

Idempotent: if the asset is already attached it does nothing unless --force. That
way re-running after a manual publish (or a crash) never duplicates the asset.

Token: GH_TOKEN or GITHUB_TOKEN in the environment -- a file, never in chat.
On this box it lives in ~/.hermes/.env (not in this repo).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

REPO = "vybesforge/k250-forge"
LAUNCHER_FILES = ("Start-K250.cmd", "Start-K250.ps1", "README-Windows-quickstart.txt")


def token():
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v
    return None


def _req(method, url, payload=None, data=None, headers=None, timeout=90):
    t = token()
    if not t:
        sys.exit("no GH_TOKEN / GITHUB_TOKEN in the environment -- nothing was sent")
    h = {"Authorization": f"Bearer {t}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "k250-forge-publish"}
    h.update(headers or {})
    body = data
    if payload is not None:
        body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode() or "{}")
        except Exception:                      # noqa: BLE001
            err = {}
        return e.code, err


def get(url):
    return _req("GET", url)


def api(path):
    return f"https://api.github.com{path}"


def safe_tag(tag):
    return re.sub(r"[^A-Za-z0-9._-]", "-", tag)


def git(*args, cwd=None):
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def git_bytes(*args, cwd=None):
    r = subprocess.run(["git", *args], capture_output=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def build_zip(tag, out_dir="dist"):
    """git-archive AT the tag + launcher files -> top-level k250-forge/ -> zip."""
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"k250-forge-windows-{safe_tag(tag)}.zip")
    tmp = tempfile.mkdtemp(prefix="k250zip_")
    try:
        stage = os.path.join(tmp, "k250-forge")
        os.makedirs(stage, exist_ok=True)
        # export the tagged tree exactly; refuse if the tag isn't a valid object.
        arc = subprocess.run(["git", "archive", "--format=zip", "-o",
                              os.path.join(tmp, "tree.zip"), tag],
                             capture_output=True, text=True)
        if arc.returncode != 0:
            raise RuntimeError(f"tag {tag} not archivable: {arc.stderr.strip()[:200]}")
        with zipfile.ZipFile(os.path.join(tmp, "tree.zip")) as z:
            z.extractall(stage)
        # launcher files that are often committed after the earliest tags.
        for f in LAUNCHER_FILES:
            src = os.path.join(os.getcwd(), f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(stage, f))
            else:
                print(f"   [zip] warning: {f} not in this checkout -- package is missing it")
        if os.path.exists(out):
            os.remove(out)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(stage):
                for fn in files:
                    p = os.path.join(root, fn)
                    z.write(p, os.path.relpath(p, os.path.dirname(stage)))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def attach_or_create(tag, zip_path, force=False):
    # find the release for this tag
    code, rel = get(api(f"/repos/{REPO}/releases/tags/{tag}"))
    if code == 200:
        release_id = rel.get("id")
        existing = [a["name"] for a in rel.get("assets") or []]
        name = os.path.basename(zip_path)
        if name in existing and not force:
            print(f"release {tag}: asset {name} already attached -- done (use --force to rebuild)")
            return 0
        if name in existing:  # force: delete then re-upload
            aid = next(a["id"] for a in rel["assets"] if a["name"] == name)
            _req("DELETE", api(f"/repos/{REPO}/releases/assets/{aid}"))
            print(f"   removed old asset {name}")
    else:
        # no release yet for this tag -- create a minimal one. The body is not
        # auto-generated (release notes are written by hand per the repo ritual);
        # a missing file is a signal to write notes, not to fabricate them.
        notes_path = os.path.join(os.getcwd(), f"RELEASE_NOTES_{tag}.md")
        body = open(notes_path).read() if os.path.isfile(notes_path) else \
            "Release notes pending -- see CHANGELOG.md."
        code, rel = _req("POST", api(f"/repos/{REPO}/releases"),
                         {"tag_name": tag, "name": f"k250-forge {tag}", "body": body,
                          "draft": False, "prerelease": False})
        if code not in (200, 201):
            print(f"release create failed HTTP {code}: {rel.get('message')} {rel.get('errors','')}")
            return 1
        release_id = rel.get("id")
        print(f"created release {tag}: {rel.get('html_url')}")

    with open(zip_path, "rb") as f:
        data = f.read()
    url = f"https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets" \
          f"?name={os.path.basename(zip_path)}"
    c2, o2 = _req("POST", url, data=data, headers={"Content-Type": "application/zip"}, timeout=180)
    if c2 in (200, 201):
        print(f"asset uploaded: {o2.get('browser_download_url')}")
        print(f"  {os.path.basename(zip_path)}  {data.__len__():,} bytes")
        return 0
    print(f"asset upload failed HTTP {c2}: {o2.get('message', o2)}")
    return 1


def current_tag(cwd):
    try:
        return git("describe", "--tags", "--exact-match", "HEAD", cwd=cwd).strip()
    except RuntimeError:
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", help="tag to publish (default: current HEAD's tag)")
    p.add_argument("--force", action="store_true", help="rebuild + re-upload even if attached")
    p.add_argument("--out", default="dist", help="temporary build dir (default: dist)")
    a = p.parse_args()

    # run from the repo root so launchers and git object resolution work
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    tag = a.tag or current_tag(root)
    if not tag:
        sys.exit("no --tag given and HEAD has no exact tag -- nothing to publish")

    print(f"building portable zip for {tag} ...")
    z = build_zip(tag, a.out)
    size = os.path.getsize(z)
    print(f"  built {os.path.basename(z)}  {size:,} bytes")

    # sanity: the installers inside the zip must match the tag
    with zipfile.ZipFile(z) as zf:
        for name in ("install.sh", "install.ps1", "install.cmd", "k250_play.py"):
            want = git_bytes("show", f"{tag}:{name}")
            have = zf.read(f"k250-forge/{name}")
            ok = have == want
            print(f"  {'OK  ' if ok else 'MISMATCH'} {name}")
            if not ok:
                sys.exit(f"aborting: {name} in zip differs from {tag} -- release not shipped")

    sys.exit(attach_or_create(tag, z, force=a.force))


if __name__ == "__main__":
    main()
