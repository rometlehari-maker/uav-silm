"""The website's download packages (tools/build_downloads.py)."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import build_downloads as bd  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None or not os.path.isdir(os.path.join(bd.ROOT, ".git")),
                                reason="needs the git repository")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    commit = subprocess.run(["git", "log", "-1", "--format=%H", "HEAD", "--", "onboard"], cwd=bd.ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    a, b = tmp_path_factory.mktemp("a"), tmp_path_factory.mktemp("b")
    return commit, bd.build(str(a), commit), bd.build(str(b), commit), a


def test_same_commit_gives_identical_files(built):
    _, first, second, _ = built
    assert [(f["name"], f["sha256"]) for f in first] == [(f["name"], f["sha256"]) for f in second]


def test_checksums_file_matches_the_files(built):
    _, files, _, out = built
    lines = (out / "SHA256SUMS.txt").read_text().splitlines()
    assert len(lines) == len(files)
    for line in lines:
        digest, name = line.split("  ")
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    meta = json.loads((out / "downloads.json").read_text())
    assert {f["name"] for f in meta["files"]} == {f["name"] for f in files}


def test_software_zip_has_licences_and_no_internal_notes(built):
    commit, files, _, out = built
    sw = next(f for f in files if f["kind"] == "software")
    with zipfile.ZipFile(out / sw["name"]) as z:
        names = z.namelist()
        top = names[0].split("/")[0] + "/"
        for must in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE.txt", "README.md",
                     "groundstation/static/vendor/leaflet/LICENSE",
                     "groundstation/static/fonts/OFL-BricolageGrotesque.txt",
                     "groundstation/static/fonts/OFL-IBMPlexMono.txt",
                     "ardupilot/scripts/silm_gps_loss.lua", "pi/install.sh"):
            assert top + must in names, must
        assert not any(n.endswith("HANDOFF.md") or "__pycache__" in n for n in names)
        assert not any("/training/datasets/" in n or "/training/runs/" in n for n in names)
        assert commit in z.read(top + "SOURCE.txt").decode()
        assert all(i.date_time == bd.FIXED_TIME for i in z.infolist())
        assert (z.getinfo(top + "pi/install.sh").external_attr >> 16) & 0o111     # still executable


def test_model_zip_carries_its_agpl_licence_and_source_pointer(built):
    _, files, _, out = built
    m = next(f for f in files if f["kind"] == "model")
    assert m["licence"] == "AGPL-3.0"
    with zipfile.ZipFile(out / m["name"]) as z:
        names = {n.split("/", 1)[1] for n in z.namelist()}
        assert {"silm_oi.tflite", "silm_oi_labels.txt", "LICENSE-silm_oi-AGPL-3.0.txt", "README.txt"} <= names
        readme = z.read("silm-oi-farm-model-v2/README.txt").decode()
        assert "AGPL-3.0" in readme and "onboard/training" in readme and "Open Images" in readme
        assert "cattle-psyty" in readme and "arthurmessias" in readme and "riis" in readme


def test_single_files_are_the_committed_ones(built):
    commit, files, _, out = built
    assert (out / "silm_gps_loss.lua").read_bytes() == bd.blob(commit, "onboard/ardupilot/scripts/silm_gps_loss.lua")


def test_a_secret_stops_the_build():
    with pytest.raises(SystemExit, match="secret"):
        bd.check_secret("x.txt", b"token = ghp_" + b"A" * 36)
    bd.check_secret("x.txt", b"bash pi/install.sh --password 'my-secret-123'")   # docs example is fine


def test_website_offers_only_the_app(tmp_path):
    """The website gets no files of its own: only the line with the version
    under the app's download buttons (which link to the latest release)."""
    commit = subprocess.run(["git", "log", "-1", "--format=%H", "HEAD", "--", "onboard"], cwd=bd.ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    meta = bd.site_meta(commit)
    assert meta["version"] == bd.blob(commit, "onboard/VERSION").decode().strip()
    page = tmp_path / "index.html"
    page.write_text("x " + bd.SITE_BEGIN + " old cards " + bd.SITE_END + " y")
    assert bd.update_site(str(page), meta)
    html = page.read_text()
    assert f"Version {meta['version']}" in html and commit in html and "releases/latest" in html
    assert "downloads/" not in html and "dl-hash" not in html and "old cards" not in html
    assert not bd.update_site(str(page), meta)                     # second time: nothing to change
    out = tmp_path / "web"
    assert bd.main(["--site", str(page), "--out", str(out)]) == 0 and not out.exists()   # no files written


def test_the_live_page_has_no_download_files():
    html = open(os.path.join(bd.ROOT, "index.html"), encoding="utf-8").read()
    block = html[html.index(bd.SITE_BEGIN):html.index(bd.SITE_END)]
    assert "downloads/" not in block and "Version " in block
    assert "releases/latest/download/UAV-SILM-Ground-Station-Setup.exe" in html


def test_the_repository_holds_no_download_files():
    tracked = subprocess.run(["git", "ls-files", "downloads"], cwd=bd.ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    assert tracked == []
