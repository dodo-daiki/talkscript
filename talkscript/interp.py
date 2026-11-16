"""補間と上限の計算。

PC（組み立て・検査）と Pi（talkscript-agent の受け取り時の検査と再生）で同じものを使う。
Pi でも動くように、標準ライブラリだけで書く（Python 3.10 以上）。

軌道は関節ごとの点の列 [[秒, 度], ...]（時刻の昇順）。補間 `smooth` は、隣り合う点の間を
θ(s) = θ0 + Δθ(3s² − 2s³)、s = (t − t0) / T でつなぐ（各点で速度 0）。
区間の最大の角速度は 1.5|Δθ|/T、最大の角加速度は 6|Δθ|/T²（s = 1/2 と s = 0, 1 で生じる）。
"""
from __future__ import annotations

import bisect
import math

INTERP = "smooth"


def _seg(track, t):
    """t を含む区間の添字 k（track[k] ≤ t < track[k+1]）。範囲外は -1 か len-1。"""
    times = [p[0] for p in track]
    return bisect.bisect_right(times, t) - 1


def eval_track(track, t: float) -> float:
    """時刻 t の角度。最初の点より前は最初の角度、最後の点より後は最後の角度を保つ。"""
    if not track:
        raise ValueError("空の軌道")
    if t <= track[0][0]:
        return float(track[0][1])
    if t >= track[-1][0]:
        return float(track[-1][1])
    k = _seg(track, t)
    t0, a = track[k]
    t1, b = track[k + 1]
    T = t1 - t0
    if T <= 0:
        return float(b)
    s = (t - t0) / T
    return a + (b - a) * (3 * s * s - 2 * s * s * s)


def eval_track_vel(track, t: float) -> float:
    """時刻 t の角速度 [度/秒]。"""
    if not track or t <= track[0][0] or t >= track[-1][0]:
        return 0.0
    k = _seg(track, t)
    t0, a = track[k]
    t1, b = track[k + 1]
    T = t1 - t0
    if T <= 0:
        return 0.0
    s = (t - t0) / T
    return (b - a) * (6 * s - 6 * s * s) / T


def eval_tracks(tracks: dict, t: float) -> dict:
    return {j: eval_track(tr, t) for j, tr in tracks.items()}


def segment_peaks(track):
    """区間ごとの (t0, t1, 角度0, 角度1, 最大角速度 [度/s], 最大角加速度 [度/s²])。T = 0 で角度が違えば inf。"""
    out = []
    for (t0, a), (t1, b) in zip(track, track[1:]):
        d = abs(b - a)
        T = t1 - t0
        if d == 0:
            v = acc = 0.0
        elif T <= 0:
            v = acc = math.inf
        else:
            v, acc = 1.5 * d / T, 6.0 * d / (T * T)
        out.append((t0, t1, a, b, v, acc))
    return out


def track_peaks(track) -> tuple[float, float]:
    segs = segment_peaks(track)
    if not segs:
        return 0.0, 0.0
    return max(s[4] for s in segs), max(s[5] for s in segs)


def limit_violations(track, *, lo=None, hi=None, vmax=None, amax=None):
    """上限を超えた箇所の一覧（文字列でなく dict）。smooth は区間の中で両端の角度を超えないので、範囲は点だけ見ればよい。"""
    bad = [{"kind": "nonfinite", "t": t, "value": d, "limit": "有限な数"}
           for t, d in track if not (math.isfinite(t) and math.isfinite(d))]
    if bad:     # NaN との比較は常に偽で、下の検査をすり抜けるので、ここで止める
        return bad
    for t, d in track:
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            bad.append({"kind": "range", "t": t, "value": d, "limit": [lo, hi]})
    for t0, t1, a, b, v, acc in segment_peaks(track):
        if vmax is not None and v > vmax:
            bad.append({"kind": "velocity", "t": t0, "t_end": t1, "value": v, "limit": vmax})
        if amax is not None and acc > amax:
            bad.append({"kind": "acceleration", "t": t0, "t_end": t1, "value": acc, "limit": amax})
    return bad


def sample_track(track, dt: float, t_end: float, t_start: float = 0.0):
    """t_start + dt, t_start + 2dt, …, t_end 以上の最初の点までの (時刻, 角度)。Sota 向けの変換で使う。"""
    n = max(1, math.ceil((t_end - t_start) / dt - 1e-9))
    return [(t_start + k * dt, eval_track(track, t_start + k * dt)) for k in range(1, n + 1)]


def move_track(a: dict, b: dict, duration_s: float, t0: float = 0.0) -> dict:
    """姿勢 a から b へ duration_s かけて移る軌道（休止姿勢への移動などに使う）。"""
    return {j: [[t0, a.get(j, b[j])], [t0 + duration_s, b[j]]] for j in b}
