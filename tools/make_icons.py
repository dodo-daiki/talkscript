#!/usr/bin/env python3
"""アイコンの画像を、SVG（talkscript/assets/icon.svg と、小さい大きさ用の icon-small.svg）から作る。

    uv run --with pillow python tools/make_icons.py [見本の画像.png]

作るもの（talkscript/assets/）:
  talkscript.ico  Windows のショートカットとインストーラー（16〜256 px。32 px 以下は icon-small.svg から）
  talkscript.png  Linux のアプリ一覧（256 px）
SVG を画像にするのに、Edge / Chrome を画面なしで使う（環境変数 BROWSER で場所を変えられる）。
"""
from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS = ROOT / "talkscript" / "assets"
BROWSER = os.environ.get("BROWSER") or (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
                                         if sys.platform == "win32" else "chromium")
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SMALL_MAX = 32
RENDER = 1024     # 大きく描いてから縮める（縮めるときに滑らかにするため）


def render(svg: pathlib.Path, tmp: pathlib.Path) -> Image.Image:
    """SVG を RENDER px 四方の透明な背景の画像にする。"""
    html = tmp / f"{svg.stem}.html"
    html.write_text(f'<!doctype html><html><body style="margin:0;background:transparent">'
                    f'<img src="{svg.as_uri()}" width="{RENDER}" height="{RENDER}" style="display:block"></body></html>',
                    encoding="utf-8")
    out = tmp / f"{svg.stem}.png"
    subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                    "--default-background-color=00000000", f"--window-size={RENDER},{RENDER}",
                    f"--screenshot={out}", html.as_uri()], check=True, capture_output=True, timeout=60)
    im = Image.open(out).convert("RGBA")
    if im.size != (RENDER, RENDER):
        raise SystemExit(f"{svg.name}: 画像の大きさが {im.size}（{RENDER} 四方のはず）")
    return im


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        big = render(ASSETS / "icon.svg", tmp)
        small = render(ASSETS / "icon-small.svg", tmp)
    imgs = {n: (small if n <= SMALL_MAX else big).resize((n, n), Image.LANCZOS) for n in SIZES}
    imgs[256].save(ASSETS / "talkscript.png", optimize=True)
    # ICO: 大きさごとに PNG を詰める（Windows Vista 以降はどの大きさでも読める）
    entries = []
    for n in SIZES:
        b = io.BytesIO()
        imgs[n].save(b, "PNG", optimize=True)
        entries.append((n, b.getvalue()))
    head = bytearray((0).to_bytes(2, "little") + (1).to_bytes(2, "little") + len(entries).to_bytes(2, "little"))
    off = 6 + 16 * len(entries)
    body = bytearray()
    for n, data in entries:
        d = 0 if n >= 256 else n
        head += bytes([d, d, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
        head += len(data).to_bytes(4, "little") + (off + len(body)).to_bytes(4, "little")
        body += data
    (ASSETS / "talkscript.ico").write_bytes(bytes(head + body))
    print("作った:", ASSETS / "talkscript.ico", ASSETS / "talkscript.png")
    if argv:   # 見本: 明るい背景と暗い背景に、全部の大きさを並べる
        W = sum(SIZES) + 16 * (len(SIZES) + 1)
        sheet = Image.new("RGBA", (W, 2 * (256 + 32)), "#f2f4f7")
        sheet.paste(Image.new("RGBA", (W, 256 + 32), "#10151d"), (0, 256 + 32))
        for row in range(2):
            x = 16
            for n in SIZES:
                sheet.alpha_composite(imgs[n], (x, row * (256 + 32) + 16 + (256 - n)))
                x += n + 16
        sheet.save(argv[0])
        print("見本:", argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
