"""実行の記録。runs/<実行 ID>/ に書く。"""
from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import threading
import time

from . import __version__, io


def git_state() -> dict:
    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=io.ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    status = run("status", "--porcelain", "--", ".")
    return {"commit": run("rev-parse", "HEAD"), "dirty_talkscript": bool(status) if status is not None else None}


class RunRecord:
    def __init__(self, script_id: str, *, base: pathlib.Path | None = None, run_id: str | None = None, echo=print):
        self.echo = echo
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = base or io.RUNS
        self.run_id = run_id or f"{stamp}_{script_id}"
        k = 2
        while (base / self.run_id).exists():
            self.run_id = f"{stamp}_{script_id}_{k}"
            k += 1
        self.dir = base / self.run_id
        self.dir.mkdir(parents=True)
        self.lock = threading.Lock()
        self.events_fp = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self.audio_fp = open(self.dir / "audio.jsonl", "a", encoding="utf-8")
        self.run = {"run_id": self.run_id, "script": script_id, "talkscript": __version__, "git": git_state(),
                    "started_at": datetime.datetime.now().isoformat(timespec="milliseconds"),
                    "clock": "PC の time.perf_counter_ns()（単調時計）。壁時計は wall で添える"}
        self.counts = {"warn": 0, "fault": 0, "audio_status": 0}
        self.ref = (time.perf_counter_ns(), time.time_ns())

    def wall(self, t_pc_ns: int) -> str:
        t = self.ref[1] + (t_pc_ns - self.ref[0])
        return datetime.datetime.fromtimestamp(t / 1e9).isoformat(timespec="milliseconds")

    def event(self, ev: dict) -> None:
        ev = dict(ev)
        ev.setdefault("t_pc_ns", time.perf_counter_ns())
        ev["wall"] = self.wall(ev["t_pc_ns"])
        kind = ev.get("kind", "")
        for k in self.counts:
            if kind == k:
                self.counts[k] += 1
        fp = self.audio_fp if kind.startswith("audio") else self.events_fp
        with self.lock:
            fp.write(json.dumps(ev, ensure_ascii=False) + "\n")
            fp.flush()
        if kind in ("warn", "fault", "heartbeat_lost", "disengaged", "audio_status"):
            self.echo(f"  [{ev.get('robot', '')}{'' if 'robot' not in ev else ' '}{kind}] {ev.get('detail') or ev.get('reason') or ''}")

    def write_run(self, **kw) -> None:
        self.run.update(kw)
        io.write_json(self.dir / "run.json", self.run)

    def write_file(self, name: str, text: str) -> None:
        (self.dir / name).write_text(text, encoding="utf-8")

    def summarize(self) -> str:
        evs = [json.loads(x) for x in (self.dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        aus = [json.loads(x) for x in (self.dir / "audio.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        lines = [f"# {self.run_id}", "", f"- 台本: {self.run.get('script')}、モード: {self.run.get('mode')}、"
                 f"ロボット: {self.run.get('robots')}", f"- 結果: {self.run.get('result')}", ""]
        t0s = {e["block"]: e["t0_pc_ns"] for e in evs if e.get("kind") == "block_t0"}
        lines.append("| ブロック | 何 | t0 からの差 [ms] | 備考 |")
        lines.append("|---|---|---|---|")
        for e in evs:
            b = e.get("block")
            if b not in t0s:
                continue
            if e.get("kind") == "started":
                lines.append(f"| {b} | {e.get('robot')} 開始（agent の周期） | {(e['t_pc_ns'] - t0s[b]) / 1e6:+.1f} | "
                             f"agent の申告 {e.get('late_ms')} ms |")
            if e.get("kind") == "sent":
                lines.append(f"| {b} | {e.get('robot')} play_motion 送信 | {(e['t_pc_ns'] - t0s[b]) / 1e6:+.1f} | "
                             f"遅れの補正 {self.run.get('sota_latency_s')} s |")
        for a in aus:
            if a.get("kind") == "audio_start":
                lines.append(f"| | 音声 {a['line']} 出力開始（推定） | 予定との差 {a['late_ms']:+.1f} | 方式 {a.get('method')} |")
        lines += ["", f"警告 {self.counts['warn']}、故障 {self.counts['fault']}、音声の乱れ {self.counts['audio_status']}", "",
                  "（音声の出力開始は PortAudio の時刻からの推定。動き出しの実際の時刻は self7_*.csv の応答の角度から後で求める）"]
        text = "\n".join(lines) + "\n"
        self.write_file("summary.md", text)
        return text

    def close(self):
        for fp in (self.events_fp, self.audio_fp):
            try:
                fp.close()
            except OSError:
                pass
