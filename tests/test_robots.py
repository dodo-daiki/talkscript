"""ロボットのアダプタ・agent・実行の指揮（擬似 RobotController・擬似 CAN ドライバ）。"""
from __future__ import annotations

import copy
import time
import types

import pytest

from talkscript import audio as audio_mod
from talkscript import build, io
from talkscript.audio import AudioOut
from talkscript.robots import AdapterError
from talkscript.robots.fake import FakeAdapter
from talkscript.robots.sota import SotaAdapter
from talkscript.run import play

from fake_robotcontroller import FakeRobotController


def _sota_cfg():
    cfg = copy.deepcopy(io.load_robot("sota"))
    for jc in cfg["joints"].values():          # テスト用に向き・範囲・上限を仮に埋める（実機の値ではない）
        jc.update(sign=1, offset=0, range=[-90, 90], vmax=300)
    return cfg


# --- Sota -------------------------------------------------------------------------------------

def test_sota_adapter_with_fake_server():
    rc = FakeRobotController().start()
    try:
        evs = []
        cfg = _sota_cfg()
        a = SotaAdapter(cfg, {"sota": {"host": "127.0.0.1", "port": rc.port, "latency_s": 0.05}}, evs.append)
        info = a.connect()
        assert set(info["axes"]) >= {"HEAD_P", "BODY_Y"}
        poses = [{"Msec": 100, "ServoMap": {j: (5 if j == "HEAD_Y" else 0) for j in cfg["joints"]}} for _ in range(3)]
        block = {"id": "B1", "duration_s": 0.3, "poses": poses}
        a.load("r1", block)
        a.goto_rest(0.1)
        t0 = time.perf_counter_ns() + int(0.3e9)
        a.start_at("B1", t0)
        assert a.wait_finished("B1", t0 + int(1e9))
        a.stop()
        cmds = [c[0] for c in rc.commands]
        assert cmds[:2] == ["read_axes", "play_pose"]
        pm = [c for c in rc.commands if c[0] == "play_motion"][0]
        assert pm[1] == poses
        sent = [e for e in evs if e["kind"] == "sent"][0]
        assert abs(sent["t_pc_ns"] - (t0 - int(0.05e9))) < 20e6          # 遅れの分だけ早く送る（±20 ms）
        assert {"stop_motion", "stop_pose"} <= set(cmds)
    finally:
        rc.stop()


def test_sota_preconnect():
    rc = FakeRobotController().start()
    try:
        cfg = _sota_cfg()
        a = SotaAdapter(cfg, {"sota": {"host": "127.0.0.1", "port": rc.port, "latency_s": 0.0, "preconnect": True}},
                        lambda e: None)
        poses = [{"Msec": 100, "ServoMap": {"HEAD_Y": 3}}]
        a.load("r", {"id": "B1", "duration_s": 0.1, "poses": poses})
        a.start_at("B1", time.perf_counter_ns() + int(0.2e9))
        a.wait_finished("B1", time.perf_counter_ns() + int(1e9))
        time.sleep(0.05)
        assert [c[1] for c in rc.commands if c[0] == "play_motion"] == [poses]
    finally:
        rc.stop()


def test_sota_refuses_tbd():
    a = SotaAdapter(io.load_robot("sota"), {}, lambda e: None)
    with pytest.raises(AdapterError):
        a.load("r", {"id": "B1", "poses": [{"Msec": 100, "ServoMap": {}}]})


# --- 実行の指揮 ---------------------------------------------------------------------------------

class _FakeAudio:
    def __init__(self):
        self.scheduled = []

    def open(self):
        return {"device": "fake", "hostapi": "fake"}

    def schedule(self, label, wav, t_ns):
        self.scheduled.append((label, t_ns))

    def wait_idle(self, d):
        return True

    def stop_all(self):
        pass

    def close(self):
        pass


def test_play_speech_motion_fake(tmp_repo, vv):
    s, _ = io.load_script("demo")
    build.synth(s, vv, log=lambda *_: None)
    fake = FakeAdapter(io.load_robot("self7"), {}, lambda e: None)
    au = _FakeAudio()
    rec = play("demo", mode="speech_motion", settings={"play": {"lead_s": 0.2, "rest_s": 0.1}},
               adapters={"self7": fake}, audio=au, operator_input=lambda _: "", log=lambda *_: None)
    assert io.read_json(rec.dir / "run.json")["result"] == "ok"
    t0 = [c for c in fake.calls if c[0] == "start_at"][0][2]
    assert au.scheduled[0] == ("L1", t0 + int(0.5e9))             # lead_in 500 ms
    assert [c[0] for c in fake.calls].count("load") == 2


def test_play_refuses_on_check_error(tmp_repo, vv):
    from talkscript.run import PlayError
    s, _ = io.load_script("demo")
    build.synth(s, vv, log=lambda *_: None)
    s["blocks"][1]["tracks"]["self7"]["waist_yaw"][1]["t_ms"] = 100     # 速すぎる
    io.write_yaml(io.SCRIPTS / "demo.yaml", s)
    with pytest.raises(PlayError):
        play("demo", mode="motion", adapters={"self7": FakeAdapter(io.load_robot("self7"), {}, lambda e: None)},
             log=lambda *_: None)


