"""補間・時刻の計算・合成から検査までの単体と通し（擬似 VOICEVOX）。"""
from __future__ import annotations

import copy

import pytest

from talkscript import build, gestures, interp, io, timing
from talkscript.check import compile_and_check, load_robot_cfgs


# --- interp -----------------------------------------------------------------------------------

def test_smooth_peaks_match_numeric():
    tr = [[0.0, 0.0], [0.8, 40.0], [1.5, 40.0], [2.0, -10.0]]
    dt = 1e-4
    vs = [(interp.eval_track(tr, t + dt) - interp.eval_track(tr, t - dt)) / (2 * dt) for t in
          [k * 1e-3 for k in range(1, 2000)]]
    pv, pa = interp.track_peaks(tr)
    assert max(abs(v) for v in vs) == pytest.approx(pv, rel=1e-3)
    assert pv == pytest.approx(1.5 * 50 / 0.5)
    assert pa == pytest.approx(6 * 50 / 0.25)
    assert interp.eval_track_vel(tr, 0.4) == pytest.approx(vs[399], rel=1e-3)


def test_eval_holds_ends():
    tr = [[1.0, 5.0], [2.0, 7.0]]
    assert interp.eval_track(tr, 0.0) == 5.0
    assert interp.eval_track(tr, 3.0) == 7.0
    assert interp.eval_track(tr, 1.5) == pytest.approx(6.0)


def test_limit_violations():
    tr = [[0.0, 0.0], [0.1, 30.0]]
    kinds = {v["kind"] for v in interp.limit_violations(tr, lo=-10, hi=20, vmax=100, amax=1000)}
    assert kinds == {"range", "velocity", "acceleration"}


# --- timing -----------------------------------------------------------------------------------

def test_timing_models_and_anchor(vv):
    q = vv.audio_query("あいうえおか。きく", 3)
    q["speedScale"] = 1.25
    wav = vv.synthesis(q, 3)
    info = build.wav_info(wav)
    tl = timing.fit_timeline(q, info["duration_s"])
    assert tl["model"] == "frames_scaled_prepost"
    assert abs(tl["diff_s"]) < 0.002
    assert len(tl["moras"]) == 8
    # 句 0 = あいうえ、句 1 = おか（後に間）、句 2 = きく
    assert [p["kana"] for p in tl["phrases"]] == ["あいうえ", "おか", "きく"]
    marks = {"timing": tl}
    gap = timing.anchor_time(marks, {"phrase": 2, "edge": "start"}) - timing.anchor_time(marks, {"phrase": 1, "edge": "end"})
    assert gap == pytest.approx(round(0.3 / 1.25 * timing.FRAME_RATE) / timing.FRAME_RATE)


def test_timing_mismatch_raises(vv):
    q = vv.audio_query("あいう", 3)
    with pytest.raises(timing.TimingMismatch):
        timing.fit_timeline(q, 5.0)


# --- 合成 → 固定 → 組み立て → 検査 ----------------------------------------------------------------

def test_pipeline(tmp_repo, vv):
    s, _ = io.load_script("demo")
    man = build.synth(s, vv, log=lambda *_: None)
    assert set(man["lines"]) == {"L1", "L2"}
    m1 = build.load_marks("demo", "L1")
    ta = m1["text_anchors"]["紹介します#1"]
    assert ta["kana"] == "紹介します"            # 擬似エンジンは文字 = モーラ
    trajs, issues = compile_and_check(s)
    errs = [i for i in issues if i["level"] == "error"]
    assert not errs, errs
    assert any(i["code"] == "K2" and i["level"] == "warn" for i in issues)       # 未固定はデモなら警告
    b1 = trajs["self7"]["blocks"][0]
    np_ = b1["tracks"]["neck_pitch"]
    l1 = b1["audio"][0]
    end_kana = l1["start_s"] + m1["timing"]["moras"][ta["mora_end"]]["end"]
    assert np_[1] == [pytest.approx(end_kana, abs=1e-6), 6.0]
    # 2 回目の合成は飛ばす
    n = len(vv.queries)
    build.synth(s, vv, log=lambda *_: None)
    assert len(vv.queries) == n
    # 固定したあと文を変えると K3
    build.freeze(s)
    s2 = copy.deepcopy(s)
    s2["blocks"][0]["lines"][1]["say"] = "こちらをご覧ください。"
    _, issues = compile_and_check(s2, write=False)
    assert any(i["code"] == "K3" and i["level"] == "error" for i in issues)
    with pytest.raises(build.BuildError):
        build.synth(s2, vv, log=lambda *_: None)


def test_experiment_rules(tmp_repo, vv):
    s, _ = io.load_script("demo")
    s["purpose"] = "experiment"
    s["blocks"][0]["lines"][1]["voice"] = {"speed": 0.9}
    build.synth(s, vv, log=lambda *_: None)
    _, issues = compile_and_check(s, write=False)
    codes = {(i["code"], i["level"]) for i in issues}
    assert ("K12", "error") in codes and ("K2", "error") in codes


