"""VOICEVOX ENGINE の HTTP クライアント（標準ライブラリだけ）。

使う API: GET /version、GET /speakers、POST /audio_query、POST /synthesis。
エンジンは手元の PC で動かす（既定 http://127.0.0.1:50021）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class VoicevoxError(Exception):
    pass


class VoicevoxClient:
    def __init__(self, url: str = "http://127.0.0.1:50021", timeout_s: float = 60.0):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self._speakers = None

    def _req(self, method: str, path: str, params: dict | None = None, body: bytes | None = None,
             content_type: str | None = None) -> bytes:
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(self.url + path + q, data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise VoicevoxError(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise VoicevoxError(f"VOICEVOX ENGINE（{self.url}）につながらない: {e}。エンジンを起動したか確かめる") from e

    def version(self) -> str:
        return json.loads(self._req("GET", "/version"))

    def speakers(self) -> list:
        if self._speakers is None:
            self._speakers = json.loads(self._req("GET", "/speakers"))
        return self._speakers

    def resolve(self, name: str, style: str = "ノーマル") -> int:
        """話者の名前とスタイルの名前 → スタイル ID。"""
        if not name or name == "TBD":
            raise VoicevoxError("話者が未決定（台本の voice.speaker.name が TBD）。talkscript voices で一覧を見て決める")
        sp = [s for s in self.speakers() if s["name"] == name]
        if not sp:
            raise VoicevoxError(f"話者「{name}」がない。候補: {', '.join(s['name'] for s in self.speakers())}")
        st = [x for x in sp[0]["styles"] if x["name"] == style]
        if not st:
            raise VoicevoxError(f"話者「{name}」にスタイル「{style}」がない。候補: {', '.join(x['name'] for x in sp[0]['styles'])}")
        return int(st[0]["id"])

    def audio_query(self, text: str, speaker: int) -> dict:
        return json.loads(self._req("POST", "/audio_query", {"text": text, "speaker": speaker}))

    def synthesis(self, query: dict, speaker: int) -> bytes:
        body = json.dumps(query, ensure_ascii=False).encode("utf-8")
        return self._req("POST", "/synthesis", {"speaker": speaker}, body, "application/json")
