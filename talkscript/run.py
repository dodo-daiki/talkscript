"""実行の指揮（talkscript-design.md §8.3）。

ブロックごとに: 全ロボットへ load（自作機は Pi でも検査）→ 休止姿勢へ（並行）→ t0 = 今 + lead →
自作機は t0 に開始（Pi の時計に直して送る）、Sota は t0 − 遅れ に play_motion、音声は t0 + 行の開始時刻 に出す →
終わりを待つ。Ctrl+C の 1 回目で全ロボットを graceful（休止姿勢へ戻す）・音声を止める、2 回目で now（トルクを切る）。
"""
from __future__ import annotations

import threading
import time

from . import build, io
from .check import compile_and_check, load_robot_cfgs
from .record import RunRecord
from .robots import AdapterError, make_adapter

MODES = {
    "speech_motion": {"audio": True, "motion": True, "engage": True},
    "motion": {"audio": False, "motion": True, "engage": True},
    "speech_hold": {"audio": True, "motion": False, "engage": True},     # 発話だけ。自作機は休止姿勢を保持（トルクあり）
    "speech_relax": {"audio": True, "motion": False, "engage": False},   # 発話だけ。ロボットにつながない（自作機はトルクなし）
    "dry": {"audio": False, "motion": False, "engage": False},           # 空運転: 検査と時間の流れだけ
}

DEFAULTS = {"lead_s": 2.0, "rest_s": 3.0, "margin_s": 1.0, "sota_dt_ms": 100}


class PlayError(Exception):
    pass


def _sha_or_none(p):
    return io.sha256_file(p) if p.exists() else None


def _parallel(fns):
    errs = []

    def wrap(f):
        try:
            f()
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ths = [threading.Thread(target=wrap, args=(f,), daemon=True) for f in fns]
    for t in ths:
        t.start()
    for t in ths:
        while t.is_alive():
            t.join(0.1)                        # Ctrl+C を受けられるように細かく待つ
    if errs:
        raise errs[0]


