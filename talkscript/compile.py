"""組み立て: 台本 → ロボットごとの軌道。

ブロックの始まりを 0 秒とする。行 i の音声の始まり S_i = 行 i−1 の音声の終わり + gap_ms（最初の行は lead_in_ms）。
キーフレームの時刻: t_ms ÷ 1000、または S_line + 発話の位置の時刻 + offset_ms ÷ 1000。
発話の位置は、句・モーラの始まりか終わり、文字の位置、または小数のモーラ番号（timing.mora_pos_time）。
ブロックの長さ = max(最後の音声の終わり, 最後のキーフレーム) + tail_ms。
"""
from __future__ import annotations

from . import build, interp, io, timing


class Issue(dict):
    """検査の結果 1 件。level: error / warn / info、code: K1〜K13。"""

    def __init__(self, level, code, msg, **kw):
        super().__init__(level=level, code=code, msg=msg, **kw)

    def __str__(self):
        where = " ".join(f"{k}={self[k]}" for k in ("robot", "block", "joint", "t") if k in self)
        return f"[{self['level']}] {self['code']} {where} {self['msg']}".replace("  ", " ")


def block_timeline(script: dict, block: dict) -> dict:
    """行ごとの開始時刻・長さ・marks。合成していない行は BuildError。"""
    t = float(block.get("lead_in_ms", script.get("lead_in_ms", io.DEFAULT_LEAD_IN_MS))) / 1000
    lines, first = {}, True
    for ln in block.get("lines", []) or []:
        if not first:
            t += float(ln.get("gap_ms", script.get("gap_ms", io.DEFAULT_GAP_MS))) / 1000
        first = False
        marks = build.load_marks(script["id"], ln["id"])
        dur = float(marks["timing"].get("wav_duration", marks["timing"]["duration"]))
        lines[ln["id"]] = {"start": t, "duration": dur, "marks": marks}
        t += dur
    return {"lines": lines, "audio_end": t if lines else 0.0}


def resolve_kf(kf: dict, tl: dict) -> float:
    if "t_ms" in kf:
        return float(kf["t_ms"]) / 1000
    at = kf["at"]
    ln = tl["lines"].get(at["line"])
    if ln is None:
        raise KeyError(f"行 {at['line']} がこのブロックにない")
    return ln["start"] + timing.anchor_time(ln["marks"], at) + float(kf.get("offset_ms", 0)) / 1000


def resolve_block(script: dict, block: dict, robot_cfgs: dict, issues: list) -> dict | None:
    """ブロックの時間軸とロボットごとの解決済みの軌道。解決できなければ issues にエラーを足して None。"""
    bid = block["id"]
    try:
        tl = block_timeline(script, block)
    except build.BuildError as e:
        issues.append(Issue("error", "K3", str(e), block=bid))
        return None
    tracks_out, last = {}, tl["audio_end"]
    for robot in script["robots"]:
        cfg = robot_cfgs[robot]
        written = (block.get("tracks") or {}).get(robot) or {}
        tracks_out[robot] = {}
        for j in written:
            if j not in cfg["joints"]:
                issues.append(Issue("error", "K1", f"関節 {j} が {robot} の設定にない（{list(cfg['joints'])}）",
                                    robot=robot, block=bid))
        for j in cfg["joints"]:
            kfs = written.get(j) or []
            pts, order_t = [], []
            for k, kf in enumerate(kfs):
                try:
                    t = resolve_kf(kf, tl)
                except KeyError as e:
                    issues.append(Issue("error", "K4", f"[{k}] 位置が決まらない: {e.args[0]}", robot=robot, block=bid, joint=j))
                    continue
                order_t.append(t)
                pts.append([round(t, 6), float(kf["deg"])])
            if any(b < a for a, b in zip(order_t, order_t[1:])):
                issues.append(Issue("warn", "K11", "キーフレームの時刻の順番が、書いた順と入れ替わっている"
                                    "（文・話速を変えたとき、固定したものと時刻で置いたものの間で起きる）", robot=robot, block=bid, joint=j))
            pts.sort(key=lambda p: p[0])
            for a, b in zip(pts, pts[1:]):
                if abs(a[0] - b[0]) < 1e-9 and a[1] != b[1]:
                    issues.append(Issue("error", "K5", f"同じ時刻 {a[0]:.3f} s に違う角度 {a[1]}° と {b[1]}°", robot=robot,
                                        block=bid, joint=j, t=round(a[0], 3)))
            dedup = []
            for p in pts:
                if dedup and abs(dedup[-1][0] - p[0]) < 1e-9:
                    continue
                dedup.append(p)
            for p in dedup:
                if p[0] < 0:
                    issues.append(Issue("error", "K4", f"時刻が負（{p[0]:.3f} s）。lead_in_ms を大きくする", robot=robot,
                                        block=bid, joint=j))
            if dedup:
                last = max(last, dedup[-1][0])
            tracks_out[robot][j] = dedup or [[0.0, float(cfg["rest"][j])]]
    duration = last + float(block.get("tail_ms", 0)) / 1000
    if duration <= 0:
        issues.append(Issue("error", "K1", "ブロックに発話も動きもない", block=bid))
    return {"timeline": tl, "tracks": tracks_out, "duration": duration}


