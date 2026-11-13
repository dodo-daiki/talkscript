"""擬似ロボット（テストと空運転用）。呼ばれた操作を記録し、ブロックの長さだけ待って終わる。"""
from __future__ import annotations

import time

from .base import RobotAdapter, sleep_until


class FakeAdapter(RobotAdapter):
    def __init__(self, cfg, settings, on_event):
        super().__init__(cfg, settings, on_event)
        self.calls = []
        self.blocks = {}
        self.t0 = {}

    def connect(self):
        self.calls.append(("connect",))
        return {"fake": True}

    def engage(self):
        self.calls.append(("engage",))

    def load(self, run_id, block):
        self.calls.append(("load", block["id"]))
        self.blocks[block["id"]] = block

    def goto_rest(self, duration_s):
        self.calls.append(("goto_rest", duration_s))
        self.event("at_rest")

    def start_at(self, block_id, t0_ns):
        self.calls.append(("start_at", block_id, t0_ns))
        self.t0[block_id] = t0_ns
        self.event("scheduled", block=block_id, t0_pc_ns=t0_ns)

    def wait_finished(self, block_id, deadline_ns):
        end = self.t0[block_id] + int(self.blocks[block_id]["duration_s"] * 1e9)
        sleep_until(min(end, deadline_ns))
        self.event("finished", block=block_id)
        return time.perf_counter_ns() <= deadline_ns + int(1e6)

    def stop(self, mode="graceful"):
        self.calls.append(("stop", mode))

    def close(self):
        self.calls.append(("close",))
