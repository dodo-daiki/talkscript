"""コマンド。引数なしで実行すると、画面（GUI）を開く。

    talkscript                           # 画面を開く（talkscript gui と同じ）
    talkscript setup                     # 初回の準備（VOICEVOX の導入・PC ごとの設定・作業フォルダ）。足りないものは確認してから入れる
    talkscript doctor                    # 動かすのに必要なものがそろっているかを確かめる
    talkscript init <フォルダ>           # 作業フォルダを作る（台本・ロボットの設定・ひな形の例を写す）
    talkscript engine start|stop|status  # VOICEVOX ENGINE（合成のコマンドは、止まっていれば自動で起動する）
    talkscript voices
    talkscript synth demo
    talkscript marks demo [L1]
    talkscript listen demo [L1]
    talkscript freeze demo [--undo]
    talkscript compile demo
    talkscript insert demo --block B1 --gesture nod --at "L1:phrase=2:end" [--write]
    talkscript play demo --mode speech_motion --robots self7 [--note …] [--condition …]
    talkscript status / stop / sota-axes / latency-sota --native HEAD_Y=10

作業フォルダは --workspace、環境変数 TALKSCRIPT_HOME、今いるフォルダから上の talkscript.toml、
PC ごとの settings.toml の workspace の順に探す（workspace.py）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

from . import __version__, build, engine, io, workspace
from .audio import AudioError
from .gestures import InsertError
from .robots import AdapterError
from .voicevox import VoicevoxClient, VoicevoxError

#: 作業フォルダなしで動くコマンド
NO_WORKSPACE = {"setup", "doctor", "init", "engine", "voices", "shortcut", "update", "uninstall"}


def _client(st):
    engine.start_engine(st)
    return VoicevoxClient(st.get("voicevox", {}).get("url", "http://127.0.0.1:50021"))


def cmd_voices(a, st):
    c = _client(st)
    print(f"VOICEVOX ENGINE {c.version()}（利用規約: {engine.TERMS_URL} と、声ごとの規約）")
    for s in c.speakers():
        print(f"{s['name']}: " + ", ".join(f"{x['name']}={x['id']}" for x in s["styles"]))


def cmd_synth(a, st):
    s, _ = io.load_script(a.script)
    build.synth(s, _client(st), force=a.force, lines=a.line)
    print(build.marks_table(s))


def cmd_marks(a, st):
    s, _ = io.load_script(a.script)
    if a.update:
        build.synth(s, _client(st), lines=[a.line] if a.line else None)
    print(build.marks_table(s, a.line))


def cmd_listen(a, st):
    from .audio import AudioOut, read_wav_pcm16
    s, _ = io.load_script(a.script)
    for _, ln in io.iter_lines(s):
        if a.line and ln["id"] != a.line:
            continue
        p = io.BUILD / s["id"] / f"{ln['id']}.wav"
        if not p.exists():
            print(f"{ln['id']}: 合成していない")
            continue
        wav = p.read_bytes()
        _, sr = read_wav_pcm16(wav)
        print(f"{ln['id']}: {ln['say']}")
        out = AudioOut(sr, device=st.get("audio", {}).get("device"))
        out.open(calibrate_s=0.2)
        try:
            out.schedule(ln["id"], wav, time.perf_counter_ns() + int(0.2e9))
            out.wait_idle(time.perf_counter_ns() + int(60e9))
            time.sleep(0.3)
        finally:
            out.close()


def cmd_freeze(a, st):
    s, _ = io.load_script(a.script)
    man = build.freeze(s, undo=a.undo)
    print("固定を解いた" if a.undo else f"固定した（{man['frozen_at']}）")


def print_issues(issues):
    for lv in ("error", "warn", "info"):
        for i in issues:
            if i["level"] == lv:
                print(f"  {i}")


def cmd_compile(a, st):
    from .check import compile_and_check
    s, _ = io.load_script(a.script)
    trajs, issues = compile_and_check(s, write=True, sota_dt_ms=int(st.get("play", {}).get("sota_dt_ms", 100)))
    print_issues(issues)
    n = sum(i["level"] == "error" for i in issues)
    for r, t in trajs.items():
        print(f"{r}: build/{s['id']}/{r}.traj.json  ブロック {len(t['blocks'])}")
        for j, p in (t.get("check", {}).get("peaks") or {}).items():
            if p["v_deg_s"]:
                print(f"    {j:12s} 最大 {p['v_deg_s']:7.1f} °/s  {p['a_deg_s2']:8.1f} °/s²")
    print("検査: 合格" if n == 0 else f"検査: エラー {n} 件（実行できない）")
    return 0 if n == 0 else 1


def cmd_insert(a, st):
    from . import gestures
    from .check import load_robot_cfgs
    s, p = io.load_script(a.script)
    at = gestures.parse_at(a.at) if a.at else None
    rep = gestures.insert(s, a.block, a.gesture, at=at, t_ms=a.t_ms, amplitude=a.amplitude, speed=a.speed,
                          robot_cfgs=load_robot_cfgs(s))
    print(f"{rep['group']}: {rep['inserted']} 点を入れ、{rep['replaced']} 点を置き換えた。入れなかったロボット: {rep['skipped_robots']}")
    blk = next(b for b in s["blocks"] if b["id"] == a.block)
    if a.write:
        shutil.copy(p, p.with_suffix(".yaml.bak"))
        io.write_yaml(p, s)
        print(f"{p.name} に書いた（元は {p.name}.bak。コメントは消える）")
    else:
        import yaml
        print("--- 挿入後のブロックの tracks（--write で台本に書く）---")
        print(yaml.safe_dump({"tracks": blk["tracks"], "groups": blk["groups"]}, allow_unicode=True, sort_keys=False))


def cmd_play(a, st):
    from .run import PlayError, play
    notes = {"note": a.note, "condition": a.condition, "subject": a.subject, "distance": a.distance, "spl_db": a.spl}
    from .run import missing_notes
    missing = missing_notes(st, notes, a.mode)
    if missing:
        print(f"記録の手入力の項目が足りない: {missing}（talkscript.toml の play.required_notes）")
        return 2
    try:
        play(a.script, mode=a.mode, robots=a.robots.split(",") if a.robots else None, settings=st,
             notes={k: v for k, v in notes.items() if v is not None})
    except PlayError as e:
        print(f"実行しない: {e}")
        return 1


def _adapters(st, names):
    from .robots import make_adapter
    out = {}
    for r in names:
        out[r] = make_adapter(r, io.load_robot(r), st, lambda ev: print(f"  {ev}"))
    return out


def cmd_status(a, st):
    for r in (a.robots or "self7,sota").split(","):
        try:
            ad = _adapters(st, [r])[r]
            info = ad.connect()
            print(f"{r}: {info}")
            if r == "self7":
                print(f"  状態: {ad.status()}")
            ad.client.close() if r == "self7" else None
        except Exception as e:  # noqa: BLE001
            print(f"{r}: つながらない（{e}）")


def cmd_stop(a, st):
    for r in (a.robots or "self7,sota").split(","):
        try:
            ad = _adapters(st, [r])[r]
            if r == "self7":
                ad.client.connect()
                ad.client.request("stop", mode="now")
                ad.client.close()
            else:
                ad.stop("now")
            print(f"{r}: 止めた")
        except Exception as e:  # noqa: BLE001
            print(f"{r}: 止められない（{e}）")


def cmd_sota_axes(a, st):
    from .robots.sota import RobotControllerClient
    cfg = io.load_robot("sota")
    conn = {**cfg["connection"], **st.get("sota", {})}
    rc = RobotControllerClient(conn["host"], int(conn.get("port", 22222)))
    for _ in range(a.n):
        print(rc.read_axes())
        time.sleep(a.interval)


def cmd_latency_sota(a, st):
    """Sota の遅れの測定の補助: 予定の時刻 t に PC から短い音を鳴らし、同じ t に Sota へ play_pose を送る。動画で音と動き出しの差を数える。"""
    from .audio import AudioOut
    from .record import RunRecord
    from .robots.sota import RobotControllerClient
    import io as _io
    import math
    import struct
    import wave
    cfg = io.load_robot("sota")
    conn = {**cfg["connection"], **st.get("sota", {})}
    rc = RobotControllerClient(conn["host"], int(conn.get("port", 22222)))
    servo = {k: int(v) for k, v in (x.split("=") for x in a.native)}
    back = rc.read_axes()
    rec = RunRecord("latency-sota")
    sr = 48000
    beep = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 1000 * k / sr))) for k in range(sr // 20))
    bio = _io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr), w.writeframes(beep)
    out = AudioOut(sr, device=st.get("audio", {}).get("device"), on_event=rec.event)
    out.open()
    try:
        for k in range(a.repeat):
            t = time.perf_counter_ns() + int(1.5e9)
            out.schedule(f"beep{k}", bio.getvalue(), t)
            from .robots.base import sleep_until
            sleep_until(t)
            ts = time.perf_counter_ns()
            rc.send("play_pose", {"Msec": a.msec, "ServoMap": servo})
            rec.event({"kind": "sent", "robot": "sota", "command": "play_pose", "servo": servo, "t_pc_ns": ts,
                       "planned_pc_ns": t, "trial": k})
            time.sleep(a.msec / 1000 + 1.0)
            rc.send("play_pose", {"Msec": a.msec, "ServoMap": {j: back[j] for j in servo}})
            time.sleep(a.msec / 1000 + 1.0)
    finally:
        out.close()
        rec.write_run(kind="latency-sota", servo=servo, msec=a.msec)
        rec.close()
    print(f"記録: {rec.dir}（動画で、音と動き出しの差を数える）")


# --- 準備・確認 -------------------------------------------------------------------------------

def ask(question: str, yes: bool) -> bool:
    """確認を求める。--yes なら「はい」。対話できない（標準入力が端末でない）ときは「いいえ」。"""
    if yes:
        print(f"{question} [y/N] y（--yes）")
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print(f"{question} → 対話できないので行わない（行うなら --yes）")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes", "はい")


def cmd_init(a, st):
    made = workspace.init_workspace(a.dir)
    print(f"作業フォルダ {pathlib_resolve(a.dir)}: {len(made)} 個のファイルを作った {made}")
    if a.default:
        p = workspace.write_user_settings({"workspace": str(pathlib_resolve(a.dir))})
        print(f"既定の作業フォルダにした（{p}）")
    print("次に talkscript.toml の [self7]・[sota] の host と、scripts/demo.yaml の話者を書く")


def pathlib_resolve(p):
    import pathlib
    return pathlib.Path(p).expanduser().resolve()


def cmd_setup(a, st):
    print(f"talkscript {__version__}（Python {sys.version.split()[0]}、{engine.platform_key()}）の準備")
    # 1. 音声の出力（PortAudio）
    try:
        import sounddevice as sd
        print(f"[済] 音声の出力: PortAudio {sd.get_portaudio_version()[1]}")
    except (ImportError, OSError) as e:
        print(f"[不足] 音声の出力（PortAudio）が使えない: {e}")
        if sys.platform.startswith("linux"):
            print("       sudo apt-get install libportaudio2 で入れる（install.sh は確認のうえで入れる）")
    # 2. VOICEVOX
    url = st.get("voicevox", {}).get("url", "http://127.0.0.1:50021")
    v = engine.running_version(url)
    exe = engine.find_engine(st)
    if v:
        print(f"[済] VOICEVOX ENGINE {v} が動いている（{url}）")
    elif exe:
        print(f"[済] VOICEVOX ENGINE: {exe}")
        workspace.write_user_settings({"voicevox": {"engine_path": str(exe)}})
    elif a.no_voicevox:
        print("[省略] VOICEVOX（--no-voicevox）")
    else:
        name, _, size = engine.ASSETS.get(engine.platform_key(), ("?", "", 0))
        print(f"[不足] VOICEVOX ENGINE が見つからない。\n"
              f"       導入するもの: VOICEVOX {engine.VOICEVOX_VERSION} CPU 版（{name}、{size / 1e9:.1f} GB）を GitHub の公式の配布から\n"
              f"       置き場所: {engine.install_dir()}（空き容量が {engine.FREE_BYTES_NEEDED / 1e9:.0f} GB ほど要る）\n"
              f"       VOICEVOX の利用規約: {engine.TERMS_URL}（ソフトウェアと、使う声ごとの規約に従う）")
        if ask("VOICEVOX の利用規約を確認し、同意して導入しますか？", a.yes):
            exe = engine.install_voicevox()
            workspace.write_user_settings({"voicevox": {"engine_path": str(exe)}})
            print(f"[済] VOICEVOX ENGINE を導入した: {exe}")
        else:
            print("       導入しなかった。あとで talkscript setup をもう一度実行するか、VOICEVOX を手で入れて"
                  " settings.toml の [voicevox] engine_path を書く")
    # 3. 作業フォルダ
    if io.ROOT is not None:
        print(f"[済] 作業フォルダ: {io.ROOT}")
    else:
        d = pathlib_resolve(a.workspace_dir or (pathlib_resolve("~") / "talkscript-work"))
        print(f"[不足] 作業フォルダがない。作る場所: {d}")
        if ask(f"{d} に作業フォルダを作り、既定の作業フォルダにしますか？", a.yes):
            workspace.init_workspace(d)
            workspace.write_user_settings({"workspace": str(d)})
            io.set_workspace(d)
            print(f"[済] 作業フォルダを作った: {d}")
    from . import shortcut
    try:   # スタートメニュー（アプリの一覧）には必ず置く（Windows の検索にも出る）。デスクトップは尋ねる
        desk = None if shortcut.desktop_exists() or shortcut.desktop_path() is None else \
            ask("デスクトップにも talkscript のアイコンを置きますか？", a.yes)
        for p in shortcut.create(desktop=desk):
            print(f"[済] ショートカット: {p}")
    except Exception as e:  # noqa: BLE001  ショートカットがなくても、talkscript と入力すれば画面は開く
        print(f"[注意] ショートカットを作れなかった: {e}（talkscript と入力すれば画面が開く）")
    print(f"PC ごとの設定: {workspace.user_settings_path()}")
    print("画面を開くには: talkscript（引数なし）か、作ったショートカット")
    print()
    return cmd_doctor(argparse.Namespace(network=False), workspace.load_settings(io.ROOT))


def cmd_shortcut(a, st):
    from . import shortcut
    if a.remove:
        for p in shortcut.remove():
            print(f"消した: {p}")
        return 0
    for p in shortcut.create(desktop=None if a.refresh else True):
        print(f"作った: {p}")
    return 0


def cmd_doctor(a, st):
    from .diag import checks
    rows = checks(st, network=getattr(a, "network", False))
    for r in rows:
        mark = {True: "OK  ", False: "NG  ", None: "注意"}[r["ok"]]
        print(f"[{mark}] {r['what']}{'：' + r['detail'] if r['detail'] else ''}")
    bad = sum(r["ok"] is False for r in rows)
    print("そろっている" if bad == 0 else f"足りないもの {bad} 件")
    return 0 if bad == 0 else 1


def cmd_import(a, st):
    """台本・ひな形の YAML を作業フォルダに取り込む。"""
    import pathlib
    from . import importer
    p = pathlib.Path(a.file)
    try:
        r = importer.import_text(p.read_text(encoding="utf-8-sig"), p.name, new_id=a.id, overwrite=a.force)
    except importer.Exists as e:
        print(f"{e}。上書きするなら --force、別の名前にするなら --id <名前>")
        return 1
    except (importer.ImportError_, OSError) as e:
        print(f"取り込めない: {e}")
        return 1
    what = "台本" if r["kind"] == "script" else "ひな形"
    print(f"{what} {r['id']} を取り込んだ: {r['path']}" + ("（前の内容は .bak に残した）" if r["replaced"] else ""))
    for w in r["warnings"]:
        print(f"注意: {w}")
    if r["kind"] == "script":
        print(f"次に: talkscript synth {r['id']}（合成） → talkscript compile {r['id']}（検査）")
    return 0


def cmd_update(a, st):
    """新しい版を確かめ、あれば（確認してから）更新する。"""
    from . import update
    r = update.check(force=True)
    if r.get("error"):
        print(f"新しい版を確かめられない: {r['error']}")
        return 1
    if not r["newer"]:
        print(f"talkscript {r['current']} は最新です（Release の最新: {r['latest']}）")
        return 0
    print(f"新しい版があります: {r['current']} → {r['latest']}（{r.get('page')}）")
    if r.get("notes"):
        print("変更点: " + r["notes"].strip())
    if a.check:
        return 0
    if not r["can_update"]:
        print(f"ここからは更新できない: {r['reason']}")
        return 1
    if not ask(f"talkscript を {r['latest']} に更新しますか？", a.yes):
        return 0
    how = update.start_update(r, wait_pids=[os.getpid(), os.getppid()])
    if how == "installer":
        print("新しい窓でインストーラーが動きます。このコマンドは終わります（終わったあとに入れ直します）")
    else:
        print(f"{r['latest']} を入れました。画面を開いていたら、閉じて開き直してください")
    return 0


def _close_gui() -> bool:
    """開いている画面（のサーバ）を閉じる。実行中なら閉じずに False。"""
    import json as _json
    import urllib.request
    from .gui import _alive, _state_file
    sf = _state_file()
    try:
        info = _json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not _alive(info):
        return True
    base = f"http://127.0.0.1:{info['port']}/api/"
    hdr = {"X-Token": info["token"], "Content-Type": "application/json"}
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "run", headers=hdr), timeout=3) as r:
            if _json.loads(r.read()).get("state") in ("running", "operator"):
                return False
        urllib.request.urlopen(urllib.request.Request(base + "quit", data=b"{}", headers=hdr, method="POST"), timeout=3).close()
    except OSError:
        pass
    for _ in range(20):
        if not _alive(info):
            break
        time.sleep(0.25)
    return True


def _size(p) -> str:
    n = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def cmd_uninstall(a, st):
    """talkscript が PC に置いたもの（ショートカット・アイコン・VOICEVOX・設定・覚え書き）を消す。
    プログラム本体（uv tool）は、このあとインストーラーが消す（動いているあいだは消せないため）。作業フォルダは消さない。"""
    import shutil
    from . import shortcut
    if not _close_gui():
        print("中止: 画面で実機の実行をしている。終わってから、もう一度アンインストールする")
        return 1
    if engine.stop_engine():
        print("[済] talkscript が起動した VOICEVOX ENGINE を止めた")
    for p in shortcut.remove():
        print(f"[済] 消した: {p}")
    vv = engine.install_dir()
    if vv.exists():
        if ask(f"talkscript が入れた VOICEVOX（{vv}、{_size(vv)}）も消しますか？", a.yes):
            shutil.rmtree(vv, ignore_errors=True)
            print(f"[済] 消した: {vv}")
        else:
            print(f"[残す] VOICEVOX: {vv}")
    ws = workspace.load_user_settings().get("workspace")
    cfg = workspace.user_config_dir()
    if cfg.exists():
        if ask(f"PC ごとの設定（{cfg}。作業フォルダの場所・音声の出力機器など）も消しますか？", a.yes):
            shutil.rmtree(cfg, ignore_errors=True)
            print(f"[済] 消した: {cfg}")
        else:
            print(f"[残す] PC ごとの設定: {cfg}")
    data = workspace.user_data_dir()
    for name in ("gui.json", "gui.log", "update.json", "updates", "engine.pid", "engine.log"):
        p = data / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink(missing_ok=True)
    if data.exists() and not any(data.iterdir()):
        data.rmdir()
    if ws:
        print(f"[残す] 作業フォルダ（台本・実行の記録）: {ws}。要らなければ、自分で消してください")
    return 0


def cmd_engine(a, st):
    url = st.get("voicevox", {}).get("url", "http://127.0.0.1:50021")
    if a.action == "start":
        print(f"VOICEVOX ENGINE {engine.start_engine(st)}（{url}）")
    elif a.action == "stop":
        print("止めた" if engine.stop_engine() else "talkscript が起動したエンジンはない")
    else:
        v = engine.running_version(url)
        print(f"動いている: {v}（{url}）" if v else f"止まっている（場所: {engine.find_engine(st)}）")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="talkscript", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", help="作業フォルダ")
    ap.add_argument("--version", action="version", version=f"talkscript {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="画面を開く")
    p = sub.add_parser("shortcut", help="スタートメニュー（Linux はアプリ一覧）とデスクトップに、画面を開くショートカットを作る")
    p.add_argument("--remove", action="store_true")
    p.add_argument("--refresh", action="store_true", help="スタートメニューと、すでにあるデスクトップのものだけ作り直す（更新のあと）")
    p = sub.add_parser("setup"); p.add_argument("--yes", action="store_true", help="確認をすべて「はい」にする")
    p.add_argument("--no-voicevox", action="store_true"); p.add_argument("--workspace-dir")
    p = sub.add_parser("import", help="台本・動作のひな形の YAML を作業フォルダに取り込む")
    p.add_argument("file"); p.add_argument("--id", help="別の名前で取り込む"); p.add_argument("--force", action="store_true", help="同じ名前があれば上書きする")
    p = sub.add_parser("update", help="新しい版を確かめ、あれば更新する")
    p.add_argument("--check", action="store_true", help="確かめるだけ"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("uninstall", help="talkscript が PC に置いたもの（ショートカット・VOICEVOX・設定）を消す。"
                                         "本体はインストーラーの -Uninstall / --uninstall で消す")
    p.add_argument("--yes", action="store_true", help="確認をすべて「はい」にする（作業フォルダは消さない）")
    p = sub.add_parser("doctor"); p.add_argument("--network", action="store_true", help="Pi の agent へつながるかも確かめる")
    p = sub.add_parser("init"); p.add_argument("dir"); p.add_argument("--default", action="store_true",
                                                                       help="PC ごとの設定の既定の作業フォルダにする")
    p = sub.add_parser("engine"); p.add_argument("action", choices=["start", "stop", "status"])
    sub.add_parser("voices")
    p = sub.add_parser("synth"); p.add_argument("script"); p.add_argument("--force", action="store_true")
    p.add_argument("--line", action="append")
    p = sub.add_parser("marks"); p.add_argument("script"); p.add_argument("line", nargs="?")
    p.add_argument("--update", action="store_true", help="文字の位置（at.text）を VOICEVOX で求め直す")
    p = sub.add_parser("listen"); p.add_argument("script"); p.add_argument("line", nargs="?")
    p = sub.add_parser("freeze"); p.add_argument("script"); p.add_argument("--undo", action="store_true")
    p = sub.add_parser("compile"); p.add_argument("script")
    p = sub.add_parser("insert"); p.add_argument("script"); p.add_argument("--block", required=True)
    p.add_argument("--gesture", required=True); p.add_argument("--at"); p.add_argument("--t-ms", type=float)
    p.add_argument("--amplitude", type=float); p.add_argument("--speed", type=float); p.add_argument("--write", action="store_true")
    p = sub.add_parser("play"); p.add_argument("script")
    p.add_argument("--mode", default="speech_motion", choices=["speech_motion", "motion", "speech_hold", "speech_relax", "dry"])
    p.add_argument("--robots"); p.add_argument("--note"); p.add_argument("--condition"); p.add_argument("--subject")
    p.add_argument("--distance"); p.add_argument("--spl", help="受聴位置の音圧 [dB(A)]（手入力）")
    p = sub.add_parser("status"); p.add_argument("--robots")
    p = sub.add_parser("stop"); p.add_argument("--robots")
    p = sub.add_parser("sota-axes"); p.add_argument("-n", type=int, default=1); p.add_argument("--interval", type=float, default=0.5)
    p = sub.add_parser("latency-sota"); p.add_argument("--native", action="append", required=True, help="例 HEAD_Y=10（ネイティブの角度）")
    p.add_argument("--msec", type=int, default=300); p.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args(argv)
    if a.cmd in (None, "gui"):
        from .gui import main as gui_main
        return gui_main(["--workspace", a.workspace] if a.workspace else [])
    for f in (sys.stdout, sys.stderr):              # 端末の文字コードで出せない文字（° など）で止まらないように
        try:
            f.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ws = workspace.find_workspace(a.workspace)
    if ws is not None and (ws / "talkscript.toml").exists():
        io.set_workspace(ws)
    elif a.cmd not in NO_WORKSPACE:
        where = f"（{ws} に talkscript.toml がない）" if ws else ""
        print(f"作業フォルダが見つからない{where}。talkscript init <フォルダ> で作るか、--workspace で指定する")
        return 2
    st = workspace.load_settings(io.ROOT)
    fn = globals()["cmd_" + a.cmd.replace("-", "_")]
    try:
        return fn(a, st) or 0
    except (io.FormatError, build.BuildError, VoicevoxError, InsertError, AdapterError, AudioError,
            engine.EngineError) as e:
        print(f"エラー: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
