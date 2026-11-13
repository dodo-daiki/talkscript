"""自作機のアダプタ: Pi 5 の talkscript-agentと TCP/JSON でやりとりする（1 行に 1 つの JSON。hello・time・engage・load・goto_rest・start_at・stop・disengage・fetch_log）。"""
from __future__ import annotations

import json
import socket
import threading
import time

from .. import clock, io
from .base import AdapterError, RobotAdapter

HEARTBEAT_S = 0.2


class AgentClient:
    def __init__(self, host: str, port: int, on_event, timeout_s: float = 5.0):
        self.host, self.port, self.on_event, self.timeout_s = host, port, on_event, timeout_s
        self.sock = None
        self._id = 0
        self._lock = threading.Lock()
        self._replies: dict = {}
        self._cv = threading.Condition()
        self._events: list = []
        self._closed = threading.Event()
        self._hb = None

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
        except OSError as e:
            raise AdapterError(f"talkscript-agent（{self.host}:{self.port}）につながらない: {e}。Pi で agent/server.py を起動したか") from e
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(None)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        f = self.sock.makefile("rb")
        try:
            for raw in f:
                t4 = time.perf_counter_ns()
                msg = json.loads(raw)
                with self._cv:
                    if msg.get("type") == "reply":
                        msg["_t4"] = t4
                        self._replies[msg.get("re")] = msg
                    else:
                        self._events.append(msg)
                    self._cv.notify_all()
                if msg.get("type") == "event" and msg.get("kind") != "log":
                    self.on_event(msg)
        except (OSError, ValueError):
            pass
        finally:
            self._closed.set()
            with self._cv:
                self._cv.notify_all()

    def send(self, msg: dict):
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self.sock.sendall(data)

    def request(self, type_: str, timeout_s: float | None = None, **kw) -> dict:
        with self._lock:
            self._id += 1
            mid = self._id
        msg = {"type": type_, "id": mid, **kw}
        if type_ == "time":
            msg["t1"] = time.perf_counter_ns()
        self.send(msg)
        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        with self._cv:
            while mid not in self._replies:
                if self._closed.is_set():
                    raise AdapterError("agent との接続が切れた")
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise AdapterError(f"agent が {type_} に応答しない")
                self._cv.wait(rem)
            r = self._replies.pop(mid)
        if not r.get("ok"):
            raise AdapterError(f"agent が {type_} を拒否: {r.get('error')}")
        return r

    def wait_event(self, pred, deadline_ns: int) -> dict | None:
        with self._cv:
            while True:
                for i, e in enumerate(self._events):
                    if pred(e):
                        return self._events.pop(i)
                rem = (deadline_ns - time.perf_counter_ns()) / 1e9
                if rem <= 0 or self._closed.is_set():
                    return None
                self._cv.wait(min(rem, 0.1))

    def clear_events(self):
        with self._cv:
            self._events.clear()

    def start_heartbeat(self):
        def run():
            while not self._closed.wait(HEARTBEAT_S):
                try:
                    self.send({"type": "heartbeat"})
                except OSError:
                    return
        if self._hb is None:
            self._hb = threading.Thread(target=run, daemon=True)
            self._hb.start()

    def measure_offset(self, n: int = 10) -> dict:
        samples = []
        for _ in range(n):
            r = self.request("time")
            samples.append((r["t1"], r["t2"], r["t3"], r["_t4"]))
        return clock.offset_from_samples(samples)

    def close(self):
        self._closed.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


