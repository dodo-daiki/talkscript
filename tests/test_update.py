"""新しい版の確認・更新と、アンインストール（ネットにはつながない）。"""
from __future__ import annotations

import hashlib
import io as _io
import json
import sys

import pytest

from talkscript import cli, engine, io, update, workspace


@pytest.fixture
def user_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdgconf"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdgdata"))
    monkeypatch.delenv(workspace.ENV, raising=False)
    monkeypatch.setattr(io, "ROOT", None)
    return tmp_path


def release(version, assets=("talkscript-setup.exe",), sha=None):
    names = list(assets) + [f"talkscript-{version}-py3-none-any.whl"]
    return {"version": version, "tag": f"v{version}", "page": f"https://example.invalid/v{version}", "notes": "変更点",
            "published": None, "assets": {n: {"url": f"https://example.invalid/{n}", "size": 1, "sha256": sha} for n in names}}


def test_parse_version():
    assert update.parse_version("v0.2.10") > update.parse_version("0.2.9")
    assert update.parse_version("0.3") > update.parse_version("0.2.99")
    assert update.parse_version("") == ()


def test_check_newer_older_offline(user_dirs, monkeypatch):
    monkeypatch.setattr(update, "installed_by_uv_tool", lambda: True)
    r = update.check(fetch=lambda: release("9.0.0"), current="0.2.2")
    assert r["newer"] and r["can_update"] and r["latest"] == "9.0.0"
    r = update.check(force=True, fetch=lambda: release("0.1.0"), current="0.2.2")
    assert not r["newer"] and not r["can_update"]

    def offline():
        raise OSError("つながらない")
    r = update.check(force=True, fetch=offline, current="0.2.2")
    assert r["error"] and not r["newer"]                       # 画面は普通に使う


def test_check_uses_cache(user_dirs):
    n = []

    def fetch():
        n.append(1)
        return release("9.0.0")
    update.check(fetch=fetch)
    update.check(fetch=fetch)
    assert len(n) == 1                                          # 1 時間は覚えておく
    update.check(fetch=fetch, force=True)
    assert len(n) == 2


def test_cannot_update_dev_env_or_missing_asset(user_dirs, monkeypatch):
    monkeypatch.setattr(update, "installed_by_uv_tool", lambda: False)
    r = update.check(force=True, fetch=lambda: release("9.0.0"))
    assert r["newer"] and not r["can_update"] and "git pull" in r["reason"]
    monkeypatch.setattr(update, "installed_by_uv_tool", lambda: True)
    monkeypatch.setattr(update, "needed_asset", lambda v: "talkscript-setup.exe")
    r = update.check(force=True, fetch=lambda: release("9.0.0", assets=()))
    assert not r["can_update"] and "talkscript-setup.exe" in r["reason"]


def test_download_checks_sha256(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"talkscript")
    good = hashlib.sha256(b"talkscript").hexdigest()
    assert update.download(src.as_uri(), tmp_path / "out" / "a.bin", good).read_bytes() == b"talkscript"
    with pytest.raises(update.UpdateError, match="sha256"):
        update.download(src.as_uri(), tmp_path / "out" / "b.bin", "0" * 64)
    assert not (tmp_path / "out" / "b.bin").exists() and not (tmp_path / "out" / "b.bin.part").exists()


