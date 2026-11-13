"""作業フォルダ・設定・VOICEVOX の導入（小さな擬似の配布ファイル）・コマンドの入口。"""
from __future__ import annotations

import hashlib
import io as _io
import os
import sys
import tarfile
import zipfile

import pytest

from talkscript import cli, engine, io, workspace


@pytest.fixture
def user_dirs(tmp_path, monkeypatch):
    """PC ごとの設定・データの場所を一時フォルダへ。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdgconf"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdgdata"))
    monkeypatch.delenv(workspace.ENV, raising=False)
    monkeypatch.setattr(io, "ROOT", None)
    return tmp_path


def test_init_and_find(user_dirs, monkeypatch):
    ws = user_dirs / "work"
    made = workspace.init_workspace(ws)
    assert "talkscript.toml" in made and (ws / "config" / "robots" / "self7.yaml").exists()
    assert (ws / ".gitignore").exists() and (ws / "runs").is_dir()
    assert workspace.init_workspace(ws) == []                     # 2 回目は何も上書きしない
    monkeypatch.chdir(ws / "scripts")
    assert workspace.find_workspace() == ws.resolve()              # 上へたどって見つける
    monkeypatch.chdir(user_dirs)
    assert workspace.find_workspace() is None
    workspace.write_user_settings({"workspace": str(ws)})
    assert workspace.find_workspace() == ws.resolve()              # PC ごとの設定の既定
    monkeypatch.setenv(workspace.ENV, str(user_dirs / "other"))
    assert workspace.find_workspace() == (user_dirs / "other").resolve()


def test_settings_layering(user_dirs):
    ws = user_dirs / "work"
    workspace.init_workspace(ws)
    (ws / "talkscript.toml").write_text('[self7]\nhost = "pi.local"\n[play]\nlead_s = 3.0\n', encoding="utf-8")
    workspace.write_user_settings({"audio": {"device": "USB スピーカー"}, "play": {"lead_s": 2.5},
                                   "voicevox": {"engine_path": "C:\\VV\\run.exe"}})
    st = workspace.load_settings(ws)
    assert st["self7"]["host"] == "pi.local"                      # 作業フォルダ
    assert st["play"]["lead_s"] == 2.5                            # PC ごとの設定が優先
    assert st["play"]["rest_s"] == 3.0                            # 同梱の既定値
    assert st["audio"]["device"] == "USB スピーカー"
    assert st["voicevox"]["engine_path"] == "C:\\VV\\run.exe"     # 逆斜線の書き戻しと読み直し


def _fake_archive(tmp_path, kind):
    """VOICEVOX/vv-engine/run(.exe) だけを含む小さな配布ファイル。"""
    name = engine.run_name()
    if kind == "zip":
        p = tmp_path / "vv.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr(f"VOICEVOX/vv-engine/{name}", b"fake")
    else:
        p = tmp_path / "vv.tar.gz"
        with tarfile.open(p, "w:gz") as t:
            ti = tarfile.TarInfo(f"VOICEVOX/vv-engine/{name}")
            ti.size = 4
            ti.mode = 0o755
            t.addfile(ti, _io.BytesIO(b"fake"))
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    return p, sha


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_install_voicevox_verifies_and_extracts(user_dirs, kind):
    src, sha = _fake_archive(user_dirs, kind)
    exe = engine.install_voicevox(asset=(src.name, sha, src.stat().st_size), url=src.as_uri(), log=lambda *_: None)
    assert exe.is_file() and exe.read_bytes() == b"fake"
    assert engine.find_engine({}) == exe
    if sys.platform != "win32":
        assert os.access(exe, os.X_OK)


def test_install_voicevox_rejects_bad_hash(user_dirs):
    src, _ = _fake_archive(user_dirs, "zip")
    with pytest.raises(engine.EngineError, match="sha256"):
        engine.install_voicevox(asset=(src.name, "0" * 64, src.stat().st_size), url=src.as_uri(), log=lambda *_: None)
    assert engine.find_engine({}) is None


def test_assets_cover_windows_and_linux():
    assert {("win32", "x64"), ("linux", "x64"), ("linux", "arm64")} <= set(engine.ASSETS)


def test_cli_requires_workspace(user_dirs, monkeypatch, capsys):
    monkeypatch.chdir(user_dirs)
    assert cli.main(["compile", "demo"]) == 2
    assert "作業フォルダが見つからない" in capsys.readouterr().out


def test_cli_init_and_compile(user_dirs, monkeypatch, capsys):
    monkeypatch.chdir(user_dirs)
    assert cli.main(["init", str(user_dirs / "w"), "--default"]) == 0
    assert workspace.load_user_settings()["workspace"] == str((user_dirs / "w").resolve())
    assert cli.main(["compile", "demo"]) == 1                     # 合成していないので検査で止まる
    out = capsys.readouterr().out
    assert "K3" in out and "検査: エラー" in out


def test_setup_without_tty_does_not_install(user_dirs, monkeypatch, capsys):
    monkeypatch.chdir(user_dirs)
    monkeypatch.setattr(engine, "running_version", lambda url: None)
    monkeypatch.setattr(engine, "install_voicevox", lambda **k: pytest.fail("確認なしに導入した"))
    monkeypatch.setattr(sys, "stdin", _io.StringIO(""))
    cli.main(["setup"])
    out = capsys.readouterr().out
    assert "VOICEVOX の利用規約" in out and "対話できないので行わない" in out
    assert not (user_dirs / "talkscript-work").exists()
