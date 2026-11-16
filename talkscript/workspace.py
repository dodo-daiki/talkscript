"""作業フォルダと設定の場所。

作業フォルダ（台本・ロボットの設定・組み立ての出力・実行の記録を置くフォルダ）は、次の順に探す:
  1. コマンドの --workspace
  2. 環境変数 TALKSCRIPT_HOME
  3. 今いるフォルダから上へたどって、talkscript.toml のあるフォルダ
  4. PC ごとの設定 settings.toml の workspace

設定は「同梱の既定値 < 作業フォルダの talkscript.toml < PC ごとの settings.toml」の順に重ねる。
PC ごとの設定・データの場所:
  Windows: %APPDATA%\\talkscript\\settings.toml、%LOCALAPPDATA%\\talkscript\\（VOICEVOX など）
  Linux:   ~/.config/talkscript/settings.toml、~/.local/share/talkscript/
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tomllib

from . import io

ENV = "TALKSCRIPT_HOME"


def user_config_dir() -> pathlib.Path:
    if sys.platform == "win32":
        return pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home() / "AppData" / "Roaming")) / "talkscript"
    return pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "talkscript"


def user_data_dir() -> pathlib.Path:
    if sys.platform == "win32":
        return pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home() / "AppData" / "Local")) / "talkscript"
    return pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share")) / "talkscript"


def user_settings_path() -> pathlib.Path:
    return user_config_dir() / "settings.toml"


def _read_toml(p: pathlib.Path) -> dict:
    if not p.exists():
        return {}
    with open(p, "rb") as f:
        return tomllib.load(f)


def load_user_settings() -> dict:
    return _read_toml(user_settings_path())


def find_workspace(arg: str | None = None) -> pathlib.Path | None:
    if arg:
        return pathlib.Path(arg).resolve()
    if os.environ.get(ENV):
        return pathlib.Path(os.environ[ENV]).resolve()
    here = pathlib.Path.cwd().resolve()
    for d in [here, *here.parents]:
        if (d / "talkscript.toml").exists():
            return d
    ws = load_user_settings().get("workspace")
    if ws:
        return pathlib.Path(ws).expanduser().resolve()
    return None


def merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


#: PC ごとの settings.toml にだけ書ける項目（作業フォルダは共有されるので、そこに書かれた値は使わない）
PC_ONLY = {"voicevox": ("engine_path",)}


def load_settings(ws: pathlib.Path | None) -> dict:
    st = _read_toml(io.DEFAULTS / "talkscript.toml")
    if ws is not None:
        shared = _read_toml(ws / "talkscript.toml")
        for sec, keys in PC_ONLY.items():   # 例: engine_path は起動するプログラムの場所なので、共有するファイルからは受けない
            for k in keys:
                (shared.get(sec) or {}).pop(k, None)
        st = merge(st, shared)
    user = load_user_settings()
    user.pop("workspace", None)
    return merge(st, user)


def init_workspace(path, *, force: bool = False) -> list[str]:
    """同梱の既定値を写して作業フォルダを作る。既にあるファイルは上書きしない（force でも talkscript.toml 以外は残す）。"""
    root = pathlib.Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    made = []
    for src in sorted(io.DEFAULTS.rglob("*")):
        if src.is_dir() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(io.DEFAULTS)
        dst = root / rel
        if dst.exists() and not (force and rel.name == "talkscript.toml"):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        made.append(str(rel))
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text("# 合成した音声（sha256 は build/<台本>/manifest.json に残す）\nbuild/**/*.wav\nbuild/**/preview.png\n"
                      "# 台本の insert --write の控え\nscripts/*.bak\n", encoding="utf-8")
        made.append(".gitignore")
    for d in ("build", "runs"):
        (root / d).mkdir(exist_ok=True)
    return made


def write_user_settings(updates: dict) -> pathlib.Path:
    """PC ごとの settings.toml に値を足す（表は 1 段まで。既存の値は残し、渡した値で上書き）。"""
    cur = merge(load_user_settings(), updates)
    p = user_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# talkscript の PC ごとの設定（talkscript が書く。手で直してよい）"]
    top = {k: v for k, v in cur.items() if not isinstance(v, dict)}
    for k, v in top.items():
        lines.append(f"{k} = {_toml_value(v)}")
    for k, v in cur.items():
        if isinstance(v, dict):
            lines.append(f"\n[{k}]")
            for kk, vv in v.items():
                lines.append(f"{kk} = {_toml_value(vv)}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'