def play(script_name: str, *, mode: str = "speech_motion", robots: list[str] | None = None, settings: dict | None = None,
         notes: dict | None = None, adapters: dict | None = None, audio=None, operator_input=input, log=print,
         runs_base=None) -> RunRecord:
    if mode not in MODES:
        raise PlayError(f"モードは {list(MODES)}")
    m = MODES[mode]
    settings = settings or {}
    opt = {**DEFAULTS, **settings.get("play", {})}
    script, spath = io.load_script(script_name)
    trajs, issues = compile_and_check(script, write=True, sota_dt_ms=int(opt["sota_dt_ms"]))
    errs = [i for i in issues if i["level"] == "error"]
    for i in issues:
        if i["level"] != "info":
            log(f"  {i}")
    if errs:
        raise PlayError(f"検査で {len(errs)} 件のエラー。実行しない（talkscript compile で詳しく見る）")
    robots = robots or list(script["robots"])
    extra = set(robots) - set(script["robots"])
    if extra:
        raise PlayError(f"台本にないロボット: {extra}")
    man = build.load_manifest(script["id"])
    cfgs = load_robot_cfgs(script)

    rec = RunRecord(script["id"], base=runs_base)
    rec.write_run(mode=mode, robots=robots, notes=notes or {}, script_sha256=io.sha256_file(spath),
                  manifest_sha256=_sha_or_none(io.BUILD / script["id"] / "manifest.json"),
                  traj_sha256={r: io.sha256_file(io.BUILD / script["id"] / f"{r}.traj.json") for r in robots},
                  engine_version=man.get("engine_version"), speakers=man.get("speakers"), frozen=man.get("frozen"),
                  options=opt, result="running")
    log(f"実行 {rec.run_id}（{mode}、{robots}）→ {rec.dir}")

    if adapters is None:
        adapters = {}
        if m["engage"] or m["motion"]:
            for r in robots:
                adapters[r] = make_adapter(r, cfgs[r], settings, rec.event)
    if m["audio"] and audio is None:
        from .audio import AudioOut
        srs = {e["sampling_rate"] for e in man["lines"].values()}
        if len(srs) != 1:
            raise PlayError(f"行によってサンプリング周波数が違う: {srs}")
        audio = AudioOut(srs.pop(), device=settings.get("audio", {}).get("device"), on_event=rec.event)
    sota_lat = getattr(adapters.get("sota"), "latency_s", None)
    rec.write_run(sota_latency_s=sota_lat)

    stopping = {"n": 0}
    result = "ok"
    try:
        info = {}
        for r, a in adapters.items():
            info[r] = a.connect()
            log(f"  {r}: つないだ")
        if audio is not None:
            info["audio"] = audio.open()
            log(f"  音声: {info['audio']['device']}（{info['audio']['hostapi']}）")
        rec.write_run(connect=info)
        if m["engage"]:
            for r, a in adapters.items():
                a.engage()
        blocks = {r: {b["id"]: b for b in trajs[r]["blocks"]} for r in script["robots"]}
        any_robot = script["robots"][0]
        for block in script["blocks"]:
            bid = block["id"]
            bt = blocks[any_robot][bid]
            if block.get("start", "auto") == "operator":
                operator_input(f"[{bid}] Enter で開始（Ctrl+C で中止）: ")
            if m["motion"]:
                for r, a in adapters.items():
                    a.load(rec.run_id, blocks[r][bid])
            if m["engage"]:
                _parallel([lambda a=a: a.goto_rest(float(opt["rest_s"])) for a in adapters.values()])
            lead = max(float(opt["lead_s"]), max([a.lead_s() for a in adapters.values()] or [0]) + 0.5)
            t0 = time.perf_counter_ns() + int(lead * 1e9)
            rec.event({"kind": "block_t0", "block": bid, "t0_pc_ns": t0, "duration_s": bt["duration_s"]})
            log(f"[{bid}] {lead:.1f} 秒後に開始（{bt['duration_s']:.2f} 秒）")
            if m["motion"]:
                for r, a in adapters.items():
                    a.start_at(bid, t0)
            if audio is not None:
                for au in bt["audio"]:
                    wav = (io.BUILD / script["id"] / f"{au['line']}.wav").read_bytes()
                    audio.schedule(au["line"], wav, t0 + int(au["start_s"] * 1e9))
            deadline = t0 + int((bt["duration_s"] + float(opt["margin_s"])) * 1e9)
            if m["motion"]:
                for r, a in adapters.items():
                    if not a.wait_finished(bid, deadline):
                        rec.event({"kind": "warn", "robot": r, "block": bid, "detail": "予定の時刻までに終わりの知らせが来ない"})
                        raise PlayError(f"{r}: {bid} が予定の時刻までに終わらない")
            if audio is not None:
                audio.wait_idle(deadline)
            while time.perf_counter_ns() < t0 + int(bt["duration_s"] * 1e9):
                time.sleep(0.02)
            rec.event({"kind": "block_end", "block": bid})
    except KeyboardInterrupt:
        result = "interrupted"
        log("中止: 全ロボットを休止姿勢へ戻す（もう一度 Ctrl+C ですぐ止める）")
        stopping["n"] = 1
    except (AdapterError, PlayError) as e:
        result = f"error: {e}"
        log(f"エラー: {e}。全ロボットを止める")
        stopping["n"] = 1
    finally:
        if audio is not None:
            audio.stop_all()
        if stopping["n"]:
            try:
                for a in adapters.values():
                    a.stop("graceful")
                time.sleep(2.5)
            except KeyboardInterrupt:
                for a in adapters.values():
                    a.stop("now")
        for r, a in adapters.items():
            for name, text in a.collect_logs(rec.run_id).items():
                rec.write_file(name, text)
            try:
                a.close()
            except Exception as e:  # noqa: BLE001
                rec.event({"kind": "warn", "robot": r, "detail": f"close: {e}"})
        if audio is not None:
            audio.close()
        rec.write_run(result=result, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        rec.close()
        log(rec.summarize())
    return rec