def sota_poses(tracks: dict, cfg: dict, duration: float, dt_ms: int) -> list[dict]:
    """関節ごとの曲線を dt_ms ごとに標本化し、RobotController の姿勢の列にする（ネイティブの角度、整数）。"""
    dt = dt_ms / 1000
    samples = {j: interp.sample_track(tr, dt, duration) for j, tr in tracks.items()}
    n = len(next(iter(samples.values())))
    poses = []
    for k in range(n):
        sm = {}
        for j, s in samples.items():
            jc = cfg["joints"][j]
            sm[j] = int(round(jc["sign"] * s[k][1] + jc["offset"]))
        poses.append({"Msec": dt_ms, "ServoMap": sm})
    return poses


def sota_native(cfg: dict, body: dict) -> dict:
    return {j: int(round(cfg["joints"][j]["sign"] * d + cfg["joints"][j]["offset"])) for j, d in body.items()}


def compile_script(script: dict, robot_cfgs: dict, issues: list, *, sota_dt_ms: int = 100) -> dict:
    """ロボット → 軌道ファイル（<ロボット>.traj.json）の中身。"""
    man = build.load_manifest(script["id"])
    trajs = {r: {"format": "talkscript/traj@1", "robot": r, "script": script["id"], "interp": interp.INTERP,
                 "unit": "deg_body", "blocks": []} for r in script["robots"]}
    for block in script["blocks"]:
        res = resolve_block(script, block, robot_cfgs, issues)
        if res is None:
            continue
        audio = [{"line": lid, "start_s": round(v["start"], 6), "duration_s": v["duration"],
                  "wav_sha256": man["lines"].get(lid, {}).get("wav_sha256")} for lid, v in res["timeline"]["lines"].items()]
        groups = [{"id": k, **v} for k, v in (block.get("groups") or {}).items()]
        for r in script["robots"]:
            b = {"id": block["id"], "start": block.get("start", "auto"), "duration_s": round(res["duration"], 6),
                 "tracks": res["tracks"][r], "groups": groups, "audio": audio}
            cfg = robot_cfgs[r]
            if cfg.get("adapter") == "sota":
                if any(io.is_tbd(jc.get("sign")) or io.is_tbd(jc.get("offset")) for jc in cfg["joints"].values()):
                    b["poses"] = None
                else:
                    b["pose_dt_ms"] = sota_dt_ms
                    b["poses"] = sota_poses(res["tracks"][r], cfg, res["duration"], sota_dt_ms)
                    b["rest_native"] = sota_native(cfg, cfg["rest"])
            trajs[r]["blocks"].append(b)
    return trajs
