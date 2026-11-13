"""talkscript の画面（GUI）。

    talkscript               # 引数なしで画面を開く
    talkscript gui           # 同じ
    talkscript-gui           # 同じ（Windows ではコンソールの窓を出さない。スタートメニューのショートカットはこれを開く）

PC の中だけで動く小さなサーバ（127.0.0.1）を立て、Edge か Chrome（Chromium）のアプリ用の窓で画面を開く。
どちらもなければ、既定のブラウザで開く。すでに開いていれば、その画面をもう一度開くだけにする。
画面の窓を閉じると、30 秒後にサーバも終わる（実行中は終わらない）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import urllib.request
import webbrowser

from .. import workspace


def _state_file() -> pathlib.Path:
    return workspace.user_data_dir() / "gui.json"


def _log_file() -> pathlib.Path:
    return workspace.user_data_dir() / "gui.log"


def _alive(info: dict) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/ping", headers={"X-Token": info["token"]})
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except (OSError, ValueError, KeyError):
        return False


def _browser_candidates() -> list[str]:
    if sys.platform == "win32":
        pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
        return [str(pathlib.Path(b) / r) for r in rel for b in pf if b]
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable"]
    return [p for p in (shutil.which(n) for n in names) if p]


def open_window(url: str) -> str:
    """アプリ用の窓で開く。戻り値は使ったもの。"""
    for exe in _browser_candidates():
        if pathlib.Path(exe).is_file():
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1500,960"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 **({"creationflags": 0x00000008} if sys.platform == "win32" else {"start_new_session": True}))
                return exe
            except OSError:
                continue
    webbrowser.open(url)
    return "既定のブラウザ"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="talkscript-gui", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", help="作業フォルダ")
    ap.add_argument("--port", type=int, default=0, help="待ち受けるポート（既定: 空いているもの）")
    ap.add_argument("--no-window", action="store_true", help="窓を開かない（URL を表示するだけ）")
    a = ap.parse_args(argv)

    def log(text):
        line = str(text)
        try:
            if sys.stdout is not None:
                print(line, flush=True)
        except (OSError, ValueError):
            pass
        try:
            _log_file().parent.mkdir(parents=True, exist_ok=True)
            with open(_log_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    sf = _state_file()
    if sf.exists() and not a.port:
        try:
            info = json.loads(sf.read_text(encoding="utf-8"))
        except ValueError:
            info = {}
        if info and _alive(info):
            url = f"http://127.0.0.1:{info['port']}/#{info['token']}"
            log(f"すでに動いている画面を開く: {url}")
            if not a.no_window:
                open_window(url)
            return 0

    from . import server
    ws = workspace.find_workspace(a.workspace)
    if ws is not None and not (ws / "talkscript.toml").exists():
        ws = None
    app = server.App(ws, log=log)
    srv = server.serve(app, a.port)
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/#{app.token}"
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps({"port": port, "token": app.token, "pid": os.getpid()}), encoding="utf-8")
    log(f"talkscript の画面: {url}（作業フォルダ {ws}）")
    if not a.no_window:
        log(f"窓を開く: {open_window(url)}")
    threading.Thread(target=server.idle_watch, args=(app,), daemon=True).start()
    app.start_update_check()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        try:
            if json.loads(sf.read_text(encoding="utf-8")).get("pid") == os.getpid():
                sf.unlink()
        except (OSError, ValueError):
            pass
    return 0
