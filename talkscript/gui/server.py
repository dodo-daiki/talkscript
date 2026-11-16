"""画面のサーバ。127.0.0.1 だけで待ち、画面（static/index.html）と API を出す。

API は、起動のたびに作る合言葉（token）をヘッダ X-Token で求める（同じ PC のほかのページから API を呼ばれないように）。
画面は URL の # の後ろで合言葉を受け取る。
"""
from __future__ import annotations

import array
import ctypes
import datetime
import http.server
import io as _io
import json
import os
import pathlib
import secrets
import shutil
import threading
import time
import traceback
import urllib.parse
import wave

import yaml

from .. import __version__, build, engine, io, workspace
from ..check import compile_and_check
from ..voicevox import VoicevoxClient

STATIC = pathlib.Path(__file__).resolve().parent / "static"
CSP = ("default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'none'")
ENV_DT = 0.01                     # 波形の概形の刻み [s]
JOINT_LABELS = {"waist_yaw": "腰ヨー", "neck_yaw": "首ヨー", "neck_pitch": "首ピッチ", "l_shoulder": "左肩", "l_elbow": "左肘",
                "r_shoulder": "右肩", "r_elbow": "右肘"}
ROBOT_LABELS = {"self7": "自作機", "sota": "Sota", "fake": "擬似"}


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _interrupt(thread: threading.Thread) -> None:
    """thread に KeyboardInterrupt を送る（CLI の Ctrl+C と同じ止め方にする）。"""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(KeyboardInterrupt))


class RunState:
    """画面から始めた実行 1 回。"""

    def __init__(self):
        self.thread = None
        self.ctx = {}
        self.log = []
        self.state = "idle"          # idle / running / operator / done
        self.prompt = None
        self.result = None
        self.run_id = None
        self.summary = None
        self.go = threading.Event()
        self.stops = 0


