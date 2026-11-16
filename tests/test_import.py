"""台本・ひな形の取り込み（talkscript import と、画面の「読み込む」）。"""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from talkscript import cli, importer, io

SCRIPT = """format: talkscript/script@1
id: trivia
purpose: demo
# 取り込んでも残るコメント
voice:
  speaker: {name: テスト, style: ノーマル}
robots: [self7]
blocks:
  - id: B1
    lines:
      - {id: L1, say: タコには心臓が三つあります。}
    tracks:
      self7:
        neck_pitch:
          - {at: {line: L1, text: 三つ, edge: end}, deg: 8}
          - {at: {line: L1, text: 三つ, edge: end}, offset_ms: 400, deg: 0}
"""

GESTURE = """format: talkscript/gesture@1
id: wave
description: 右手を振る
anchor_at_ms: 0
robots:
  self7:
    r_shoulder: [[0, 0], [500, 40], [1500, 40], [2000, 0]]
"""


def test_import_script_keeps_text_and_comments(tmp_repo):
    r = importer.import_text(SCRIPT, "trivia.yaml")
    assert r["kind"] == "script" and r["id"] == "trivia" and not r["replaced"] and not r["warnings"]
    text = (io.SCRIPTS / "trivia.yaml").read_text(encoding="utf-8")
    assert text == SCRIPT and "# 取り込んでも残るコメント" in text
    s, _ = io.load_script("trivia")
    assert s["blocks"][0]["lines"][0]["say"] == "タコには心臓が三つあります。"


def test_import_conflict_overwrite_and_rename(tmp_repo):
    importer.import_text(SCRIPT, "trivia.yaml")
    with pytest.raises(importer.Exists):
        importer.import_text(SCRIPT, "trivia.yaml")
    r = importer.import_text(SCRIPT.replace("三つ", "3つ"), "trivia.yaml", overwrite=True)
    assert r["replaced"] and (io.SCRIPTS / "trivia.yaml.bak").read_text(encoding="utf-8") == SCRIPT
    r = importer.import_text(SCRIPT, "trivia.yaml", new_id="trivia-2")
    text = (io.SCRIPTS / "trivia-2.yaml").read_text(encoding="utf-8")
    assert r["id"] == "trivia-2" and "id: trivia-2\n" in text and "# 取り込んでも残るコメント" in text
    io.load_script("trivia-2")                                   # id とファイル名がそろっている


def test_import_rejects_bad_files(tmp_repo):
    with pytest.raises(importer.ImportError_, match="format"):
        importer.import_text("format: something/else@1\nid: x\n", "x.yaml")
    with pytest.raises(importer.ImportError_, match="YAML"):
        importer.import_text("a: [", "x.yaml")
    bad = SCRIPT.replace("text: 三つ, edge: end}, deg: 8", "text: 三つ, edge: middle}, deg: 8")
    with pytest.raises(importer.ImportError_, match="edge"):
        importer.import_text(bad, "trivia.yaml")
    with pytest.raises(importer.ImportError_, match="英数字"):
        importer.import_text(SCRIPT, "trivia.yaml", new_id="タコ")
    assert not (io.SCRIPTS / "trivia.yaml").exists()            # 誤りがあれば何も書かない


def test_import_warns_unknown_robot_and_tbd_speaker(tmp_repo):
    s = SCRIPT.replace("robots: [self7]", "robots: [self7, pepper]").replace("name: テスト", "name: TBD")
    r = importer.import_text(s, "trivia.yaml")                   # 形式は正しいので取り込み、注意を返す
    assert any("pepper" in w for w in r["warnings"]) and any("TBD" in w for w in r["warnings"])


def test_import_gesture(tmp_repo):
    r = importer.import_text(GESTURE, "wave.yaml")
    assert r["kind"] == "gesture" and (io.CONFIG / "gestures" / "wave.yaml").exists()
    assert io.load_gesture("wave")["robots"]["self7"]["r_shoulder"][1] == [500, 40]
    with pytest.raises(importer.ImportError_, match="時刻が増えていない"):
        importer.import_text(GESTURE.replace("[1500, 40]", "[300, 40]"), "wave2.yaml", new_id="wave2")


