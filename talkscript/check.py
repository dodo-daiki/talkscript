"""実行前の検査。compile.py の結果に K2・K3・K7〜K10・K12 を足す。

K1 形式・参照先、K2 固定、K3 音声の照合、K4 発話の位置、K5 同じ時刻の矛盾、K7 範囲・角速度・角加速度、
K8 未確定の設定、K9 休止姿勢で始まり終わるか、K10 長さ（表示だけ）、K11 順番の入れ替わり、K12 実験の台本の声。

K13（合成し直しで固定先が見つからず時刻で置き直した）は編集画面の機能なので、CLI では出ない。
"""
from __future__ import annotations

import json

from . import build, interp, io
from .compile import Issue, compile_script


def load_robot_cfgs(script: dict) -> dict:
    return {r: io.load_robot(r) for r in script["robots"]}


def check_voice(script: dict, issues: list) -> None:
    if script.get("purpose", "demo") != "experiment":
        return
    over = [ln["id"] for _, ln in io.iter_lines(script) if ln.get("voice")]
    if over:
        issues.append(Issue("error", "K12", f"実験の台本で行ごとに声を上書きしている: {over}（実験では刺激の声を 1 つに固定する）"))
    if io.is_tbd((script.get("voice") or {}).get("speaker", {}).get("name")):
        issues.append(Issue("error", "K12", "実験の台本の話者が未決定（TBD）"))


def check_audio(script: dict, issues: list) -> dict:
    man = build.load_manifest(script["id"])
    if not any(True for _ in io.iter_lines(script)):
        return man                                     # 発話のない台本（動作だけ）は合成も固定も要らない
    st = build.line_status(script, man)
    msg = {"unsynthesized": "合成していない（talkscript synth）", "changed": "文・声・読みが合成のときと違う（talkscript synth）",
           "wav_missing": "WAV がない（talkscript synth）", "wav_changed": "WAV の sha256 が manifest と違う"}
    for lid, s in st.items():
        if s != "ok":
            issues.append(Issue("error", "K3", f"{lid}: {msg[s]}"))
    if not man.get("frozen"):
        level = "error" if script.get("purpose", "demo") == "experiment" else "warn"
        issues.append(Issue(level, "K2", "音声を固定していない（talkscript freeze）" +
                            ("。実験の台本は固定が必須" if level == "error" else "。デモとして続ける")))
    return man


