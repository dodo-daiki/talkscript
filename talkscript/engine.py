"""VOICEVOX ENGINE の検出・導入・起動。

導入は、VOICEVOX の公式の配布ファイル（GitHub Releases、CPU 版）を、版と sha256 を固定してダウンロードし、
PC ごとのデータの場所（workspace.user_data_dir()/VOICEVOX）に展開する。配布ファイルの中のエンジン（vv-engine/run）を
画面なしで起動して使う（エディタの画面も同じフォルダに入るので、VOICEVOX.exe / VOICEVOX から使ってもよい）。
VOICEVOX の利用規約: https://voicevox.hiroshiba.jp/term/ と、声ごとの規約。導入の前に確認を求める。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

from . import workspace
from .voicevox import VoicevoxClient, VoicevoxError

VOICEVOX_VERSION = "0.26.2"
TERMS_URL = "https://voicevox.hiroshiba.jp/term/"
_BASE = f"https://github.com/VOICEVOX/voicevox/releases/download/{VOICEVOX_VERSION}/"
#: (OS, CPU) → (ファイル名, sha256（GitHub の digest）, 大きさ [byte])。2026-11-13 に GitHub の API で確かめた値
ASSETS = {
    ("win32", "x64"): ("voicevox-windows-cpu-0.26.2.zip",
                       "9d24c91fbe72f91a6a79e13a87102cb7b2139848a0d146a28d33087552538dce", 2127595484),
    ("linux", "x64"): ("voicevox-linux-cpu-x64-0.26.2.tar.gz",
                       "709d0085e8748f8c4e1d9f75c71bc86d3f6e2ebab2f650b1b19dfd000aa1c85c", 2140488275),
    ("linux", "arm64"): ("voicevox-linux-cpu-arm64-0.26.2.tar.gz",
                         "8ff57733b13cd4c9dcceaa168a2c7860091400a3578a1bb10114b8583349def9", 2136376713),
}
FREE_BYTES_NEEDED = 7 * 1024 ** 3        # ダウンロード（約 2 GB）＋展開（推定。確かめていない）に余裕を見た値


class EngineError(Exception):
    pass


def platform_key() -> tuple[str, str]:
    m = platform.machine().lower()
    cpu = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)
    osn = "win32" if sys.platform == "win32" else ("linux" if sys.platform.startswith("linux") else sys.platform)
    return osn, cpu


def run_name() -> str:
    return "run.exe" if sys.platform == "win32" else "run"


def install_dir() -> pathlib.Path:
    return workspace.user_data_dir() / "VOICEVOX"


def candidates(st: dict) -> list[pathlib.Path]:
    """エンジンを探す場所。engine_path は PC ごとの settings.toml の値だけを使う（st に入っていても見ない。
    作業フォルダの talkscript.toml は共有されるので、そこから起動するプログラムを決めない）。"""
    out = []
    p = (workspace.load_user_settings().get("voicevox") or {}).get("engine_path")
    if p:
        out.append(pathlib.Path(p).expanduser())
    out.append(install_dir() / "VOICEVOX" / "vv-engine" / run_name())
    if sys.platform == "win32":
        la = pathlib.Path(os.environ.get("LOCALAPPDATA", ""))
        out.append(la / "Programs" / "VOICEVOX" / "vv-engine" / "run.exe")         # 公式のインストーラーの既定の場所
    return out


def find_engine(st: dict) -> pathlib.Path | None:
    for p in candidates(st):
        if p.is_file():
            return p
    return None


def running_version(url: str) -> str | None:
    try:
        return VoicevoxClient(url, timeout_s=2).version()
    except VoicevoxError:
        return None


def _pidfile() -> pathlib.Path:
    return workspace.user_data_dir() / "engine.pid"


def start_engine(st: dict, *, wait_s: float = 180, log=print) -> str:
    """エンジンが動いていなければ起動し、版を返す。"""
    url = st.get("voicevox", {}).get("url", "http://127.0.0.1:50021")
    v = running_version(url)
    if v:
        return v
    exe = find_engine(st)
    if exe is None:
        raise EngineError("VOICEVOX ENGINE が見つからない。talkscript setup で導入するか、"
                          "settings.toml の [voicevox] engine_path に vv-engine/run の場所を書く")
    host_port = url.split("//", 1)[-1].split("/", 1)[0]
    host, _, port = host_port.partition(":")
    logf = workspace.user_data_dir() / "engine.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    log(f"VOICEVOX ENGINE を起動しています（{exe}）…")
    with open(logf, "ab") as f:
        proc = subprocess.Popen([str(exe), "--host", host or "127.0.0.1", "--port", port or "50021"],
                                cwd=exe.parent, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
    _pidfile().write_text(str(proc.pid))
    t_end = time.monotonic() + wait_s
    while time.monotonic() < t_end:
        if proc.poll() is not None:
            raise EngineError(f"VOICEVOX ENGINE がすぐ終了した（終了コード {proc.returncode}）。記録: {logf}")
        v = running_version(url)
        if v:
            log(f"VOICEVOX ENGINE {v} が起動した")
            return v
        time.sleep(1)
    raise EngineError(f"VOICEVOX ENGINE が {wait_s:.0f} 秒で応答しない。記録: {logf}")


def stop_engine() -> bool:
    p = _pidfile()
    if not p.exists():
        return False
    pid = int(p.read_text())
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(pid, 15)
    except (OSError, ProcessLookupError):
        pass
    p.unlink(missing_ok=True)
    return True


# --- 導入 ---------------------------------------------------------------------------------

def download(url: str, dest: pathlib.Path, sha256: str, size: int | None = None, log=print) -> None:
    """dest.part へ書きながら sha256 を計算し、一致したら dest にする。"""
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    got, last = 0, 0.0
    req = urllib.request.Request(url, headers={"User-Agent": "talkscript-installer"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = size or int(r.headers.get("Content-Length") or 0)
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            h.update(b)
            got += len(b)
            if time.monotonic() - last > 2:
                last = time.monotonic()
                pct = f"{got / total * 100:5.1f}%" if total else ""
                log(f"  ダウンロード {got / 1e6:8.0f} MB {pct}")
    if h.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise EngineError(f"sha256 が一致しない（期待 {sha256}、実際 {h.hexdigest()}）。ダウンロードし直す")
    part.replace(dest)


def extract(archive: pathlib.Path, dest: pathlib.Path, log=print) -> None:
    log(f"  展開しています → {dest}")
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
    if dest.exists():
        shutil.rmtree(dest)
    tmp.replace(dest)


def install_voicevox(*, log=print, asset: tuple | None = None, url: str | None = None) -> pathlib.Path:
    """配布ファイルをダウンロード・照合・展開し、エンジンの場所を返す。asset・url はテスト用の差し替え。"""
    key = platform_key()
    asset = asset or ASSETS.get(key)
    if asset is None:
        raise EngineError(f"この OS・CPU（{key}）用の配布ファイルを知らない。VOICEVOX を手で入れ、engine_path を設定する")
    name, sha, size = asset
    d = install_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(d.parent).free
    if free < FREE_BYTES_NEEDED and size > 10 ** 8:
        raise EngineError(f"空き容量が足りない（{free / 1e9:.1f} GB。{FREE_BYTES_NEEDED / 1e9:.0f} GB ほど要る）: {d.parent}")
    archive = d.parent / name
    if not (archive.exists() and _sha256_file(archive) == sha):
        log(f"VOICEVOX {VOICEVOX_VERSION} をダウンロードします: {url or _BASE + name}")
        download(url or _BASE + name, archive, sha, size, log)
    extract(archive, d, log)
    exe = d / "VOICEVOX" / "vv-engine" / run_name()
    if not exe.is_file():
        raise EngineError(f"展開したがエンジンが見つからない: {exe}")
    if sys.platform != "win32":
        exe.chmod(exe.stat().st_mode | 0o111)
    archive.unlink(missing_ok=True)
    return exe


def _sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
