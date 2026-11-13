"""画面（GUI）を開くショートカット。

Windows: スタートメニュー（%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs）とデスクトップに talkscript.lnk。
Linux:   ~/.local/share/applications/talkscript.desktop（アプリ一覧）と、デスクトップのフォルダがあればそこにも。
どちらも talkscript-gui（Windows ではコンソールの窓を出さない起動口）を開く。
アイコンは同梱の talkscript/assets/ から、PC ごとのデータの場所へ写して使う（talkscript を入れ直しても場所が変わらない）:
  Windows: %LOCALAPPDATA%\\talkscript\\talkscript.ico
  Linux:   ~/.local/share/icons/hicolor/256x256/apps/talkscript.png
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

NAME = "talkscript"
ASSETS = pathlib.Path(__file__).resolve().parent / "assets"


class ShortcutError(Exception):
    pass


def gui_exe() -> pathlib.Path:
    here = pathlib.Path(sys.executable).parent
    cands = [here / "talkscript-gui.exe", here / "Scripts" / "talkscript-gui.exe", here / "talkscript-gui"]
    w = shutil.which("talkscript-gui")
    if w:
        cands.append(pathlib.Path(w))
    for c in cands:
        if c.is_file():
            return c
    raise ShortcutError("talkscript-gui が見つからない（talkscript を入れ直す）")


def icon_path() -> pathlib.Path:
    if sys.platform == "win32":
        from .workspace import user_data_dir
        return user_data_dir() / f"{NAME}.ico"
    base = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share"))
    return base / "icons" / "hicolor" / "256x256" / "apps" / f"{NAME}.png"


def install_icon() -> pathlib.Path | None:
    """同梱のアイコンを写す。写せなければ None（アイコンなしでショートカットを作る）。"""
    src = ASSETS / (f"{NAME}.ico" if sys.platform == "win32" else f"{NAME}.png")
    dst = icon_path()
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return dst
    except OSError:
        return None


def _desktop() -> pathlib.Path | None:
    if os.environ.get("TALKSCRIPT_DESKTOP_DIR"):   # 試験で、本物のデスクトップに作らないため
        p = pathlib.Path(os.environ["TALKSCRIPT_DESKTOP_DIR"])
        return p if p.is_dir() else None
    if sys.platform == "win32":
        p = _known_folder_desktop() or pathlib.Path(os.environ.get("USERPROFILE", pathlib.Path.home())) / "Desktop"
        return p if p.is_dir() else None
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, encoding="utf-8", errors="replace",
                             timeout=5).stdout.strip()
        p = pathlib.Path(out) if out else pathlib.Path.home() / "Desktop"
    except (OSError, subprocess.SubprocessError):
        p = pathlib.Path.home() / "Desktop"
    return p if p.is_dir() and p != pathlib.Path.home() else None


def _known_folder_desktop() -> pathlib.Path | None:
    """Windows のデスクトップの場所（OneDrive の「デスクトップ」に移されていても）。
    PowerShell に尋ねると、出力の文字コード（UTF-8 か cp932 か）で読み違えることがあるので、Windows に直接尋ねる。"""
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
    g = GUID.from_buffer_copy(uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}").bytes_le)   # FOLDERID_Desktop
    out = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        return pathlib.Path(out.value) if out.value else None
    except (OSError, AttributeError):
        return None
    finally:
        if out:
            ctypes.windll.ole32.CoTaskMemFree(out)


def start_menu_path() -> pathlib.Path:
    """スタートメニュー（Windows）・アプリの一覧（Linux）のショートカット。Windows の検索はここから探す。"""
    if sys.platform == "win32":
        return pathlib.Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{NAME}.lnk"
    return pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share")) / "applications" / f"{NAME}.desktop"


def desktop_path() -> pathlib.Path | None:
    d = _desktop()
    return d / (f"{NAME}.lnk" if sys.platform == "win32" else f"{NAME}.desktop") if d else None


def paths() -> list[pathlib.Path]:
    d = desktop_path()
    return [start_menu_path()] + ([d] if d else [])


def where() -> str:
    return "スタートメニュー（Windows の検索にも出る）" if sys.platform == "win32" else "アプリの一覧"


def exists() -> bool:
    return start_menu_path().exists()


def desktop_exists() -> bool:
    d = desktop_path()
    return bool(d and d.exists())


def create(desktop: bool | None = True) -> list[pathlib.Path]:
    """ショートカットを作る（あれば作り直す。アイコンと起動先を今の talkscript に合わせる）。
    スタートメニュー（アプリの一覧）には必ず作る。デスクトップは desktop が True なら作り、None ならすでにあるときだけ作り直す。"""
    exe = gui_exe()
    icon = install_icon()
    made = []
    d = desktop_path()
    want_desk = d is not None and (desktop or (desktop is None and d.exists()))
    for p in [start_menu_path()] + ([d] if want_desk else []):
        p.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TS_LNK); $s.TargetPath = $env:TS_EXE; "
                  "$s.WorkingDirectory = $env:USERPROFILE; $s.Description = '発話と動作の台本ツール talkscript'; "
                  "if ($env:TS_ICO) { $s.IconLocation = $env:TS_ICO + ',0' }; $s.Save()")
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                               env={**os.environ, "TS_LNK": str(p), "TS_EXE": str(exe), "TS_ICO": str(icon or "")},
                               capture_output=True, encoding="utf-8", errors="replace", timeout=30)
            if r.returncode != 0 or not p.exists():
                raise ShortcutError(f"{p} を作れない: {r.stderr.strip()[:200]}")
        else:
            p.write_text("[Desktop Entry]\nType=Application\nName=talkscript\nComment=発話と動作の台本ツール\n"
                         f"Exec=\"{exe}\"\n" + (f"Icon={icon}\n" if icon else "") +
                         "Terminal=false\nCategories=Development;AudioVideo;\n", encoding="utf-8")
            p.chmod(0o755)
        made.append(p)
    return made


def remove() -> list[pathlib.Path]:
    gone = []
    for p in paths() + [icon_path()]:
        if p.exists():
            p.unlink()
            gone.append(p)
    return gone