class Self7Adapter(RobotAdapter):
    def __init__(self, cfg, settings, on_event):
        super().__init__(cfg, settings, on_event)
        conn = dict(cfg.get("connection") or {})
        conn.update(settings.get("self7", {}))
        if io.is_tbd(conn.get("host")):
            raise AdapterError("Pi のアドレスが未設定（talkscript.toml の [self7] host か config/robots/self7.yaml の connection.host）")
        self.client = AgentClient(conn["host"], int(conn.get("port", 50110)), self._agent_event)
        self.offset = None
        self.info = {}

    def _agent_event(self, ev):
        kw = {k: v for k, v in ev.items() if k not in ("type", "kind")}
        if "t_mono_ns" in kw and self.offset is not None:
            kw["t_pc_ns"] = kw["t_mono_ns"] - self.offset["offset_ns"]       # Pi の時刻 → PC の時刻
        self.event(ev["kind"], **kw)

    def connect(self) -> dict:
        self.client.connect()
        self.info = self.client.request("hello")
        self.offset = self.client.measure_offset()
        self.event("clock", **self.offset)
        self._compare_limits(self.info.get("limits", {}))
        from .. import __version__
        if self.info.get("talkscript") != __version__:
            self.event("warn", detail=f"Pi の talkscript（補間の式）の版 {self.info.get('talkscript')} が PC の {__version__} と違う。"
                                      "Pi の talkscript を更新する")
        return {"agent": {k: self.info[k] for k in ("version", "talkscript", "robot_layer", "hz") if k in self.info},
                "limits": self.info.get("limits"), "clock": self.offset}

    def _compare_limits(self, lim: dict):
        """config/robots/self7.yaml の上限が agent の上限より緩ければ知らせる（agent の受け取りの検査で拒否される）。"""
        for j, jc in self.cfg["joints"].items():
            a = lim.get(j)
            if not a:
                continue
            if jc.get("vmax") and float(jc["vmax"]) > a["vmax"] + 1e-6:
                self.event("warn", detail=f"{j}: self7.yaml の vmax {jc['vmax']} が agent の {a['vmax']} より大きい")
            r = jc.get("range")
            if r and (r[0] < a["range"][0] or r[1] > a["range"][1]):
                self.event("warn", detail=f"{j}: self7.yaml の範囲 {r} が agent の {a['range']} より広い")

    def engage(self):
        self.client.request("engage", timeout_s=10)
        self.client.start_heartbeat()

    def load(self, run_id, block):
        r = self.client.request("load", run_id=run_id, block={**block, "interp": "smooth"}, timeout_s=10)
        if not r.get("accepted"):
            raise AdapterError("agent が軌道を拒否: " + "; ".join(r.get("errors", [])))

    def goto_rest(self, duration_s):
        self.client.clear_events()
        self.client.request("goto_rest", duration_s=duration_s)
        ev = self.client.wait_event(lambda e: e.get("kind") == "at_rest",
                                    time.perf_counter_ns() + int((duration_s + 3) * 1e9))
        if ev is None:
            raise AdapterError("休止姿勢に着いた知らせが来ない")

    def lead_s(self):
        return 0.3

    def start_at(self, block_id, t0_ns):
        self.offset = self.client.measure_offset()
        self.event("clock", **self.offset)
        t0_pi = t0_ns + self.offset["offset_ns"]
        self.client.request("start_at", block_id=block_id, t0_ns=t0_pi)
        self.event("scheduled", block=block_id, t0_pc_ns=t0_ns, t0_pi_ns=t0_pi)

    def wait_finished(self, block_id, deadline_ns):
        ev = self.client.wait_event(lambda e: e.get("kind") in ("finished", "fault", "disengaged")
                                    and e.get("block", block_id) == block_id, deadline_ns)
        if ev is None:
            return False
        if ev.get("kind") != "finished":
            raise AdapterError(f"自作機が止まった: {ev.get('detail') or ev.get('reason')}")
        return True

    def stop(self, mode="graceful"):
        try:
            self.client.request("stop", mode=mode)
        except AdapterError as e:
            self.event("warn", detail=f"stop を送れない: {e}")

    def status(self):
        return self.client.request("status")["status"]

    def collect_logs(self, run_id):
        try:
            return self.client.request("fetch_log", run_id=run_id, timeout_s=30)["files"]
        except AdapterError as e:
            self.event("warn", detail=f"記録を回収できない: {e}")
            return {}

    def close(self):
        try:
            self.client.request("disengage")
        except AdapterError:
            pass
        self.client.close()
