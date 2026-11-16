"""画面のサーバの API（擬似 VOICEVOX）。"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from talkscript import io
from talkscript.gui import server
from talkscript.testing import FakeVoicevox


@pytest.fixture
def gui(tmp_repo, monkeypatch):
    app = server.App(tmp_repo, voicevox_factory=FakeVoicevox, log=lambda *_: None)
    srv = server.serve(app)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(method, path, body=None, token=app.token, raw=False):
        req = urllib.request.Request(base + "/api/" + path, method=method,
                                     data=None if body is None else json.dumps(body).encode("utf-8"),
                                     headers={"X-Token": token or "", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
                return (r.status, data) if raw else (r.status, json.loads(data))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield app, call, base
    srv.shutdown()
    srv.server_close()


def test_page_and_token(gui):
    app, call, base = gui
    with urllib.request.urlopen(base + "/") as r:
        html = r.read().decode("utf-8")
    assert "<title>talkscript</title>" in html and "fonts.googleapis" not in html
    assert call("GET", "state", token="wrong")[0] == 403
    assert 'href="/icon.svg"' in html
    with urllib.request.urlopen(base + "/icon.svg") as r:
        assert r.headers["Content-Type"] == "image/svg+xml" and b"<svg" in r.read()


def test_state_and_script_roundtrip(gui):
    app, call, _ = gui
    code, st = call("GET", "state")
    assert code == 200 and st["scripts"] == ["demo"]
    assert st["robots"]["self7"]["joints"]["r_shoulder"]["label"] == "右肩"
    assert "nod" in st["gestures"]
    code, sc = call("GET", "script?id=demo")
    assert code == 200 and sc["errors"] is None and sc["lines"]["L1"]["status"] == "unsynthesized"
    s = sc["script"]
    s["blocks"][0]["lines"][0]["say"] = "こんにちは。"
    code, r = call("PUT", "script?id=demo", {"script": s, "base_sha": sc["sha"]})
    assert code == 200 and r["sha"] != sc["sha"]
    assert io.read_yaml(io.SCRIPTS / "demo.yaml")["blocks"][0]["lines"][0]["say"] == "こんにちは。"
    assert (io.SCRIPTS / "demo.yaml.bak").exists()
    code, r2 = call("PUT", "script?id=demo", {"script": s, "base_sha": sc["sha"]})       # 古い sha → 競合
    assert code == 409
    assert call("PUT", "script?id=demo", {"script": s, "base_sha": sc["sha"], "force": True})[0] == 200


def test_synth_check_audio_freeze(gui):
    app, call, _ = gui
    _, sc = call("GET", "script?id=demo")
    code, r = call("POST", "synth", {"script": sc["script"]})
    assert code == 200, r
    l1 = r["lines"]["L1"]
    assert l1["status"] == "ok" and l1["timing"]["phrases"] and l1["envelope"] is not None
    assert "紹介します#1" in l1["text_anchors"]
    code, ck = call("POST", "check", {"script": sc["script"]})
    assert code == 200 and not [i for i in ck["issues"] if i["level"] == "error"], ck["issues"]
    assert "format: talkscript/script@1" in ck["yaml"]
    code, wav = call("GET", "audio?id=demo&line=L1", raw=True)
    assert code == 200 and wav[:4] == b"RIFF"
    assert call("POST", "freeze", {"id": "demo"})[1]["frozen"] is True
    bad = json.loads(json.dumps(sc["script"]))
    bad["blocks"][1]["tracks"]["self7"]["waist_yaw"][1]["t_ms"] = 100
    _, ck = call("POST", "check", {"script": bad})
    assert any(i["code"] == "K7" for i in ck["issues"])


def test_new_script_and_errors(gui):
    app, call, _ = gui
    assert call("POST", "script/new", {"id": "bad name"})[0] == 400
    code, sc = call("POST", "script/new", {"id": "trial1", "template": ""})
    assert code == 200 and sc["script"]["id"] == "trial1" and sc["errors"] is None
    code, sc2 = call("POST", "script/new", {"id": "copy1", "template": "demo"})
    assert code == 200 and len(sc2["script"]["blocks"]) == 2
    assert call("GET", "script?id=nothing")[0] == 404


def test_play_dry_and_stop(gui, monkeypatch):
    app, call, _ = gui
    code, r = call("POST", "play", {"id": "demo", "mode": "dry", "robots": []})
    assert code == 200
    while call("GET", "run")[1]["state"] != "done":
        time.sleep(0.05)
    assert call("GET", "run")[1]["result"].startswith("実行しない")              # 合成していないので検査で止まる
    _, sc = call("GET", "script?id=demo")
    assert call("POST", "synth", {"script": sc["script"]})[0] == 200
    code, r = call("POST", "play", {"id": "demo", "mode": "dry", "robots": []})
    assert code == 200
    deadline = time.monotonic() + 20
    st = {}
    while time.monotonic() < deadline:
        _, st = call("GET", "run?since=0")
        if st["state"] == "operator":                # B2 は操作者の合図で開始
            call("POST", "run/continue")
        if st["state"] == "done":
            break
        time.sleep(0.1)
    assert st["state"] == "done" and st["result"] == "ok", st
    _, runs = call("GET", "runs")
    assert runs["runs"][0]["run_id"] == st["run_id"]
    assert "台本: demo" in call("GET", f"runs/summary?run_id={st['run_id']}")[1]["summary"]

    # 止める: 実行の指揮の代わりに、ずっと待つ関数で試す（KeyboardInterrupt が届くこと）
    from talkscript import run as run_mod
    seen = {}

    def fake_play(*a, **k):
        try:
            while True:
                time.sleep(0.05)
        except KeyboardInterrupt:
            seen["interrupted"] = True
            raise
    monkeypatch.setattr(run_mod, "play", fake_play)
    call("POST", "play", {"id": "demo", "mode": "dry"})
    time.sleep(0.3)
    assert call("POST", "run/stop", {"mode": "graceful"})[1]["running"] is True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and call("GET", "run")[1]["state"] != "done":
        time.sleep(0.05)
    assert seen.get("interrupted") and call("GET", "run")[1]["result"] == "interrupted"


def test_voices_and_doctor(gui):
    app, call, _ = gui
    code, v = call("GET", "voices")
    assert code == 200 and v["speakers"][0]["name"] == "テスト"
    code, d = call("GET", "doctor")
    assert code == 200 and any(c["what"] == "作業フォルダ" for c in d["checks"])


def test_api_rejects_unsafe_ids(gui):
    app, call, base = gui
    for path in ("script?id=..%2F..%2Fx", "audio?id=demo&line=..%2F..%2Fx", "audio?id=..%2Fdemo&line=L1",
                 "runs/summary?run_id=..%2F..%2Fx"):
        st, r = call("GET", path)
        assert st == 400 and "英数字" in r["error"], (path, st, r)
    s = call("GET", "script?id=demo")[1]["script"]
    st, r = call("PUT", "script?id=..%2Fx", {"script": s})
    assert st == 400
