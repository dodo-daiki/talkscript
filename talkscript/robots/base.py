"""ロボットのアダプタの共通の型。

時刻はすべて PC の単調時計 time.perf_counter_ns() の値（ns）で渡す。壁時計は記録のためだけに使う。
"""
from __future__ import annotations

import time


class AdapterError(Exception):
    pass


class RobotAdapter:
    name = "base"

    def __init__(self, cfg: dict, settings: dict, on_event):
        self.cfg, self.settings, self.on_event = cfg, settings, on_event
        self.name = cfg["id"]

    def event(self, kind: str, **kw) -> None:
        self.on_event({"robot": self.name, "kind": kind, "t_pc_ns": kw.pop("t_pc_ns", time.perf_counter_ns()), **kw})

    # 実行の前後
    def connect(self) -> dict:
        """つないで、版・上限などの情報を返す。"""
        raise NotImplementedError

    def engage(self) -> None:
        """動かせる状態にする（自作機はトルクを入れて休止姿勢を保持）。"""

    def close(self) -> None:
        pass

    # ブロックごと
    def load(self, run_id: str, block: dict) -> None:
        """ブロックの軌道を渡す。拒否されたら AdapterError。"""
        raise NotImplementedError

    def goto_rest(self, duration_s: float) -> None:
        """休止姿勢へゆっくり移り、着くまで待つ。"""
        raise NotImplementedError

    def lead_s(self) -> float:
        """開始時刻の何秒前までに start_at を呼ぶ必要があるか。"""
        return 0.0

    def start_at(self, block_id: str, t0_ns: int) -> None:
        """PC の単調時計の t0_ns に動き始めるよう予約する（すぐ戻る）。"""
        raise NotImplementedError

    def wait_finished(self, block_id: str, deadline_ns: int) -> bool:
        """ブロックの終わりを待つ。deadline を過ぎたら False。"""
        raise NotImplementedError

    def stop(self, mode: str = "graceful") -> None:
        raise NotImplementedError

    def status(self) -> dict:
        return {}

    def collect_logs(self, run_id: str) -> dict:
        """実行のあとに回収する記録（ファイル名 → 中身）。"""
        return {}


def sleep_until(t_ns: int, stop_event=None) -> bool:
    """PC の単調時計で t_ns まで待つ（最後の 2 ms は空回り）。stop_event が立てば False。"""
    while True:
        rem = (t_ns - time.perf_counter_ns()) / 1e9
        if stop_event is not None and stop_event.is_set():
            return False
        if rem <= 0:
            return True
        if rem > 0.004:
            time.sleep(min(rem - 0.002, 0.05))