def test_start_update_windows_opens_installer(user_dirs, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr(update, "installed_by_uv_tool", lambda: True)
    got = {}
    monkeypatch.setattr(update, "download", lambda url, dest, sha=None: got.setdefault("dl", (url, dest)) and dest)

    class P:
        def __init__(self, args, **kw):
            got["args"], got["kw"] = args, kw
    monkeypatch.setattr(update.subprocess, "Popen", P)
    r = update.check(force=True, fetch=lambda: release("9.0.0"))
    assert update.start_update(r, wait_pids=[11, 22], log=lambda *_: None) == "installer"
    assert got["dl"][0].endswith("talkscript-setup.exe") and got["dl"][1].name == "talkscript-setup-9.0.0.exe"
    assert got["args"][1:] == ["--wait-pid", "11,22", "-Update"]
    assert got["kw"]["creationflags"] & 0x10                    # 新しい窓


def test_start_update_linux_reinstalls(user_dirs, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "linux")
    monkeypatch.setattr(update, "installed_by_uv_tool", lambda: True)
    monkeypatch.setattr(update, "download", lambda url, dest, sha=None: dest)
    monkeypatch.setattr(update, "_uv", lambda: "uv")
    runs = []

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(update.subprocess, "run", lambda args, **kw: runs.append(args) or R())
    from talkscript import shortcut
    monkeypatch.setattr(shortcut, "exists", lambda: False)
    r = update.check(force=True, fetch=lambda: release("9.0.0"))
    assert update.start_update(r, log=lambda *_: None) == "done"
    assert runs[0][:3] == ["uv", "tool", "install"] and runs[0][-1] == "talkscript"
    assert runs[0][-3:-1] == ["--from", str(workspace.user_data_dir() / "updates" / "talkscript-9.0.0-py3-none-any.whl")]


def test_cli_update_check(user_dirs, monkeypatch, capsys):
    monkeypatch.setattr(update, "fetch_latest", lambda: release("9.0.0"))
    monkeypatch.setattr(update, "installed_by_uv_tool", lambda: True)
    assert cli.main(["update", "--check"]) == 0
    assert "新しい版があります" in capsys.readouterr().out


def test_uninstall_removes_ours_and_keeps_workspace(user_dirs, monkeypatch, capsys):
    from talkscript import shortcut
    desk = user_dirs / "desktop"
    desk.mkdir()
    monkeypatch.setenv("TALKSCRIPT_DESKTOP_DIR", str(desk))
    ws = user_dirs / "work"
    workspace.init_workspace(ws)
    workspace.write_user_settings({"workspace": str(ws)})
    vv = engine.install_dir()
    (vv / "vv-engine").mkdir(parents=True)
    (vv / "vv-engine" / "run.exe").write_bytes(b"x" * 10)
    (workspace.user_data_dir() / "update.json").write_text("{}", encoding="utf-8")
    made = shortcut.create()
    monkeypatch.setattr(sys, "stdin", _io.StringIO(""))          # 対話できない → 尋ねるものは消さない
    assert cli.main(["uninstall"]) == 0
    out = capsys.readouterr().out
    assert not any(p.exists() for p in made) and not shortcut.icon_path().exists()
    assert vv.exists() and workspace.user_settings_path().exists() and "[残す] VOICEVOX" in out
    assert not (workspace.user_data_dir() / "update.json").exists()
    assert cli.main(["uninstall", "--yes"]) == 0                # すべて「はい」
    out = capsys.readouterr().out
    assert not vv.exists() and not workspace.user_config_dir().exists()
    assert ws.exists() and (ws / "talkscript.toml").exists() and "作業フォルダ" in out   # 作業フォルダは消さない
    assert not workspace.user_data_dir().exists()


def test_uninstall_refuses_while_running(user_dirs, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_close_gui", lambda: False)
    assert cli.main(["uninstall", "--yes"]) == 1
    assert "実機の実行" in capsys.readouterr().out


def test_gui_update_api(tmp_repo, monkeypatch):
    import threading
    import urllib.request
    from talkscript.gui import server
    from talkscript.testing import FakeVoicevox
    monkeypatch.setattr(update, "installed_by_uv_tool", lambda: True)
    app = server.App(tmp_repo, voicevox_factory=FakeVoicevox, log=lambda *_: None)
    srv = server.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/api/"

    def call(method, path):
        req = urllib.request.Request(base + path, method=method, data=b"{}" if method == "POST" else None,
                                     headers={"X-Token": app.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert call("GET", "state")[1]["update"] is None           # 起動口（gui.main）から呼んだときだけ確かめる
        assert call("POST", "update")[0] == 409
        app.start_update_check(lambda: update.check(force=True, fetch=lambda: release("9.0.0"), current="0.2.2"))
        for _ in range(50):
            u = call("GET", "state")[1]["update"]
            if not u.get("pending"):
                break
        assert u["newer"] and u["latest"] == "9.0.0" and u["can_update"] and "info" not in u
        seen = {}
        monkeypatch.setattr(update, "start_update", lambda r, **kw: seen.update(kw) or "done")
        assert call("POST", "update") == (200, {"mode": "done", "latest": "9.0.0"})
        assert len(seen["wait_pids"]) == 2
        app.run.state = "running"
        assert call("POST", "update")[0] == 409                    # 実機の実行中は更新しない
    finally:
        srv.shutdown()
        srv.server_close()


def test_update_check_can_be_disabled(tmp_repo, user_dirs):
    from talkscript.gui import server
    workspace.write_user_settings({"update": {"check": False}})
    app = server.App(tmp_repo, log=lambda *_: None)
    app.start_update_check(lambda: pytest.fail("確かめないはず"))
    assert app.update is None
