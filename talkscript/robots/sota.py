"""Sota のアダプタ: 研究室の RobotController（Sota の上の Java の TCP サーバ）へ送る（talkscript-design.md §8.7）。

通信（RobotController の README、2026-11-13 に確認した現行版。研究室の Sota の版が同じかは未確認 = V4）:
  1 接続 1 コマンド。メッセージ = 4 バイトの本文長（big-endian、ヘッダ自身は含まない）＋本文。
  第 1 メッセージがコマンド名（ASCII）、第 2 メッセージが中身（不要なコマンドは送らない）。
  play_pose / play_motion / stop_pose / stop_motion は応答なし。read_axes だけ JSON を 1 メッセージ返す。
  姿勢 = {"Msec": 整数 ms, "ServoMap": {軸名: 角度（整数, 度）}}。モーションは姿勢の配列で、各要素を順に投入し Msec だけ待つ。
起動（ssh・java -jar）は人が行う。パスワードはリポジトリに書かない。
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time

from .. import io
from .base import AdapterError, RobotAdapter, sleep_until


def _frame(b: bytes) -> bytes:
    return struct.pack(">i", len(b)) + b


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise AdapterError("RobotController が途中で接続を閉じた")
        buf += chunk
    return buf


class RobotControllerClient:
    def __init__(self, host: str, port: int = 22222, timeout_s: float = 3.0):
        self.host, self.port, self.timeout_s = host, port, timeout_s

    def open(self) -> socket.socket:
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
        except OSError as e:
            raise AdapterError(f"RobotController（{self.host}:{self.port}）につながらない: {e}。"
                               "Sota で java -jar RobotController.jar を起動したか") from e
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return s

    def send(self, command: str, payload=None, *, reply: bool = False, sock: socket.socket | None = None):
        """1 コマンドを送る。payload は dict/list（JSON にする）か bytes。sock を渡すと、コマンド名を送り済みの接続を使う。"""
        s = sock or self.open()
        try:
            if sock is None:
                s.sendall(_frame(command.encode("ascii")))
            if payload is not None:
                body = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode("ascii")
                s.sendall(_frame(body))
            if reply:
                n = struct.unpack(">i", _recv_exact(s, 4))[0]
                return json.loads(_recv_exact(s, n).decode("ascii"))
            return None
        finally:
            s.close()

    def read_axes(self) -> dict:
        return self.send("read_axes", reply=True)


class SotaAdapter(RobotAdapter):
    def __init__(self, cfg, settings, on_event):
        super().__init__(cfg, settings, on_event)
        conn = dict(cfg.get("connection") or {})
        conn.update(settings.get("sota", {}))
        self.rc = RobotControllerClient(conn["host"], int(conn.get("port", 22222)))
        lat = settings.get("sota", {}).get("latency_s", cfg.get("latency_s"))
        self.latency_s = 0.0 if io.is_tbd(lat) else float(lat)
        self.latency_measured = not io.is_tbd(lat)
        self.preconnect = bool(settings.get("sota", {}).get("preconnect", cfg.get("preconnect", False)))
        self.axes_hz = float(settings.get("sota", {}).get("axes_hz", cfg.get("axes_hz", 0)))
        self.blocks = {}
        self._stop = threading.Event()
        self._threads = []
        self.axes_rows = []

    def connect(self):
        axes = self.rc.read_axes()
        if not self.latency_measured:
            self.event("warn", detail="Sota の遅れ latency_s が未測定（V5）。0 秒として送る")
        return {"axes": axes, "latency_s": self.latency_s, "preconnect": self.preconnect}

    def _need_native(self):
        if any(io.is_tbd(jc.get("sign")) or io.is_tbd(jc.get("offset")) for jc in self.cfg["joints"].values()):
            raise AdapterError("config/robots/sota.yaml の sign・offset が未確定（V4）。Sota は動かせない")

    def load(self, run_id, block):
        self._need_native()
        if not block.get("poses"):
            raise AdapterError(f"{block['id']}: Sota の姿勢の列がない（talkscript compile を確かめる）")
        self.blocks[block["id"]] = block

    def rest_native(self):
        self._need_native()
        from ..compile import sota_native
        return sota_native(self.cfg, self.cfg["rest"])

    def goto_rest(self, duration_s):
        rest = self.rest_native()
        self.rc.send("play_pose", {"Msec": int(duration_s * 1000), "ServoMap": rest})
        time.sleep(duration_s + 0.2)
        axes = self.rc.read_axes()
        bad = {j: (axes.get(j), v) for j, v in rest.items() if j in axes and abs(axes[j] - v) > 3}
        self.event("at_rest", axes=axes)
        if bad:
            self.event("warn", detail=f"休止姿勢から 3° 以上ずれている（読んだ値, 目標）: {bad}")

    def lead_s(self):
        return self.latency_s + 0.3

    def start_at(self, block_id, t0_ns):
        block = self.blocks[block_id]
        self._stop.clear()
        send_ns = t0_ns - int(self.latency_s * 1e9)
        sock = None
        if self.preconnect:
            sock = self.rc.open()
            sock.sendall(_frame(b"play_motion"))

        def run():
            if not sleep_until(send_ns, self._stop):
                if sock:
                    sock.close()
                return
            t = time.perf_counter_ns()
            try:
                self.rc.send("play_motion", block["poses"], sock=sock)
                self.event("sent", block=block_id, command="play_motion", t_pc_ns=t, poses=len(block["poses"]),
                           send_late_ms=round((t - send_ns) / 1e6, 2), done_pc_ns=time.perf_counter_ns())
            except AdapterError as e:
                self.event("fault", block=block_id, detail=str(e))

        th = threading.Thread(target=run, daemon=True)
        th.start()
        self._threads.append(th)
        if self.axes_hz > 0:
            end_ns = t0_ns + int((block["duration_s"] + 1.0) * 1e9)
            ax = threading.Thread(target=self._log_axes, args=(block_id, t0_ns, end_ns), daemon=True)
            ax.start()
            self._threads.append(ax)
        self.event("scheduled", block=block_id, t0_pc_ns=t0_ns, send_pc_ns=send_ns)

    def _log_axes(self, block_id, t0_ns, end_ns):
        period = 1.0 / self.axes_hz
        nxt = t0_ns - int(0.5e9)
        while not self._stop.is_set() and time.perf_counter_ns() < end_ns:
            sleep_until(nxt, self._stop)
            try:
                a = self.rc.read_axes()
                self.axes_rows.append({"block": block_id, "t_pc_ns": time.perf_counter_ns(), **a})
            except AdapterError:
                pass
            nxt += int(period * 1e9)

    def wait_finished(self, block_id, deadline_ns):
        """Sota は完了を返さないので、予定の終わり（送信 + 遅れ + Msec の合計）まで待つだけ。"""
        block = self.blocks[block_id]
        for th in self._threads:
            th.join(max(0.0, (deadline_ns - time.perf_counter_ns()) / 1e9))
        self._threads = []
        self.event("finished_estimated", block=block_id, total_ms=sum(p["Msec"] for p in block["poses"]))
        return True

    def stop(self, mode="graceful"):
        self._stop.set()
        try:
            self.rc.send("stop_motion")
            self.rc.send("stop_pose")
            a1 = self.rc.read_axes()
            time.sleep(0.2)
            a2 = self.rc.read_axes()
            moving = {j: (a1[j], a2.get(j)) for j in a1 if a2.get(j) != a1[j]}
            self.event("stopped", axes=a2, still_moving=moving)
        except AdapterError as e:
            self.event("warn", detail=f"Sota を止められたか確かめられない: {e}")

    def collect_logs(self, run_id):
        if not self.axes_rows:
            return {}
        keys = sorted({k for r in self.axes_rows for k in r if k not in ("block", "t_pc_ns")})
        lines = ["block,t_pc_ns," + ",".join(keys)]
        for r in self.axes_rows:
            lines.append(",".join([r["block"], str(r["t_pc_ns"])] + [str(r.get(k, "")) for k in keys]))
        return {"sota_axes.csv": "\n".join(lines) + "\n"}
