"""時計のずれの計測。

Pi の時計を PC に合わせる（chrony など）のではなく、**ブロックの直前にずれを測って補う**。
PC の単調時計（perf_counter_ns）と Pi の単調時計（perf_counter_ns = CLOCK_MONOTONIC）の差を、
NTP と同じ 4 つの時刻のやりとりで 10 回測り、往復時間の最も短い回の値を使う。
数秒のブロックの間の時計の進み方の違い（数十 ppm 程度と思われる。推測）は無視できる。
PC で NTP サーバを用意する必要がなく、壁時計が途中で合わせ直されても影響しない。
"""
from __future__ import annotations


def offset_from_samples(samples: list[tuple[int, int, int, int]]) -> dict:
    """samples: (t1 PC 送信, t2 Pi 受信, t3 Pi 送信, t4 PC 受信)。戻り値の offset_ns は Pi − PC。"""
    rows = []
    for t1, t2, t3, t4 in samples:
        rtt = (t4 - t1) - (t3 - t2)
        off = ((t2 - t1) + (t3 - t4)) // 2
        rows.append((rtt, off))
    rows.sort()
    best_rtt, best_off = rows[0]
    offs = sorted(o for _, o in rows)
    return {"offset_ns": best_off, "rtt_ns": best_rtt, "n": len(rows),
            "offset_spread_ns": offs[-1] - offs[0], "rtt_max_ns": max(r for r, _ in rows)}