def test_cli_import(tmp_repo, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_repo)
    f = tmp_path / "trivia.yaml"
    f.write_text("﻿" + SCRIPT, encoding="utf-8")              # BOM つきでも読める
    assert cli.main(["import", str(f)]) == 0
    assert "取り込んだ" in capsys.readouterr().out
    assert cli.main(["import", str(f)]) == 1
    assert "--force" in capsys.readouterr().out
    assert cli.main(["import", str(f), "--force"]) == 0


def test_gui_import_api(tmp_repo):
    from talkscript.gui import server
    from talkscript.testing import FakeVoicevox
    app = server.App(tmp_repo, voicevox_factory=FakeVoicevox, log=lambda *_: None)
    srv = server.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(body):
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/script/import", method="POST",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"X-Token": app.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert call({"name": "trivia.yaml", "text": SCRIPT})[1]["id"] == "trivia"
        st, r = call({"name": "trivia.yaml", "text": SCRIPT})
        assert st == 200 and r["exists"] and r["id"] == "trivia"         # 画面が上書きか別の名前かを尋ねる
        assert call({"name": "trivia.yaml", "text": SCRIPT, "id": "trivia", "overwrite": True})[1]["replaced"]
        assert call({"name": "x.yaml", "text": "a: 1"})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


# --- 数の項目に文字を入れた台本（画面に値をそのまま入れる箇所があったため） ------------------------------

INJECT = '"><img src=x onerror=alert(1)>'


@pytest.mark.parametrize("where", ["voice.speed", "gap_ms", "lead_in_ms", "tail_ms", "voice.pitch", "line.voice.volume",
                                   "script.gap_ms", "voice.speaker.name", "voice.sampling_rate"])
def test_import_rejects_non_numbers(tmp_repo, where):
    import yaml
    d = yaml.safe_load(SCRIPT)
    b, ln = d["blocks"][0], d["blocks"][0]["lines"][0]
    if where == "voice.speed":
        d["voice"]["speed"] = INJECT
    elif where == "voice.pitch":
        d["voice"]["pitch"] = float("nan")
    elif where == "gap_ms":
        ln["gap_ms"] = INJECT
    elif where == "lead_in_ms":
        b["lead_in_ms"] = INJECT
    elif where == "tail_ms":
        b["tail_ms"] = INJECT
    elif where == "line.voice.volume":
        ln["voice"] = {"volume": INJECT}
    elif where == "script.gap_ms":
        d["gap_ms"] = INJECT
    elif where == "voice.speaker.name":
        d["voice"]["speaker"] = {"name": ["x"], "style": "ノーマル"}
    elif where == "voice.sampling_rate":
        d["voice"]["sampling_rate"] = 24000.5
    with pytest.raises(importer.ImportError_):
        importer.import_text(yaml.safe_dump(d, allow_unicode=True), "trivia.yaml")
    assert not (io.SCRIPTS / "trivia.yaml").exists()


def test_page_has_csp_with_nonce_and_no_inline_handlers(tmp_repo):
    import re
    from talkscript.gui import server
    app = server.App(tmp_repo, log=lambda *_: None)
    srv = server.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        with urllib.request.urlopen(url, timeout=10) as r:
            csp, html = r.headers.get("Content-Security-Policy", ""), r.read().decode("utf-8")
        with urllib.request.urlopen(url, timeout=10) as r:
            csp2 = r.headers.get("Content-Security-Policy", "")
        m = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", csp)
        assert m and f'<script nonce="{m.group(1)}">' in html                      # スクリプトは nonce で許す
        assert csp != csp2                                                           # 読み込むたびに変わる
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]     # インラインのハンドラは動かない
        assert "object-src 'none'" in csp and "base-uri 'none'" in csp
        assert not re.search(r"<[^>]+\son[a-z]+\s*=", html)                          # 画面自身もインラインのハンドラを使わない
    finally:
        srv.shutdown()
        srv.server_close()
