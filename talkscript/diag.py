"""動かすのに必要なものがそろっているかの確認（talkscript doctor と、画面の「環境」で使う）。"""
from __future__ import annotations

import sys

from . import __version__, engine, io


def checks(st: dict, *, network: bool = False) -> list[dict]:
    """[{ok: True / False / None（注意）, what, detail}]。"""
    out = []

    def line(ok, what, detail=""):
        out.append({"ok": ok, "what": what, "detail": detail})

    line(True, "talkscript", f"{__version__}（Python {sys.version.split()[0]}、{engine.platform_key()}）")
    try:
        import sounddevice as sd
        pa = sd.get_portaudio_version()[1]
        try:
            want = st.get("audio", {}).get("device")
            dev = sd.query_devices(want if want is not None else None, kind="output")
            line(True, "音声の出力", f"{pa}、{'指定の' if want is not None else '既定の'}出力 {dev['name']}")
        except Exception as e:  # noqa: BLE001
            line(False, "音声の出力", f"{pa} は使えるが、出力機器が見つからない（{e}）。スピーカーをつなぐか、OS の音声の設定を確かめる")
    except (ImportError, OSError) as e:
        line(False, "音声の出力", f"PortAudio が使えない: {e}（Linux は libportaudio2 が要る）")
    url = st.get("voicevox", {}).get("url", "http://127.0.0.1:50021")
    v = engine.running_version(url)
    exe = engine.find_engine(st)
    if v:
        line(True, "VOICEVOX ENGINE", f"{v} が動いている（{url}）")
    elif exe:
        line(True, "VOICEVOX ENGINE", f"止まっている（合成のときに自動で起動する: {exe}）")
    else:
        line(False, "VOICEVOX ENGINE", "見つからない（talkscript setup）")
    if io.ROOT is None:
        line(False, "作業フォルダ", "見つからない（talkscript init <フォルダ> --default）")
    else:
        line(True, "作業フォルダ", str(io.ROOT))
        for r in sorted(x.stem for x in io.CONFIG.glob("robots/*.yaml")):
            try:
                cfg = io.load_robot(r)
                host = st.get(r, {}).get("host", (cfg.get("connection") or {}).get("host"))
                tbd = [j for j, jc in cfg["joints"].items()
                       if any(io.is_tbd(jc.get(k, 0)) for k in ("sign", "offset", "range", "vmax"))]
                detail = f"アドレス {host}" + (f"、未確定の関節 {len(tbd)}" if tbd else "")
                line(None if io.is_tbd(host) or tbd else True, f"ロボット {r}", detail)
            except io.FormatError as e:
                line(False, f"ロボット {r}", str(e))
        n = len(list(io.SCRIPTS.glob("*.yaml")))
        line(True, "台本", f"{n} 個（{io.SCRIPTS}）")
    if network and io.ROOT is not None:
        try:
            from .robots.self7 import Self7Adapter
            ad = Self7Adapter(io.load_robot("self7"), st, lambda e: None)
            info = ad.connect()
            ad.client.close()
            line(True, "自作機の agent", f"{info['agent']}、時計のずれ {info['clock']['offset_ns'] / 1e6:.1f} ms")
        except Exception as e:  # noqa: BLE001
            line(False, "自作機の agent", str(e))
    return out
