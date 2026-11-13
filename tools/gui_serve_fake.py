#!/usr/bin/env python3
"""擬似 VOICEVOX で画面のサーバを立てたままにする（画面の操作の試験 tools/gui_e2e.mjs 用）。

    python tools/gui_serve_fake.py <作業フォルダ（なければ作る）> [秒]

1 行目に画面の URL（合言葉つき）を出す。demo の台本は擬似 VOICEVOX で合成しておく。
"""
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from talkscript import build, io  # noqa: E402
from talkscript.gui import server  # noqa: E402
from talkscript.testing import FakeVoicevox, make_workspace  # noqa: E402

ws = pathlib.Path(sys.argv[1]).resolve()
if not (ws / "talkscript.toml").exists():
    make_workspace(ws)
app = server.App(ws, voicevox_factory=FakeVoicevox, log=lambda *_: None)
build.synth(io.load_script("demo")[0], FakeVoicevox(), log=lambda *_: None)
srv = server.serve(app)
app.start_update_check(lambda: {"current": "0.2.2", "latest": "9.9.9", "newer": True, "page": "https://example.invalid/",
                                "notes": "試験用の変更点", "can_update": False, "reason": "（試験: 更新はしない）"})
print(f"http://127.0.0.1:{srv.server_address[1]}/#{app.token}", flush=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 120)
