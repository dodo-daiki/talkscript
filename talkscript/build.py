"""合成と固定。

出力（build/<台本 id>/）:
  <行>.query.json  合成に使った audio_query（声の値と読みの修正を反映したもの）
  <行>.wav         合成した音声（git 管理外。sha256 で照合）
  <行>.marks.json  モーラ・句の時刻（timing.py）と、台本の文字の位置（text_anchors）
  manifest.json    エンジンの版、話者 ID、行ごとの合成の鍵・sha256・長さ、固定したか

文字の位置（at: {text: …}）の求め方:
  文の先頭からその文字の手前まで・終わりまでの 2 つの部分文を audio_query にかけ、モーラの数を数える。
  部分文のモーラ列が文全体のモーラ列の先頭と一致することを確かめる（一致しなければエラーにし、phrase か mora で書くよう知らせる）。
  VOICEVOX 自身の辞書で数えるので、別の形態素解析器（pyopenjtalk など）との辞書の違いが起きない。

読みの修正（reading: [{text: 今日, kana: きょう}]）:
  合成の前に、文の中の text を kana に置き換えてから audio_query にかける（accent_phrases を差し替えるより簡単な方法。
  アクセントが変わりうるので、聞いて確かめる）。
"""
from __future__ import annotations

import datetime
import io as _io
import json
import wave

from . import io, timing
from .voicevox import VoicevoxClient

VOICE_TO_QUERY = {"speed": "speedScale", "pitch": "pitchScale", "intonation": "intonationScale", "volume": "volumeScale",
                  "pre_silence_s": "prePhonemeLength", "post_silence_s": "postPhonemeLength",
                  "sampling_rate": "outputSamplingRate"}


class BuildError(Exception):
    pass


def apply_reading(text: str, readings) -> str:
    for r in readings or []:
        text = text.replace(r["text"], r["kana"])
    return text


def apply_voice(query: dict, voice: dict) -> dict:
    q = dict(query)
    for k, qk in VOICE_TO_QUERY.items():
        if k in voice and voice[k] is not None:
            q[qk] = voice[k]
    return q


def speaker_label(voice: dict) -> str:
    return f"{voice['speaker'].get('name')}/{voice['speaker'].get('style', 'ノーマル')}"


