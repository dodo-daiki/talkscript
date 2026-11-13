"""audio_query からモーラ・アクセント句の時刻を計算する（talkscript-design.md §5.2）。

VOICEVOX ENGINE の合成の仕組みについての仮定（V-T1 で確かめる。エンジンのソースの記憶にもとづく。未確認）:

- 音素の長さは秒。子音・母音・句の後の間（pause_mora）・前後の無音（pre/postPhonemeLength）が 1 つずつ音素になる。
- 文中の間は、`pauseLength` が null でなければその値に置き換え、`pauseLengthScale` を掛ける（項目がない版では何もしない）。
- 全音素の長さを `speedScale` で割る（前後の無音も割るかは版によって違う可能性がある）。
- 音素ごとに、フレーム（24000/256 = 93.75 フレーム/秒）の整数に丸める。

確かでないので、「前後の無音を話速で割るか」×「フレームに丸めるか」の 4 通りで全体の長さを計算し、
WAV の長さと 10 ms 以内で合うものを採用して marks.json に記録する（どれも合わなければエラー）。
"""
from __future__ import annotations

FRAME_RATE = 24000 / 256          # 93.75 フレーム/秒（仮定。V-T1）
TOLERANCE_S = 0.010               # 計算と WAV の長さの差の許容（提案。design §5.2）

MODELS = [                         # 試す順番（最初に合ったものを採る）
    {"name": "frames_scaled_prepost", "frames": True, "scale_prepost": True},
    {"name": "frames_unscaled_prepost", "frames": True, "scale_prepost": False},
    {"name": "raw_scaled_prepost", "frames": False, "scale_prepost": True},
    {"name": "raw_unscaled_prepost", "frames": False, "scale_prepost": False},
]


class TimingMismatch(Exception):
    pass


def _phonemes(query: dict, scale_prepost: bool):
    """(種類, 長さ[s], 句の番号, モーラの番号) の列。モーラの番号は行の中の通し番号（間は None）。"""
    speed = float(query.get("speedScale", 1.0)) or 1.0
    pause_len = query.get("pauseLength")
    pause_scale = float(query.get("pauseLengthScale", 1.0) if query.get("pauseLengthScale") is not None else 1.0)
    pre = float(query.get("prePhonemeLength", 0.0))
    post = float(query.get("postPhonemeLength", 0.0))
    out = [("pre", pre / speed if scale_prepost else pre, None, None)]
    mi = 0
    for pi, ap in enumerate(query["accent_phrases"]):
        for m in ap["moras"]:
            if m.get("consonant") and m.get("consonant_length") is not None:
                out.append(("consonant", float(m["consonant_length"]) / speed, pi, mi))
            out.append(("vowel", float(m["vowel_length"]) / speed, pi, mi))
            mi += 1
        pm = ap.get("pause_mora")
        if pm:
            length = float(pause_len) if pause_len is not None else float(pm["vowel_length"])
            out.append(("pause", length * pause_scale / speed, pi, None))
    out.append(("post", post / speed if scale_prepost else post, None, None))
    return out


def _lengths(phonemes, frames: bool):
    if frames:
        return [round(p[1] * FRAME_RATE) / FRAME_RATE for p in phonemes]
    return [p[1] for p in phonemes]


def total_duration(query: dict, model: dict) -> float:
    return sum(_lengths(_phonemes(query, model["scale_prepost"]), model["frames"]))


def timeline(query: dict, model: dict | None = None) -> dict:
    """モーラと句の時刻（行の音声の始まり = 0 秒）。model を省略すると MODELS[0]。"""
    model = model or MODELS[0]
    ph = _phonemes(query, model["scale_prepost"])
    lens = _lengths(ph, model["frames"])
    moras, phrases = [], []
    flat = [m for ap in query["accent_phrases"] for m in ap["moras"]]
    t = 0.0
    for (kind, _, pi, mi), L in zip(ph, lens):
        if kind in ("consonant", "vowel"):
            if not moras or moras[-1]["i"] != mi:
                moras.append({"i": mi, "phrase": pi, "text": flat[mi]["text"], "start": t, "end": t + L})
            else:
                moras[-1]["end"] = t + L
        t += L
    for pi, ap in enumerate(query["accent_phrases"]):
        ms = [m for m in moras if m["phrase"] == pi]
        if not ms:
            continue
        phrases.append({"i": pi, "kana": "".join(m["text"] for m in ms), "start": ms[0]["start"], "end": ms[-1]["end"],
                        "mora_start": ms[0]["i"], "mora_end": ms[-1]["i"],
                        "pause_after": bool(ap.get("pause_mora"))})
    return {"model": model["name"], "duration": t, "moras": moras, "phrases": phrases}


def fit_timeline(query: dict, wav_duration_s: float) -> dict:
    """WAV の長さに合う仮定を探して時刻を返す。合わなければ TimingMismatch（V-T1 の結果で式を直す）。"""
    tried = []
    for model in MODELS:
        d = total_duration(query, model)
        tried.append(f"{model['name']}: {d:.4f} s")
        if abs(d - wav_duration_s) <= TOLERANCE_S:
            tl = timeline(query, model)
            tl["wav_duration"] = wav_duration_s
            tl["diff_s"] = d - wav_duration_s
            return tl
    raise TimingMismatch(f"計算した長さが WAV の長さ {wav_duration_s:.4f} s と合わない（許容 {TOLERANCE_S * 1000:.0f} ms）: "
                         + "; ".join(tried) + "。talkscript/timing.py の仮定を V-T1 で確かめて直す")


def anchor_time(marks: dict, at: dict) -> float:
    """発話の位置（phrase / mora / text）の時刻 [s]（行の音声の始まりから）。text は marks の text_anchors を使う。"""
    edge = at.get("edge", "start")
    if edge not in ("start", "end"):
        raise KeyError(f"edge は start か end（{edge}）")
    if "phrase" in at:
        ps = {p["i"]: p for p in marks["timing"]["phrases"]}
        if at["phrase"] not in ps:
            raise KeyError(f"句 {at['phrase']} がない（0〜{len(ps) - 1}）")
        return ps[at["phrase"]][edge]
    if "mora" in at:
        ms = marks["timing"]["moras"]
        if not 0 <= at["mora"] < len(ms):
            raise KeyError(f"モーラ {at['mora']} がない（0〜{len(ms) - 1}）")
        return ms[at["mora"]][edge]
    if "text" in at:
        key = text_key(at["text"], at.get("occurrence", 1))
        ta = marks.get("text_anchors", {}).get(key)
        if ta is None:
            raise KeyError(f"文字の位置「{at['text']}」がまだ求められていない（talkscript marks で求める）")
        if "error" in ta:
            raise KeyError(f"文字の位置「{at['text']}」: {ta['error']}")
        ms = marks["timing"]["moras"]
        return ms[ta["mora_start"]]["start"] if edge == "start" else ms[ta["mora_end"]]["end"]
    raise KeyError("at には phrase・mora・text のどれかが要る")


def text_key(text: str, occurrence: int = 1) -> str:
    return f"{text}#{occurrence}"
