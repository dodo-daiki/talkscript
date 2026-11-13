#!/usr/bin/env python3
"""Windows のインストーラー talkscript-setup.exe を作る（Windows で実行する）。

    python tools/build_setup.py [出力先のフォルダ（既定: dist）] [--script 入れる ps1（既定: install.ps1）]

install.ps1 を中に入れた小さな exe を、Windows に入っている .NET Framework 4 の C# コンパイラ（csc.exe）で作る。
アイコンは talkscript/assets/talkscript.ico。exe の版は talkscript の版と同じにする。
Release には talkscript-setup.exe の名前で載せる（README の「最新の版」のリンクが、この名前を指す）。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "setup_exe"


def csc() -> pathlib.Path:
    win = pathlib.Path(os.environ.get("WINDIR", r"C:\Windows"))
    for fw in ("Framework64", "Framework"):
        p = win / "Microsoft.NET" / fw / "v4.0.30319" / "csc.exe"
        if p.is_file():
            return p
    raise SystemExit("csc.exe（.NET Framework 4）が見つからない。Windows で実行する")


def version() -> str:
    m = re.search(r'__version__ = "([^"]+)"', (ROOT / "talkscript" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(ROOT / "dist"))
    ap.add_argument("--script", default=str(ROOT / "install.ps1"))
    a = ap.parse_args(argv)
    ver = version()
    script = pathlib.Path(a.script).resolve()
    if a.script == str(ROOT / "install.ps1"):
        m = re.search(r'\$TalkscriptVersion = "([^"]+)"', script.read_text(encoding="utf-8-sig"))
        if not m or m.group(1) != ver:
            raise SystemExit(f"install.ps1 の版（{m and m.group(1)}）が talkscript の版（{ver}）と違う")
    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    exe = out / "talkscript-setup.exe"
    num = ".".join((re.findall(r"\d+", ver) + ["0"] * 4)[:4])
    with tempfile.TemporaryDirectory() as t:
        info = pathlib.Path(t) / "Version.cs"
        info.write_text(f'[assembly: System.Reflection.AssemblyVersion("{num}")]\n'
                        f'[assembly: System.Reflection.AssemblyFileVersion("{num}")]\n'
                        f'[assembly: System.Reflection.AssemblyInformationalVersion("{ver}")]\n', encoding="utf-8")
        cmd = [str(csc()), "/nologo", "/target:exe", "/platform:anycpu", "/optimize+", "/codepage:65001",
               f"/win32icon:{ROOT / 'talkscript' / 'assets' / 'talkscript.ico'}",
               f"/win32manifest:{SRC / 'app.manifest'}",
               f"/resource:{script},install.ps1", f"/out:{exe}", str(SRC / "TalkscriptSetup.cs"), str(info)]
        r = subprocess.run(cmd, capture_output=True)
        text = (r.stdout + r.stderr).decode("cp932", "replace").strip()
        if r.returncode != 0:
            print(text)
            return r.returncode
        if text:
            print(text)
    print(f"作った: {exe}（talkscript {ver}、{exe.stat().st_size // 1024} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