def check_limits(script: dict, robot_cfgs: dict, trajs: dict, issues: list) -> dict:
    """K7・K8・K9。戻り値はロボット → 関節 → 最大の角速度・角加速度。"""
    peaks = {}
    for r, traj in trajs.items():
        cfg = robot_cfgs[r]
        peaks[r] = {}
        moved = set()
        for b in traj["blocks"]:
            for j, tr in b["tracks"].items():
                jc = cfg["joints"][j]
                rest = float(cfg["rest"][j])
                if len(tr) > 1 or abs(tr[0][1] - rest) > 1e-9:
                    moved.add(j)
                if abs(tr[0][1] - rest) > 1e-9 or abs(tr[-1][1] - rest) > 1e-9:
                    issues.append(Issue("error", "K9", f"ブロックの最初か最後が休止姿勢（{rest}°）でない"
                                        f"（最初 {tr[0][1]}°・最後 {tr[-1][1]}°）", robot=r, block=b["id"], joint=j))
                rng = jc.get("range")
                lo, hi = (None, None) if io.is_tbd(rng) else rng
                vmax = None if io.is_tbd(jc.get("vmax")) else float(jc["vmax"])
                amax = None if io.is_tbd(jc.get("amax")) else float(jc["amax"])
                for v in interp.limit_violations(tr, lo=lo, hi=hi, vmax=vmax, amax=amax):
                    kind = {"range": "範囲", "velocity": "角速度", "acceleration": "角加速度"}[v["kind"]]
                    unit = {"range": "°", "velocity": "°/s", "acceleration": "°/s²"}[v["kind"]]
                    span = f"{v['t']:.3f}〜{v['t_end']:.3f} s" if "t_end" in v else f"{v['t']:.3f} s"
                    issues.append(Issue("error", "K7", f"{kind}の上限を超える: {v['value']:.1f}{unit}（上限 {v['limit']}）{span}。"
                                        "切り詰めずに止める。キーフレームの間を広げるか角度を小さくする",
                                        robot=r, block=b["id"], joint=j, t=round(v["t"], 3)))
                pv, pa = interp.track_peaks(tr)
                cur = peaks[r].setdefault(j, {"v_deg_s": 0.0, "a_deg_s2": 0.0})
                cur["v_deg_s"] = round(max(cur["v_deg_s"], pv), 2)
                cur["a_deg_s2"] = round(max(cur["a_deg_s2"], pa), 2)
            if cfg.get("adapter") == "sota" and b.get("poses"):
                prev = b["rest_native"]
                for k, p in enumerate(b["poses"]):
                    for j, n in p["ServoMap"].items():
                        vmax = cfg["joints"][j].get("vmax")
                        if not io.is_tbd(vmax) and abs(n - prev[j]) / (p["Msec"] / 1000) > float(vmax) * 1.05:
                            issues.append(Issue("error", "K7", f"標本化・丸めのあとの姿勢 {k} で角速度の上限を超える",
                                                robot=r, block=b["id"], joint=j))
                    prev = p["ServoMap"]
        for j in sorted(moved):
            jc = cfg["joints"][j]
            tbd = [k for k in ("sign", "offset", "range", "vmax") if k in jc and io.is_tbd(jc[k])]
            if tbd:
                issues.append(Issue("error", "K8", f"設定が未確定（{', '.join(tbd)} が TBD）の関節を動かしている。"
                                    f"config/robots/{r}.yaml を実機で確かめて埋める", robot=r, joint=j))
            if jc.get("amax") is None:
                issues.append(Issue("info", "K7", "角加速度の上限がない（検査しない）", robot=r, joint=j))
        if cfg.get("adapter") == "sota":
            if any(b.get("poses") is None for b in traj["blocks"]):
                issues.append(Issue("error", "K8", "Sota の関節の向き（sign・offset）が未確定で、姿勢の列を作れない", robot=r))
            for f in cfg.get("forbidden") or []:
                issues.append(Issue("warn", "K8", f"ぶつかる組み合わせの検査は未実装: {f}", robot=r))
    return peaks


def check_info(script: dict, trajs: dict, issues: list) -> None:
    for r, traj in trajs.items():
        for b in traj["blocks"]:
            audio = sum(a["duration_s"] for a in b["audio"])
            issues.append(Issue("info", "K10", f"長さ {b['duration_s']:.2f} s、音声 {audio:.2f} s、行 {len(b['audio'])}",
                                robot=r, block=b["id"]))
            if r == "sota" and b["duration_s"] > 30:
                issues.append(Issue("warn", "K10", "Sota の 1 ブロックが 30 秒を超える（PC が落ちたときに止められない）",
                                    robot=r, block=b["id"]))


def compile_and_check(script: dict, *, write: bool = True, sota_dt_ms: int = 100) -> tuple[dict, list]:
    """組み立てと検査。write なら build/<id>/<ロボット>.traj.json に書く。戻り値: (軌道, 検査の結果)。"""
    issues: list = []
    try:
        robot_cfgs = load_robot_cfgs(script)
    except io.FormatError as e:
        return {}, [Issue("error", "K1", str(e))]
    check_voice(script, issues)
    check_audio(script, issues)
    trajs = compile_script(script, robot_cfgs, issues, sota_dt_ms=sota_dt_ms)
    peaks = check_limits(script, robot_cfgs, trajs, issues)
    check_info(script, trajs, issues)
    ok = not any(i["level"] == "error" for i in issues)
    if write:
        out = io.build_dir(script["id"])
        sp = io.script_path(script["id"]) if (io.SCRIPTS / f"{script['id']}.yaml").exists() else None
        man_p = out / "manifest.json"
        for r, traj in trajs.items():
            traj["script_sha256"] = io.sha256_file(sp) if sp else None
            traj["manifest_sha256"] = io.sha256_file(man_p) if man_p.exists() else None
            traj["check"] = {"ok": ok, "peaks": peaks.get(r, {}),
                             "issues": [dict(i) for i in issues if i.get("robot") in (None, r)]}
            (out / f"{r}.traj.json").write_text(json.dumps(traj, ensure_ascii=False, indent=1), encoding="utf-8")
    return trajs, issues
