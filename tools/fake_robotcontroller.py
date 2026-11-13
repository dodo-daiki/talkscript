#!/usr/bin/env python3
"""RobotController の擬似サーバ（テスト用。talkscript-design.md §14）。

    python tools/fake_robotcontroller.py --port 22222

1 接続 1 コマンド。4 バイトの本文長（big-endian）＋本文。read_axes には現在の角度を返し、play_pose・play_motion の
最後の姿勢で角度を（すぐに）書き換える。受けたコマンドと受信の時刻を commands に残す。
"""
from __future__ import annotations

import argparse
import json
import socketserver
import struct
import threading
import time

AXES = ["BODY_Y", "L_SHOU", "L_ELBO", "R_SHOU", "R_ELBO", "HEAD_Y", "HEAD_P", "HEAD_R"]
NO_PAYLOAD = {"stop_pose", "stop_motion", "stop_wav", "stop_idle_motion", "read_axes"}


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError("closed")
        buf += c
    return buf


def _recv_frame(s):
    n = struct.unpack(">i", _recv_exact(s, 4))[0]
    return _recv_exact(s, n)


class FakeRobotController:
    def __init__(self, host="127.0.0.1", port=0):
        self.axes = {a: 0 for a in AXES}
        self.commands = []
        self.lock = threading.Lock()
        outer = self

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    cmd = _recv_frame(self.request).decode("ascii")
                    t = time.perf_counter_ns()
                    payload = None
                    if cmd not in NO_PAYLOAD:
                        payload = json.loads(_recv_frame(self.request))
                    with outer.lock:
                        outer.commands.append((cmd, payload, t))
                        if cmd == "play_pose":
                            outer.axes.update(payload.get("ServoMap", {}))
                        elif cmd == "play_motion" and payload:
                            outer.axes.update(payload[-1].get("ServoMap", {}))
                        if cmd == "read_axes":
                            body = json.dumps(outer.axes).encode("ascii")
                            self.request.sendall(struct.pack(">i", len(body)) + body)
                except ConnectionError:
                    pass

        class S(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = S((host, port), H)
        self.port = self.server.server_address[1]

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=22222)
    a = ap.parse_args()
    f = FakeRobotController("127.0.0.1", a.port)
    print(f"fake RobotController: 127.0.0.1:{f.port}")
    try:
        f.server.serve_forever()
    except KeyboardInterrupt:
        pass
