"""新しい版の確認と更新。

確認: GitHub Releases の最新の版（https://api.github.com/repos/<REPO>/releases/latest）と、いまの版を比べる。
  画面を開くたびに 1 回確かめる（結果は 1 時間だけ覚えて、開き直しても問い合わせ過ぎないようにする）。
  ネットにつながらないときは何も言わない。PC ごとの設定 settings.toml の [update] check = false で止められる。
更新:
  Windows: 新しい版の talkscript-setup.exe をダウンロードし（sha256 を確かめる）、新しい窓で開く。
           インストーラーは、画面（このプログラム）が終わるのを待ってから入れ直す（使用中のファイルは置き換えられないため）。
           足りないものがあれば、これまでどおり窓で 1 つずつ尋ねる。
  Linux:   新しい版の wheel をダウンロードし（sha256 を確かめる）、uv tool install で入れ直す。開き直すと新しい版になる。
uv tool で入れた talkscript だけを更新できる（開発用の環境は git pull で更新する）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from . import __version__, workspace

REPO = "dodo-daiki/talkscript"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
CACHE_S = 3600
PYTHON = "3.12"
SETUP_EXE = "talkscript-setup.exe"


class UpdateError(Exception):
    pass


def parse_version(s: str) -> tuple:
    m = re.match(r"v?(\d+(?:\.\d+)*)", (s or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def _cache_file() -> pathlib.Path:
    return workspace.user_data_dir() / "update.json"


def fetch_latest(timeout_s: float = 5.0) -> dict:
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"talkscript/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        d = json.loads(r.read())
    assets = {}
    for a in d.get("assets") or []:
        dg = a.get("digest") or ""
        assets[a["name"]] = {"url": a["browser_download_url"], "size": a.get("size"),
                             "sha256": dg[7:] if dg.startswith("sha256:") else None}
    return {"version": d["tag_name"].lstrip("v"), "tag": d["tag_name"], "page": d.get("html_url"),
            "notes": d.get("body") or "", "published": d.get("published_at"), "assets": assets}


def latest(*, force: bool = False, fetch=None) -> dict:
    """最新の版の情報。1 時間以内に確かめていれば、そのときの結果を使う。"""
    cf = _cache_file()
    if not force:
        try:
            c = json.loads(cf.read_text(encoding="utf-8"))
            if time.time() - c["checked_at"] < CACHE_S and c.get("current") == __version__:
                return c["latest"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    info = (fetch or fetch_latest)()
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"checked_at": time.time(), "current": __version__, "latest": info},
                                 ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return info


def installed_by_uv_tool() -> bool:
    """uv tool で入れたものか（その環境には uv-receipt.toml がある）。"""
    return (pathlib.Path(sys.prefix) / "uv-receipt.toml").is_file()


def needed_asset(version: str) -> str:
    return SETUP_EXE if sys.platform == "win32" else f"talkscript-{version}-py3-none-any.whl"


def check(*, force: bool = False, fetch=None, current: str = __version__) -> dict:
    """いまの版と最新の版を比べる。ネットにつながらなければ error だけを返す。"""
    try:
        info = latest(force=force, fetch=fetch)
    except Exception as e:  # noqa: BLE001  確かめられないだけで、画面は普通に使う
        return {"current": current, "newer": False, "error": f"{type(e).__name__}: {e}"}
    newer = parse_version(info["version"]) > parse_version(current)
    out = {"current": current, "latest": info["version"], "newer": newer, "page": info.get("page"),
           "notes": info.get("notes", ""), "can_update": False, "reason": None, "info": info}
    if newer:
        if not installed_by_uv_tool():
            out["reason"] = "開発用の環境で動いているので、ここからは更新しない（git pull で更新する）"
        elif needed_asset(info["version"]) not in info.get("assets", {}):
            out["reason"] = f"この版の Release に {needed_asset(info['version'])} がない"
        else:
            out["can_update"] = True
    return out


def download(url: str, dest: pathlib.Path, sha256: str | None = None, *, timeout_s: float = 60.0) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": f"talkscript/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            h.update(b)
            f.write(b)
    if sha256 and h.hexdigest() != sha256.lower():
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"ダウンロードした {dest.name} の sha256 が Release の記録と違う")
    os.replace(tmp, dest)
    return dest


def _uv() -> str:
    for c in (shutil.which("uv"), str(pathlib.Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv"))):
        if c and pathlib.Path(c).is_file():
            return c
    raise UpdateError("uv が見つからない（インストーラーをもう一度実行する）")


def start_update(result: dict, *, wait_pids=(), log=print) -> str:
    """更新を始める。戻り値: "installer"（Windows。インストーラーの窓を開いた。このプログラムは終わること）
    / "done"（Linux。入れ直した。開き直すと新しい版）。"""
    if not result.get("can_update"):
        raise UpdateError(result.get("reason") or "更新できる新しい版がない")
    info = result["info"]
    ver = info["version"]
    asset = info["assets"][needed_asset(ver)]
    d = workspace.user_data_dir() / "updates"
    if sys.platform == "win32":
        exe = download(asset["url"], d / f"talkscript-setup-{ver}.exe", asset.get("sha256"))
        log(f"インストーラーを開く: {exe}")
        args = [str(exe), "--wait-pid", ",".join(str(p) for p in wait_pids if p), "-Update"]
        flags = 0x00000010 | 0x00000200          # 新しいコンソールの窓、新しいプロセスグループ
        try:
            subprocess.Popen(args, creationflags=flags | 0x01000000, close_fds=True)   # ジョブから外れる（できれば）
        except OSError:
            subprocess.Popen(args, creationflags=flags, close_fds=True)
        return "installer"
    whl = download(asset["url"], d / needed_asset(ver), asset.get("sha256"))
    uv = _uv()
    log(f"入れ直す: {whl}")
    r = subprocess.run([uv, "tool", "install", "--force", "--python", PYTHON, "--from", str(whl), "talkscript"],
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise UpdateError(f"uv tool install に失敗した: {(r.stderr or r.stdout).strip()[-400:]}")
    from . import shortcut
    if shortcut.exists():    # 新しい版のコマンドで作り直す（アイコン・起動先）
        bin_dir = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, encoding="utf-8", errors="replace").stdout.strip()
        ts = pathlib.Path(bin_dir) / "talkscript"
        if ts.is_file():
            subprocess.run([str(ts), "shortcut", "--refresh"], capture_output=True)
    return "done"