class App:
    def __init__(self, ws: pathlib.Path | None, *, voicevox_factory=None, idle_timeout_s: float = 30.0, log=print):
        self.token = __import__("secrets").token_urlsafe(18)
        self.lock = threading.Lock()
        self.run = RunState()
        self.last_ping = None
        self.idle_timeout_s = idle_timeout_s
        self.log = log
        self.voicevox_factory = voicevox_factory
        self.server = None
        self.update = None          # 新しい版の確認の結果（None = まだ確かめていない、または確かめない）
        if ws is not None:
            io.set_workspace(ws)

    # --- 新しい版 ----------------------------------------------------------------------------
    def start_update_check(self, checker=None) -> None:
        """新しい版を、裏で 1 回確かめる（画面を待たせない）。PC ごとの設定 [update] check = false なら確かめない。"""
        from .. import update
        if workspace.load_user_settings().get("update", {}).get("check", True) is False:
            return
        self.update = {"pending": True}

        def work():
            r = (checker or update.check)()
            self.update = r
            if r.get("newer"):
                self.log(f"新しい版がある: {r['current']} → {r['latest']}")
        threading.Thread(target=work, daemon=True).start()

    def update_public(self) -> dict | None:
        u = self.update
        if not u:
            return None
        return {k: u.get(k) for k in ("pending", "current", "latest", "newer", "page", "notes", "can_update", "reason")}

    def start_update(self) -> dict:
        from .. import update
        if self.run.state in ("running", "operator"):
            raise ApiError("実機の実行中は更新できない", 409)
        u = self.update or {}
        if not u.get("can_update"):
            raise ApiError(u.get("reason") or "更新できる新しい版がない", 409)
        try:
            how = update.start_update(u, wait_pids=[os.getpid(), os.getppid()], log=self.log)
        except (update.UpdateError, OSError) as e:
            raise ApiError(f"更新を始められない: {e}", 500)
        if how == "installer":      # インストーラーが入れ直せるように、このサーバを終える
            threading.Timer(1.5, self.server.shutdown).start()
        return {"mode": how, "latest": u.get("latest")}

    # --- 設定・VOICEVOX ------------------------------------------------------------------------
    def settings(self) -> dict:
        return workspace.load_settings(io.ROOT)

    def voicevox(self):
        if self.voicevox_factory:
            return self.voicevox_factory()
        st = self.settings()
        engine.start_engine(st, log=self.log)
        return VoicevoxClient(st.get("voicevox", {}).get("url", "http://127.0.0.1:50021"))

    def need_ws(self):
        if io.ROOT is None:
            raise ApiError("作業フォルダが決まっていない", 409)

    # --- 台本 ---------------------------------------------------------------------------------
    def robots_info(self) -> dict:
        out = {}
        for p in sorted(io.CONFIG.glob("robots/*.yaml")):
            try:
                cfg = io.load_robot(p.stem)
            except io.FormatError as e:
                out[p.stem] = {"error": str(e)}
                continue
            joints = {}
            for j, jc in cfg["joints"].items():
                rng = jc.get("range")
                joints[j] = {"label": JOINT_LABELS.get(j, j), "part": jc.get("part", ""),
                             "range": None if io.is_tbd(rng) else rng,
                             "vmax": None if io.is_tbd(jc.get("vmax")) else jc.get("vmax"),
                             "amax": None if io.is_tbd(jc.get("amax")) else jc.get("amax"),
                             "tbd": [k for k in ("sign", "offset", "range", "vmax") if k in jc and io.is_tbd(jc[k])]}
            out[p.stem] = {"label": ROBOT_LABELS.get(p.stem, p.stem), "adapter": cfg.get("adapter"), "joints": joints,
                           "rest": cfg["rest"]}
        return out

    def gestures_info(self) -> dict:
        out = {}
        for p in sorted(io.CONFIG.glob("gestures/*.yaml")):
            try:
                g = io.load_gesture(p.stem)
            except io.FormatError:
                continue
            out[p.stem] = {"description": g.get("description", ""), "anchor_at_ms": g.get("anchor_at_ms", 0),
                           "params": g.get("params", {}),
                           "robots": {r: {j: [[ms, None if io.is_tbd(d) else d] for ms, d in pts] for j, pts in spec.items()}
                                      for r, spec in (g.get("robots") or {}).items()}}
        return out

    def state(self) -> dict:
        st = self.settings()
        d = {"version": __version__, "workspace": str(io.ROOT) if io.ROOT else None, "update": self.update_public(),
             "default_workspace": str(pathlib.Path.home() / "talkscript-work"),
             "terms_url": engine.TERMS_URL, "voicevox_version": engine.VOICEVOX_VERSION,
             "settings": {"self7_host": st.get("self7", {}).get("host"), "sota_host": st.get("sota", {}).get("host"),
                          "audio_device": st.get("audio", {}).get("device"),
                          "voicevox_url": st.get("voicevox", {}).get("url")}}
        if io.ROOT is not None:
            d["scripts"] = sorted(p.stem for p in io.SCRIPTS.glob("*.yaml"))
            d["robots"] = self.robots_info()
            d["gestures"] = self.gestures_info()
        return d

    def script_payload(self, sid: str) -> dict:
        p = io.SCRIPTS / f"{sid}.yaml"
        if not p.exists():
            raise ApiError(f"台本 {sid} がない", 404)
        raw = p.read_bytes()
        s = io.read_yaml(p)
        errors = None
        try:
            io.validate_script(s, sid)
        except io.FormatError as e:
            errors = str(e)
        return {"id": sid, "sha": io.sha256_bytes(raw), "script": s, "errors": errors, **self.build_payload(s)}

    def build_payload(self, s: dict) -> dict:
        man = build.load_manifest(s.get("id", ""))
        try:
            status = build.line_status(s, man)
        except Exception:  # noqa: BLE001  形式の誤りのある台本
            status = {}
        lines = {}
        d = io.BUILD / s.get("id", "")
        for lid, stt in status.items():
            e = {"status": stt}
            mp = d / f"{lid}.marks.json"
            if mp.exists():
                m = io.read_json(mp)
                e["timing"] = m["timing"]
                e["text_anchors"] = m.get("text_anchors", {})
                e["say"] = m.get("say")
            qp = d / f"{lid}.query.json"
            if qp.exists():   # 話速を変えた直後の仮の時刻（合成し直すまで）を、前の合成から縮めて出すため
                e["speed"] = io.read_json(qp).get("speedScale")
            wp = d / f"{lid}.wav"
            if wp.exists():
                e["envelope"] = envelope(wp.read_bytes())
                e["wav_sha256"] = man["lines"].get(lid, {}).get("wav_sha256")
            if lid in man["lines"]:
                e["speaker"] = man["lines"][lid].get("speaker")
            lines[lid] = e
        return {"manifest": {"frozen": bool(man.get("frozen")), "frozen_at": man.get("frozen_at"),
                             "engine_version": man.get("engine_version")}, "lines": lines}

    def save_script(self, sid: str, s: dict, base_sha: str | None, force: bool = False) -> str:
        if s.get("id") != sid:
            raise ApiError("台本の id がファイル名と違う")
        io.validate_script(s, sid)
        p = io.SCRIPTS / f"{sid}.yaml"
        if p.exists():
            cur = io.sha256_file(p)
            if base_sha and cur != base_sha and not force:
                raise ApiError("台本のファイルが、画面で開いたあとに書き換わっている（別の画面かエディタで変更された）。"
                               "読み込み直すか、上書きを選ぶ", 409)
            shutil.copy(p, p.with_suffix(".yaml.bak"))
        io.write_yaml(p, s)
        return io.sha256_file(p)

    def new_script(self, sid: str, template: str) -> None:
        if not sid or not all(c.isalnum() or c in "-_" for c in sid):
            raise ApiError("台本の名前は英数字と - _ だけ")
        p = io.SCRIPTS / f"{sid}.yaml"
        if p.exists():
            raise ApiError(f"台本 {sid} はもうある")
        if template and (io.SCRIPTS / f"{template}.yaml").exists():
            s = io.read_yaml(io.SCRIPTS / f"{template}.yaml")
        else:
            robots = [r for r in self.robots_info() if r != "fake"][:1] or ["self7"]
            s = {"format": "talkscript/script@1", "id": sid, "purpose": "demo",
                 "voice": dict(io.DEFAULT_VOICE), "robots": robots,
                 "blocks": [{"id": "B1", "lines": [{"id": "L1", "say": "こんにちは。"}], "tracks": {}}]}
        s["id"] = sid
        io.write_yaml(p, s)

    # --- 実行 ---------------------------------------------------------------------------------
    def start_play(self, body: dict) -> dict:
        from .. import run as run_mod
        missing = run_mod.missing_notes(self.settings(), body.get("notes"), body.get("mode", "speech_motion"))
        if missing:
            raise ApiError(f"記録の手入力の項目が足りない: {', '.join(missing)}（talkscript.toml の play.required_notes）")
        with self.lock:
            if self.run.state in ("running", "operator"):
                raise ApiError("実行中", 409)
            r = self.run = RunState()
        sid = body["id"]

        def log(text):
            r.log.append({"t": time.time(), "text": str(text)})

        def operator(prompt):
            r.prompt, r.state = prompt, "operator"
            r.go.clear()
            while not r.go.wait(0.2):
                pass
            r.prompt, r.state = None, "running"

        def work():
            r.state = "running"
            try:
                rec = run_mod.play(sid, mode=body.get("mode", "speech_motion"), robots=body.get("robots") or None,
                                   settings=self.settings(), notes={k: v for k, v in (body.get("notes") or {}).items() if v},
                                   operator_input=operator, log=log, ctx=r.ctx)
                r.run_id = rec.run_id
                r.result = io.read_json(rec.dir / "run.json").get("result")
                r.summary = (rec.dir / "summary.md").read_text(encoding="utf-8")
            except run_mod.PlayError as e:
                r.result = f"実行しない: {e}"
                log(r.result)
            except KeyboardInterrupt:
                r.result = "interrupted"
            except Exception as e:  # noqa: BLE001
                r.result = f"error: {type(e).__name__}: {e}"
                log(traceback.format_exc())
            finally:
                r.state = "done"
                rec = r.ctx.get("record")
                if rec is not None and r.run_id is None:
                    r.run_id = rec.run_id

        r.thread = threading.Thread(target=work, name="play", daemon=True)
        r.thread.start()
        return {"started": True}

    def stop_play(self, mode: str) -> dict:
        r = self.run
        if r.thread is None or not r.thread.is_alive():
            return {"running": False}
        r.stops += 1
        if mode == "now":
            for a in (r.ctx.get("adapters") or {}).values():
                try:
                    a.stop("now")
                except Exception:  # noqa: BLE001
                    pass
        _interrupt(r.thread)
        return {"running": True, "stops": r.stops}

    def run_status(self, since: int) -> dict:
        r = self.run
        return {"state": r.state, "prompt": r.prompt, "result": r.result, "run_id": r.run_id, "summary": r.summary,
                "log": [x["text"] for x in r.log[since:]], "next": len(r.log)}

    def runs_list(self) -> list:
        out = []
        if io.RUNS is None or not io.RUNS.exists():
            return out
        for d in sorted((x for x in io.RUNS.iterdir() if x.is_dir()), reverse=True)[:40]:
            rj = d / "run.json"
            if not rj.exists():
                continue
            try:
                r = io.read_json(rj)
            except ValueError:
                continue
            out.append({"run_id": d.name, "script": r.get("script"), "mode": r.get("mode"), "robots": r.get("robots"),
                        "result": r.get("result"), "started_at": r.get("started_at"), "notes": r.get("notes")})
        return out


