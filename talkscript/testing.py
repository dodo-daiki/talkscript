"""試験用の部品（擬似 VOICEVOX と、一時的な作業フォルダ）。talkscript の試験と、Pi の agent の試験で使う。"""
from __future__ import annotations

import io as _io
import pathlib
import wave

from . import io, timing, workspace

PUNCT = "。、！？"


class FakeVoicevox:
    """1 文字 = 1 モーラ（子音 0.05 s・母音 0.1 s）。句読点で句を切り、間 0.3 s。4 モーラごとにも句を切る。
    合成は、timing.MODELS[0]（フレームに丸め・前後の無音も話速で割る）の長さの無音 WAV。"""

    def __init__(self):
        self.queries = []

    def version(self):
        return "0.0.0-fake"

    def speakers(self):
        return [{"name": "テスト", "styles": [{"name": "ノーマル", "id": 3}]}]

    def resolve(self, name, style="ノーマル"):
        from talkscript.voicevox import VoicevoxError
        if name != "テスト":
            raise VoicevoxError(f"話者「{name}」がない")
        return 3

    def audio_query(self, text, speaker):
        self.queries.append(text)
        phrases, cur = [], []
        for ch in text:
            if ch in PUNCT:
                if cur:
                    phrases.append({"moras": cur, "accent": 1,
                                    "pause_mora": {"text": "、", "vowel": "pau", "vowel_length": 0.3, "pitch": 0}})
                    cur = []
                continue
            cur.append({"text": ch, "consonant": "k", "consonant_length": 0.05, "vowel": "a", "vowel_length": 0.1,
                        "pitch": 5.0})
            if len(cur) == 4:
                phrases.append({"moras": cur, "accent": 1, "pause_mora": None})
                cur = []
        if cur:
            phrases.append({"moras": cur, "accent": 1, "pause_mora": None})
        return {"accent_phrases": phrases, "speedScale": 1.0, "pitchScale": 0.0, "intonationScale": 1.0,
                "volumeScale": 1.0, "prePhonemeLength": 0.1, "postPhonemeLength": 0.1, "outputSamplingRate": 24000,
                "outputStereo": False, "kana": ""}

    def synthesis(self, query, speaker):
        d = timing.total_duration(query, timing.MODELS[0])
        sr = int(query["outputSamplingRate"])
        n = round(d * sr)
        b = _io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(b"\x00\x00" * n)
        return b.getvalue()




def make_workspace(path: pathlib.Path) -> pathlib.Path:
    """同梱の既定値で作業フォルダを作り、demo の台本の話者を擬似 VOICEVOX の「テスト」にする。"""
    workspace.init_workspace(path)
    io.set_workspace(path)
    p = io.SCRIPTS / "demo.yaml"
    s = io.read_yaml(p)
    s["voice"]["speaker"]["name"] = "テスト"
    io.write_yaml(p, s)
    return path