# --- 音声の出力（コールバックの計算だけ。機器は開かない） ---------------------------------------------

def test_audio_callback_places_clip(monkeypatch):
    evs = []
    out = AudioOut(1000, on_event=evs.append)
    out.stream = types.SimpleNamespace(latency=0.0)
    now0 = time.perf_counter_ns()
    pcm = b"\x01\x00" * 30
    out.clips.append(audio_mod._Clip("L1", pcm, now0 + int(0.025e9)))
    buf = bytearray(40)
    ti = types.SimpleNamespace()
    for k in range(3):                                            # 流れの時刻 = 単調時計として、20 標本（20 ms）ずつ 3 回
        t = now0 + int(k * 0.02e9)
        monkeypatch.setattr(audio_mod.time, "perf_counter_ns", lambda t=t: t)
        ti.currentTime = t / 1e9
        ti.outputBufferDacTime = t / 1e9
        out._cb(buf, 20, ti, None)
        if k == 1:                                                # 25 ms = 2 回目の 5 標本目から
            assert bytes(buf[:10]) == b"\x00" * 10 and bytes(buf[10:12]) == b"\x01\x00"
    st = [e for e in evs if e["kind"] == "audio_start"][0]
    assert abs(st["late_ms"]) < 1.5
    assert any(e["kind"] == "audio_done" for e in evs)


# --- 想定外の例外（ソケットの切断など） -----------------------------------------------------------

class _BrokenAtStart(FakeAdapter):
    def start_at(self, block_id, t0_ns):
        super().start_at(block_id, t0_ns)
        raise ConnectionResetError("接続が切れた（試験）")


def test_unexpected_error_records_error_and_stops_robots(tmp_repo, vv, monkeypatch):
    """AdapterError・PlayError 以外の例外でも、記録を error にし、全ロボットに停止を送ってから上げ直す。"""
    import json
    from talkscript import run as run_mod
    monkeypatch.setattr(run_mod.time, "sleep", lambda s: None)          # 停止後の待ちを省く
    s, _ = io.load_script("demo")
    build.synth(s, vv, log=lambda *_: None)
    a = _BrokenAtStart(io.load_robot("self7"), {}, lambda e: None)
    ctx = {}
    with pytest.raises(ConnectionResetError):
        play("demo", mode="motion", adapters={"self7": a}, settings={"play": {"lead_s": 0.1, "rest_s": 0.1}},
             log=lambda *_: None, ctx=ctx)
    rj = json.loads((ctx["record"].dir / "run.json").read_text(encoding="utf-8"))
    assert rj["result"].startswith("error"), rj["result"]
    assert ("stop", "graceful") in a.calls and ("close",) in a.calls


def test_agent_client_send_wraps_oserror():
    """agent への送信の失敗（OSError）は AdapterError にする（実行の後始末が受けられるように）。"""
    from talkscript.robots.self7 import AgentClient

    class DeadSock:
        def sendall(self, data):
            raise ConnectionResetError("切れた")
    c = AgentClient("127.0.0.1", 1, lambda e: None)
    c.sock = DeadSock()
    with pytest.raises(AdapterError, match="送れない"):
        c.send({"type": "ping"})


# --- 記録の書き込みは呼んだスレッドで行わない（音声のコールバックから呼ばれるため） ---------------------------

def test_record_event_writes_on_writer_thread(tmp_path):
    import json
    import threading
    from talkscript.record import RunRecord
    echoed = []
    rec = RunRecord("demo", base=tmp_path, echo=lambda t: echoed.append((threading.get_ident(), t)))
    caller = threading.get_ident()
    writes = []
    for name in ("events_fp", "audio_fp"):
        fp = getattr(rec, name)
        real = fp.write
        fp.write = lambda s, real=real: (writes.append(threading.get_ident()), real(s))[1]
    rec.event({"kind": "audio_status", "detail": "output underflow"})    # 音声のコールバックから来るもの
    rec.event({"kind": "audio_start", "line": "L1", "late_ms": 0.1, "planned_pc_ns": 0})
    rec.event({"kind": "warn", "robot": "self7", "detail": "試験"})
    rec.event({"kind": "block_t0", "block": "B1", "t0_pc_ns": 1})
    rec.close()
    assert writes and caller not in writes                               # ファイルへの書き込みは別のスレッド
    assert echoed and all(t != caller for t, _ in echoed)               # 画面・端末への表示も別のスレッド
    ev = [json.loads(x) for x in (rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    au = [json.loads(x) for x in (rec.dir / "audio.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["kind"] for e in ev] == ["warn", "block_t0"] and [a["kind"] for a in au] == ["audio_status", "audio_start"]
    assert all("wall" in e for e in ev + au)
    assert rec.counts["warn"] == 1 and rec.counts["audio_status"] == 1