def envelope(wav_bytes: bytes) -> list:
    """WAV の ENV_DT ごとの最大の振幅（0〜1、小数 3 桁）。"""
    with wave.open(_io.BytesIO(wav_bytes)) as w:
        sr, n, sw, ch = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
        if sw != 2:
            return []
        a = array.array("h", w.readframes(n))
    step = max(1, int(sr * ENV_DT)) * ch
    return [round(max(abs(min(a[i:i + step])), abs(max(a[i:i + step]))) / 32768, 3) for i in range(0, len(a), step)]


def make_handler(app: App):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 通信の記録は出さない
            pass

        # --- 応答 ---
        def _send(self, code, body: bytes, ctype: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _route(self, method):
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if not u.path.startswith("/api/"):
                if method != "GET":
                    return self._json({"error": "not found"}, 404)
                if u.path in ("/", "/index.html"):
                    # 読み込むたびに nonce を変え、画面自身のスクリプトだけを動かす（取り込んだ台本の文字が HTML に
                    # 紛れ込んでも、インラインのイベントハンドラや別のスクリプトは動かない）
                    nonce = secrets.token_urlsafe(18)
                    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("<script>", f'<script nonce="{nonce}">', 1)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", {
                        "Content-Security-Policy": CSP.format(nonce=nonce), "Referrer-Policy": "no-referrer"})
                if u.path == "/icon.svg":   # タブと、アプリ用の窓のアイコン
                    return self._send(200, (STATIC.parent.parent / "assets" / "icon.svg").read_bytes(), "image/svg+xml")
                return self._json({"error": "not found"}, 404)
            if self.headers.get("X-Token") != app.token:
                return self._json({"error": "合言葉が違う"}, 403)
            try:
                res = self.api(method, u.path[5:], q)
                if isinstance(res, tuple):
                    return self._send(200, res[0], res[1])
                return self._json(res if res is not None else {})
            except ApiError as e:
                return self._json({"error": str(e)}, e.status)
            except (io.FormatError, build.BuildError) as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                app.log(traceback.format_exc())
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        # --- API ---
        def api(self, method, path, q):
            if path == "ping":
                app.last_ping = time.monotonic()
                return {"ok": True}
            if path == "state":
                return app.state()
            if path == "update" and method == "POST":
                return app.start_update()
            if path == "quit" and method == "POST":
                threading.Thread(target=app.server.shutdown, daemon=True).start()
                return {"ok": True}
            if path == "workspace" and method == "POST":
                b = self._body()
                d = pathlib.Path(b["path"]).expanduser().resolve()
                if not (d / "talkscript.toml").exists():
                    if not b.get("create"):
                        raise ApiError(f"{d} は作業フォルダでない（talkscript.toml がない）")
                    workspace.init_workspace(d)
                io.set_workspace(d)
                if b.get("default", True):
                    workspace.write_user_settings({"workspace": str(d)})
                return app.state()
            if path == "doctor":
                from ..diag import checks
                return {"checks": checks(app.settings(), network=q.get("network") == "1")}
            if path == "devices":
                import sounddevice as sd
                devs = []
                for i, dv in enumerate(sd.query_devices()):
                    if dv["max_output_channels"] > 0:
                        api = sd.query_hostapis(dv["hostapi"])["name"]
                        devs.append({"index": i, "name": dv["name"], "hostapi": api})
                return {"devices": devs, "current": app.settings().get("audio", {}).get("device")}
            if path == "settings" and method == "POST":
                b = self._body()
                if "audio_device" in b:
                    dev = b["audio_device"]
                    cur = workspace.load_user_settings()
                    audio = dict(cur.get("audio", {}))
                    if dev in (None, ""):
                        audio.pop("device", None)
                    else:
                        audio["device"] = dev
                    cur["audio"] = audio
                    p = workspace.user_settings_path()
                    p.unlink(missing_ok=True)
                    workspace.write_user_settings(cur)
                return app.state()
            if path == "voices":
                c = app.voicevox()
                return {"version": c.version(), "speakers": [{"name": s["name"], "styles": [x["name"] for x in s["styles"]]}
                                                             for s in c.speakers()]}
            app.need_ws()
            for k in ("id", "line", "run_id"):     # ファイルの場所になる値は、英数字と - _ だけを受ける
                if k in q and not io.is_id(q[k]):
                    raise ApiError(f"{k}（{q[k]!r}）は英数字と - _ だけで書く")
            if path == "script" and method == "GET":
                return app.script_payload(q["id"])
            if path == "script" and method == "PUT":
                b = self._body()
                sha = app.save_script(q["id"], b["script"], b.get("base_sha"), bool(b.get("force")))
                return {"sha": sha, **app.build_payload(b["script"])}
            if path == "script/import" and method == "POST":
                from .. import importer
                app.need_ws()
                b = self._body()
                try:
                    return importer.import_text(b.get("text", ""), b.get("name", ""), new_id=b.get("id") or None,
                                                overwrite=bool(b.get("overwrite")))
                except importer.Exists as e:
                    return {"exists": True, "kind": e.kind, "id": e.id, "error": str(e)}
                except importer.ImportError_ as e:
                    raise ApiError(str(e))
            if path == "script/new" and method == "POST":
                b = self._body()
                app.new_script(b["id"], b.get("template", ""))
                return app.script_payload(b["id"])
            if path == "synth" and method == "POST":
                b = self._body()
                s = b["script"]
                io.validate_script(s, s.get("id"))
                msgs = []
                build.synth(s, app.voicevox(), lines=b.get("lines"), log=msgs.append)
                return {"log": msgs, **app.build_payload(s)}
            if path == "check" and method == "POST":
                b = self._body()
                s = b["script"]
                try:
                    io.validate_script(s, s.get("id"))
                except io.FormatError as e:
                    return {"issues": [{"level": "error", "code": "K1", "msg": str(e)}], "peaks": {}, "yaml": _dump(s)}
                trajs, issues = compile_and_check(s, write=False)
                return {"issues": [dict(i) for i in issues],
                        "durations": {r: [b2["duration_s"] for b2 in t["blocks"]] for r, t in trajs.items()},
                        "yaml": _dump(s)}
            if path == "audio":
                p = io.BUILD / q["id"] / f"{q['line']}.wav"
                if not p.exists():
                    raise ApiError("合成していない", 404)
                return (p.read_bytes(), "audio/wav")
            if path == "freeze" and method == "POST":
                b = self._body()
                s, _ = io.load_script(b["id"])
                man = build.freeze(s, undo=bool(b.get("undo")))
                return {"frozen": man.get("frozen"), "frozen_at": man.get("frozen_at")}
            if path == "play" and method == "POST":
                return app.start_play(self._body())
            if path == "run":
                return app.run_status(int(q.get("since", 0)))
            if path == "run/continue" and method == "POST":
                app.run.go.set()
                return {"ok": True}
            if path == "run/stop" and method == "POST":
                return app.stop_play(self._body().get("mode", "graceful"))
            if path == "runs":
                return {"runs": app.runs_list()}
            if path == "runs/summary":
                d = io.RUNS / q["run_id"]
                if not (d / "summary.md").exists() or d.parent != io.RUNS:
                    raise ApiError("記録がない", 404)
                return {"summary": (d / "summary.md").read_text(encoding="utf-8")}
            raise ApiError(f"知らない API {method} {path}", 404)

    return H


def _dump(s: dict) -> str:
    return yaml.safe_dump(s, allow_unicode=True, sort_keys=False, width=140, default_flow_style=None)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def serve(app: App, port: int = 0) -> Server:
    srv = Server(("127.0.0.1", port), make_handler(app))
    app.server = srv
    return srv


def idle_watch(app: App) -> None:
    """画面からの合図が idle_timeout_s 来なくなったら（窓を閉じた）、実行中でなければ止める。"""
    started = time.monotonic()
    while True:
        time.sleep(2)
        busy = app.run.state in ("running", "operator")
        last = app.last_ping if app.last_ping is not None else started
        limit = app.idle_timeout_s if app.last_ping is not None else max(120.0, app.idle_timeout_s)
        if not busy and time.monotonic() - last > limit:
            app.log(f"{datetime.datetime.now():%H:%M:%S} 画面が閉じられたので終了する")
            app.server.shutdown()
            return