def test_limits_and_rest(tmp_repo, vv):
    s, _ = io.load_script("demo")
    build.synth(s, vv, log=lambda *_: None)
    s["blocks"][1]["tracks"]["self7"]["waist_yaw"] = [{"t_ms": 0, "deg": 0}, {"t_ms": 100, "deg": 30}]
    _, issues = compile_and_check(s, write=False)
    k = {(i["code"], i.get("joint")) for i in issues if i["level"] == "error"}
    assert ("K7", "waist_yaw") in k and ("K9", "waist_yaw") in k


def test_text_anchor_failure_reported(tmp_repo, vv):
    s, _ = io.load_script("demo")
    s["blocks"][0]["tracks"]["self7"]["neck_pitch"][0]["at"]["text"] = "ありません"
    build.synth(s, vv, log=lambda *_: None)
    _, issues = compile_and_check(s, write=False)
    assert any(i["code"] == "K4" for i in issues)


def test_order_swap_warning(tmp_repo, vv):
    s, _ = io.load_script("demo")
    build.synth(s, vv, log=lambda *_: None)
    tr = s["blocks"][0]["tracks"]["self7"]["neck_pitch"]
    tr.insert(1, {"t_ms": 100, "deg": 0})              # 書いた順では固定したものの後、時刻では前
    _, issues = compile_and_check(s, write=False)
    assert any(i["code"] == "K11" for i in issues)


def test_gesture_insert_replaces(tmp_repo, vv):
    s, _ = io.load_script("demo")
    build.synth(s, vv, log=lambda *_: None)
    cfgs = load_robot_cfgs(s)
    rep = gestures.insert(s, "B1", "nod", at=gestures.parse_at("L1:text=紹介します:end"), amplitude=1.5, robot_cfgs=cfgs)
    assert rep["inserted"] == 4 and rep["replaced"] == 4
    np_ = s["blocks"][0]["tracks"]["self7"]["neck_pitch"]
    assert [kf["deg"] for kf in np_] == [0, 9.0, 0, 0]
    assert all(kf["group"] == rep["group"] for kf in np_)
    # 時刻で入れる
    rep = gestures.insert(s, "B2", "nod", t_ms=1000, robot_cfgs=cfgs)
    assert [kf["t_ms"] for kf in s["blocks"][1]["tracks"]["self7"]["neck_pitch"]] == [700, 1000, 1350, 1800]
    _, issues = compile_and_check(s, write=False)
    assert not [i for i in issues if i["level"] == "error"]


def test_fractional_mora_follows_speed(tmp_repo, vv):
    """小数のモーラ位置に置いたキーフレームは、話速を変えて合成し直しても、発話の中の同じ場所に付いていく。"""
    s, _ = io.load_script("demo")
    s["blocks"][0]["tracks"]["self7"]["neck_yaw"] = [
        {"t_ms": 0, "deg": 0},
        {"at": {"line": "L1", "mora": 3.5}, "deg": 5},
        {"at": {"line": "L2", "mora": 2.25}, "deg": 0},
    ]
    io.validate_script(s)

    def where():
        build.synth(s, vv, log=lambda *_: None)
        trajs, issues = compile_and_check(s, write=False)
        assert not [i for i in issues if i["level"] == "error"], issues
        b1 = trajs["self7"]["blocks"][0]
        starts = {a["line"]: a["start_s"] for a in b1["audio"]}
        out = []
        for (t, _), (lid, u) in zip(b1["tracks"]["neck_yaw"][1:], [("L1", 3.5), ("L2", 2.25)]):
            ms = build.load_marks("demo", lid)["timing"]["moras"]
            i, f = int(u), u - int(u)
            assert t == pytest.approx(starts[lid] + ms[i]["start"] + f * (ms[i + 1]["start"] - ms[i]["start"]), abs=1e-6)
            out.append(t)
        return out

    slow = where()
    s["voice"]["speed"] = 1.5
    fast = where()
    assert fast[0] < slow[0] and fast[1] < slow[1]        # 発話が速くなった分だけ前へ


def test_fractional_mora_rules():
    ms = [{"start": 0.1, "end": 0.25}, {"start": 0.25, "end": 0.4}, {"start": 0.7, "end": 0.85}]
    assert timing.mora_pos_time(ms, 1.5) == pytest.approx(0.475)       # 間を含む枠（0.25〜0.7）の半分
    assert timing.mora_pos_time(ms, 3.0) == pytest.approx(0.85)        # 最後のモーラの終わり
    assert timing.anchor_time({"timing": {"moras": ms}}, {"mora": 2.0}) == pytest.approx(0.7)
    with pytest.raises(KeyError):
        timing.mora_pos_time(ms, 3.5)
    bad = {"format": "talkscript/script@1", "id": "x", "voice": {}, "robots": ["self7"],
           "blocks": [{"id": "B1", "lines": [{"id": "L1", "say": "あ"}],
                       "tracks": {"self7": {"neck_yaw": [{"at": {"line": "L1", "mora": 0.5, "edge": "end"}, "deg": 0}]}}}]}
    with pytest.raises(io.FormatError, match="edge を書かない"):
        io.validate_script(bad)
