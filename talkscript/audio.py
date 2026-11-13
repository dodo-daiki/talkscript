"""時刻を指定した音声の出力（talkscript-design.md §8.4）。

sounddevice（PortAudio）の出力の流れを先に開いて無音を流しておき、コールバックが受け取る
「この塊が DAC から出る時刻」（outputBufferDacTime、PortAudio の流れの時刻）を PC の単調時計に直して、
予定の時刻に当たる標本から音声を書き込む。流れの時刻と単調時計の対応は、コールバックの中で
currentTime と perf_counter_ns() を並べて測る（中央値）。DAC の時刻が 0 を返す出力方式では、流れの遅れ（latency）で代用する。
どれだけ正しいか（Windows の MME・WASAPI で）は V7 で測る。実際に出し始めた時刻（推定）を行ごとに記録する。
"""
from __future__ import annotations

import io as _io
import statistics
import threading
import time
import wave


class AudioError(Exception):
    pass


def read_wav_pcm16(b: bytes) -> tuple[bytes, int]:
    with wave.open(_io.BytesIO(b)) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise AudioError(f"モノラル 16 bit の WAV だけ扱う（{w.getnchannels()} ch、{w.getsampwidth() * 8} bit）")
        return w.readframes(w.getnframes()), w.getframerate()


class _Clip:
    def __init__(self, label, pcm, t_ns):
        self.label, self.pcm, self.t_ns = label, pcm, t_ns
        self.pos = 0
        self.started_ns = None
        self.done = False


class AudioOut:
    def __init__(self, samplerate: int, device=None, latency="low", on_event=None):
        try:
            import sounddevice as sd
        except ImportError as e:
            raise AudioError("sounddevice が入っていない（pip install sounddevice）") from e
        self.sd = sd
        self.sr = samplerate
        self.device = device
        self.latency = latency
        self.on_event = on_event or (lambda ev: None)
        self.clips: list[_Clip] = []
        self.lock = threading.Lock()
        self.offsets: list[int] = []
        self.underflows = 0
        self.method = None
        self.stream = None

    def open(self, calibrate_s: float = 0.5) -> dict:
        try:
            self.stream = self.sd.RawOutputStream(samplerate=self.sr, channels=1, dtype="int16", device=self.device,
                                                  latency=self.latency, callback=self._cb)
            self.stream.start()
        except Exception as e:  # noqa: BLE001  PortAudio の例外の型は環境による
            raise AudioError(f"音声の出力を開けない（{self.sr} Hz）: {e}。voice.sampling_rate を出力機器に合わせる"
                             "（例 48000）か、talkscript.toml の [audio] device を変える") from e
        time.sleep(calibrate_s)
        dev = self.sd.query_devices(self.stream.device)
        api = self.sd.query_hostapis(dev["hostapi"])["name"]
        info = {"device": dev["name"], "hostapi": api, "samplerate": self.sr, "latency_s": self.stream.latency,
                "clock_method": self.method, "offset_samples": len(self.offsets)}
        self.on_event({"kind": "audio_open", **info})
        return info

    def _cb(self, outdata, frames, time_info, status):
        now = time.perf_counter_ns()
        if status:
            self.underflows += 1
            self.on_event({"kind": "audio_status", "detail": str(status), "t_pc_ns": now})
        cur, dac = time_info.currentTime, time_info.outputBufferDacTime
        if cur > 0:
            self.offsets.append(now - int(cur * 1e9))
            if len(self.offsets) > 200:
                del self.offsets[:100]
        if cur > 0 and dac > 0 and self.offsets:
            off = int(statistics.median(self.offsets[-50:]))
            dac_pc = int(dac * 1e9) + off
            self.method = "dac_time"
        else:
            dac_pc = now + int(self.stream.latency * 1e9)
            self.method = "latency"
        buf = bytearray(frames * 2)
        with self.lock:
            for c in self.clips:
                if c.done:
                    continue
                if c.started_ns is None:
                    k = round((c.t_ns - dac_pc) * self.sr / 1e9)
                    if k >= frames:
                        continue
                    k = max(0, k)
                    c.started_ns = dac_pc + int(k * 1e9 / self.sr)
                else:
                    k = 0
                n = min(frames - k, (len(c.pcm) - c.pos) // 2)
                buf[k * 2:(k + n) * 2] = c.pcm[c.pos:c.pos + n * 2]
                c.pos += n * 2
                if c.pos >= len(c.pcm):
                    c.done = True
                    self.on_event({"kind": "audio_done", "line": c.label, "t_pc_ns": dac_pc + int((k + n) * 1e9 / self.sr)})
                if c.started_ns is not None and c.pos == n * 2:
                    self.on_event({"kind": "audio_start", "line": c.label, "planned_pc_ns": c.t_ns, "t_pc_ns": c.started_ns,
                                   "late_ms": round((c.started_ns - c.t_ns) / 1e6, 2), "method": self.method})
        outdata[:] = bytes(buf)

    def schedule(self, label: str, wav_bytes: bytes, t_ns: int) -> None:
        pcm, sr = read_wav_pcm16(wav_bytes)
        if sr != self.sr:
            raise AudioError(f"{label}: WAV の {sr} Hz が出力の {self.sr} Hz と違う")
        with self.lock:
            self.clips.append(_Clip(label, pcm, t_ns))

    def wait_idle(self, deadline_ns: int) -> bool:
        while time.perf_counter_ns() < deadline_ns:
            with self.lock:
                if all(c.done for c in self.clips):
                    return True
            time.sleep(0.02)
        return False

    def stop_all(self):
        with self.lock:
            for c in self.clips:
                c.done = True
            self.clips.clear()

    def close(self):
        self.stop_all()
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
