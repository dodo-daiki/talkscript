"""動作のひな形の挿入。

挿入すると台本の tracks に写しが入る（以後は普通のキーフレーム）。挿入した範囲 [最初の点, 最後の点] にある
同じ関節のキーフレームは置き換える（D9）。挿入したまとまりには group が付く。
"""
from __future__ import annotations

import copy
import datetime

from . import io
from .compile import block_timeline, resolve_kf


class InsertError(Exception):
    pass


def _resolvable(kf, tl) -> bool:
    try:
        resolve_kf(kf, tl)
        return True
    except KeyError:
        return False


def parse_at(s: str) -> dict:
    """"L1:phrase=2:end" / "L1:text=紹介します:end" / "L1:mora=5:start" → at の辞書。"""
    parts = s.split(":")
    if len(parts) not in (2, 3) or "=" not in parts[1]:
        raise InsertError(f"--at の形は 行:phrase=番号[:start|end]、行:mora=番号、行:text=文字（{s}）")
    k, v = parts[1].split("=", 1)
    if k not in ("phrase", "mora", "text"):
        raise InsertError(f"--at の位置は phrase・mora・text（{k}）")
    at = {"line": parts[0], k: int(v) if k != "text" else v, "edge": parts[2] if len(parts) == 3 else "start"}
    return at


def insert(script: dict, block_id: str, gesture: str, *, at: dict | None = None, t_ms: float | None = None,
           amplitude: float | None = None, speed: float | None = None, robot_cfgs: dict) -> dict:
    """script を書き換え、報告（入れた数・置き換えた数・入れなかったロボット）を返す。"""
    if (at is None) == (t_ms is None):
        raise InsertError("挿入位置は at か t_ms のどちらか 1 つ")
    g = io.load_gesture(gesture)
    params = g.get("params", {})

    def param(name, val):
        spec = params.get(name, {"default": 1.0, "min": 1.0, "max": 1.0})
        v = spec["default"] if val is None else val
        if not spec["min"] <= v <= spec["max"]:
            raise InsertError(f"{name} は {spec['min']}〜{spec['max']}（{v}）")
        return v

    amp, spd = param("amplitude", amplitude), param("speed", speed)
    blocks = {b["id"]: b for b in script["blocks"]}
    if block_id not in blocks:
        raise InsertError(f"ブロック {block_id} がない")
    block = blocks[block_id]
    from .build import BuildError
    try:
        tl = block_timeline(script, block)
    except BuildError as e:
        if at:
            raise InsertError(f"発話の位置に挿入するには、先に合成する（{e}）") from e
        tl = {"lines": {}, "audio_end": 0.0}
    base_kf = {"at": at} if at else {"t_ms": t_ms}
    try:
        base_t = resolve_kf({**base_kf, "deg": 0}, tl)
    except KeyError as e:
        raise InsertError(f"挿入位置が決まらない: {e.args[0]}") from e
    groups = block.setdefault("groups", {}) or {}
    block["groups"] = groups
    n = 1
    while f"{gesture}-{n}" in groups:
        n += 1
    gid = f"{gesture}-{n}"
    report = {"group": gid, "inserted": 0, "replaced": 0, "skipped_robots": {}}
    anchor = float(g.get("anchor_at_ms", 0))
    tracks = block.setdefault("tracks", {}) or {}
    block["tracks"] = tracks
    for r in script["robots"]:
        spec = (g.get("robots") or {}).get(r)
        if not spec:
            report["skipped_robots"][r] = "このロボットの定義がない"
            continue
        if any(io.is_tbd(d) for pts in spec.values() for _, d in pts):
            report["skipped_robots"][r] = "角度に TBD がある"
            continue
        cfg = robot_cfgs[r]
        rt = tracks.setdefault(r, {}) or {}
        tracks[r] = rt
        for j, pts in spec.items():
            if j not in cfg["joints"]:
                raise InsertError(f"ひな形 {gesture} の関節 {j} が {r} の設定にない")
            rest = float(cfg["rest"][j])
            new = []
            for ms, d in pts:
                off = (float(ms) - anchor) / spd
                kf = copy.deepcopy(base_kf)
                if at:
                    if round(off):
                        kf["offset_ms"] = round(off)
                else:
                    kf["t_ms"] = round(t_ms + off)
                    if kf["t_ms"] < 0:
                        raise InsertError(f"挿入するとキーフレームの時刻が負になる（{kf['t_ms']} ms）")
                kf["deg"] = round(rest + float(d) * amp, 2)
                kf["group"] = gid
                new.append(kf)
            lo = base_t + (float(pts[0][0]) - anchor) / spd / 1000
            hi = base_t + (float(pts[-1][0]) - anchor) / spd / 1000
            keep = []
            for kf in rt.get(j) or []:
                try:
                    t = resolve_kf(kf, tl)
                except KeyError:
                    keep.append(kf)
                    continue
                if lo - 1e-6 <= t <= hi + 1e-6:
                    report["replaced"] += 1
                else:
                    keep.append(kf)
            merged = keep + new
            if all(_resolvable(kf, tl) for kf in merged):
                merged.sort(key=lambda kf: resolve_kf(kf, tl))
            rt[j] = merged
            report["inserted"] += len(new)
    groups[gid] = {"gesture": gesture, "amplitude": amp, "speed": spd,
                   "inserted": datetime.datetime.now().isoformat(timespec="seconds")}
    return report