def synth_key(line: dict, voice: dict, speaker_id: int, engine_version: str) -> str:
    body = {"say": line["say"], "reading": line.get("reading") or [], "speaker_id": speaker_id,
            "voice": {k: voice[k] for k in sorted(voice) if k != "speaker"}, "engine": engine_version}
    return io.sha256_bytes(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def wav_info(b: bytes) -> dict:
    with wave.open(_io.BytesIO(b)) as w:
        n, sr, ch, sw = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
    return {"samples": n, "sampling_rate": sr, "channels": ch, "sample_width": sw, "duration_s": n / sr}


def flat_mora_texts(query: dict) -> list[str]:
    return [m["text"] for ap in query["accent_phrases"] for m in ap["moras"]]


def text_anchors_wanted(script: dict) -> dict:
    """行 id → {(文字, 何回目)}。台本の tracks の at.text から集める。"""
    want = {}
    for b in script["blocks"]:
        for joints in (b.get("tracks") or {}).values():
            for kfs in (joints or {}).values():
                for kf in kfs:
                    at = kf.get("at") if isinstance(kf, dict) else None
                    if at and "text" in at:
                        want.setdefault(at["line"], set()).add((at["text"], int(at.get("occurrence", 1))))
    return want


def find_text(say: str, text: str, occurrence: int = 1) -> int:
    i = -1
    for _ in range(occurrence):
        i = say.find(text, i + 1)
        if i < 0:
            raise KeyError(f"「{text}」が文の中に{'' if occurrence == 1 else f' {occurrence} 回'}ない")
    return i


def align_text(client, speaker_id: int, line: dict, full_texts: list[str], text: str, occurrence: int = 1) -> dict:
    """文字の範囲 → モーラの範囲。失敗は {"error": 理由}（K4 で知らせる）。"""
    say, readings = line["say"], line.get("reading")
    try:
        i = find_text(say, text, occurrence)
    except KeyError as e:
        return {"error": str(e)}
    j = i + len(text)

    def count(prefix: str) -> int:
        if not prefix.strip():
            return 0
        q = client.audio_query(apply_reading(prefix, readings), speaker_id)
        t = flat_mora_texts(q)
        if t != full_texts[:len(t)]:
            raise ValueError(f"文の先頭「{prefix}」の読み {''.join(t)} が、文全体の読みの先頭 {''.join(full_texts[:len(t)])} と違う")
        return len(t)

    try:
        a, b = count(say[:i]), count(say[:j])
    except ValueError as e:
        return {"error": f"{e}。phrase（句の番号）か mora（モーラの番号）で書く（talkscript marks で番号が分かる）"}
    if b <= a:
        return {"error": f"「{text}」にモーラがない（記号だけ？）"}
    return {"mora_start": a, "mora_end": b - 1, "chars": [i, j], "kana": "".join(full_texts[a:b])}


def load_manifest(script_id: str) -> dict:
    p = io.BUILD / script_id / "manifest.json"
    if p.exists():
        return io.read_json(p)
    return {"format": "talkscript/manifest@1", "script": script_id, "frozen": False, "lines": {}, "speakers": {}}


def synth(script: dict, client: VoicevoxClient, *, force: bool = False, lines: list[str] | None = None, log=print) -> dict:
    """台本の全行（lines を渡せばその行だけ）を合成する。合成済みで鍵が同じ行は飛ばす（文字の位置だけ求め直す）。"""
    sid = script["id"]
    out = io.build_dir(sid)
    man = load_manifest(sid)
    ver = client.version()
    if man.get("engine_version") and man["engine_version"] != ver:
        msg = f"エンジンの版が固定したときと違う（{man['engine_version']} → {ver}）"
        if man.get("frozen"):
            raise BuildError(msg + "。固定済みなので合成しない")
        log("警告: " + msg)
    man["engine_version"] = ver
    want = text_anchors_wanted(script)
    for b, ln in io.iter_lines(script):
        lid = ln["id"]
        if lines and lid not in lines:
            continue
        voice = io.voice_for_line(script, ln)
        spk = client.resolve(voice["speaker"]["name"], voice["speaker"].get("style", "ノーマル"))
        man["speakers"][speaker_label(voice)] = spk
        key = synth_key(ln, voice, spk, ver)
        entry = man["lines"].get(lid)
        wav_p, q_p, m_p = out / f"{lid}.wav", out / f"{lid}.query.json", out / f"{lid}.marks.json"
        cached = entry and entry["key"] == key and wav_p.exists() and io.sha256_file(wav_p) == entry["wav_sha256"]
        if man.get("frozen") and not cached:
            raise BuildError(f"{lid}: 固定済みの台本の文・声・読みが変わった（または WAV がない）。固定を解く（talkscript freeze --undo）か元に戻す")
        if cached and not force:
            query, marks = io.read_json(q_p), io.read_json(m_p)
            wi = {"duration_s": entry["duration_s"]}
            log(f"{lid}: 合成済み（鍵が同じ）")
        else:
            query = apply_voice(client.audio_query(apply_reading(ln["say"], ln.get("reading")), spk), voice)
            wav = client.synthesis(query, spk)
            wi = wav_info(wav)
            tl = timing.fit_timeline(query, wi["duration_s"])
            wav_p.write_bytes(wav)
            io.write_json(q_p, query)
            marks = {"format": "talkscript/marks@1", "line": lid, "say": ln["say"], "timing": tl, "text_anchors": {}}
            man["lines"][lid] = {"key": key, "block": b["id"], "speaker": speaker_label(voice), "speaker_id": spk,
                                 "wav_sha256": io.sha256_bytes(wav), **wi, "timing_model": tl["model"],
                                 "synthesized_at": datetime.datetime.now().isoformat(timespec="seconds")}
            log(f"{lid}: 合成した {wi['duration_s']:.3f} s（話者 {speaker_label(voice)} = {spk}、時刻の式 {tl['model']}、"
                f"差 {tl['diff_s'] * 1000:+.1f} ms）")
        full = flat_mora_texts(query)
        for text, occ in sorted(want.get(lid, ())):
            k = timing.text_key(text, occ)
            if k not in marks["text_anchors"] or "error" in marks["text_anchors"][k]:
                marks["text_anchors"][k] = align_text(client, spk, ln, full, text, occ)
        io.write_json(m_p, marks)
        for k, v in marks["text_anchors"].items():
            if "error" in v:
                log(f"  {lid}: 文字の位置「{k}」が求められない: {v['error']}")
    io.write_json(out / "manifest.json", man)
    return man


def freeze(script: dict, *, undo: bool = False) -> dict:
    man = load_manifest(script["id"])
    if undo:
        man["frozen"] = False
        man.pop("frozen_at", None)
    else:
        missing = [ln["id"] for _, ln in io.iter_lines(script) if ln["id"] not in man["lines"]]
        if missing:
            raise BuildError(f"合成していない行がある: {missing}")
        stale = line_status(script, man)
        bad = [lid for lid, st in stale.items() if st != "ok"]
        if bad:
            raise BuildError(f"合成し直しが要る行がある: {bad}（talkscript synth）")
        man["frozen"] = True
        man["frozen_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    io.write_json(io.BUILD / script["id"] / "manifest.json", man)
    return man


def line_status(script: dict, man: dict) -> dict:
    """行 → "ok" / "unsynthesized" / "changed"（文・声・読みが合成のときと違う）/ "wav_missing" / "wav_changed"。"""
    st = {}
    d = io.BUILD / script["id"]
    for _, ln in io.iter_lines(script):
        lid = ln["id"]
        e = man["lines"].get(lid)
        if not e:
            st[lid] = "unsynthesized"
            continue
        voice = io.voice_for_line(script, ln)
        spk = man["speakers"].get(speaker_label(voice))
        if spk is None or synth_key(ln, voice, spk, man.get("engine_version", "")) != e["key"]:
            st[lid] = "changed"
        elif not (d / f"{lid}.wav").exists():
            st[lid] = "wav_missing"
        elif io.sha256_file(d / f"{lid}.wav") != e["wav_sha256"]:
            st[lid] = "wav_changed"
        else:
            st[lid] = "ok"
    return st


def load_marks(script_id: str, line_id: str) -> dict:
    p = io.BUILD / script_id / f"{line_id}.marks.json"
    if not p.exists():
        raise BuildError(f"{line_id}: 合成していない（talkscript synth）")
    return io.read_json(p)


def marks_table(script: dict, line_id: str | None = None) -> str:
    """句の表（talkscript marks）。"""
    rows = []
    for _, ln in io.iter_lines(script):
        if line_id and ln["id"] != line_id:
            continue
        try:
            m = load_marks(script["id"], ln["id"])
        except BuildError as e:
            rows.append(f"{ln['id']}: {e}")
            continue
        rows.append(f"{ln['id']}  {ln['say']}  （{m['timing']['duration']:.3f} s、式 {m['timing']['model']}）")
        rows.append("  句  モーラ    開始      終了      カナ")
        for p in m["timing"]["phrases"]:
            rows.append(f"  {p['i']:>2}  {p['mora_start']:>2}-{p['mora_end']:<3}  {p['start']:7.3f}  {p['end']:7.3f}  {p['kana']}"
                        f"{'  （間）' if p['pause_after'] else ''}")
        for k, v in m.get("text_anchors", {}).items():
            rows.append(f"  文字「{k}」: " + (v["error"] if "error" in v else f"モーラ {v['mora_start']}〜{v['mora_end']}（{v['kana']}）"))
    return "\n".join(rows)
