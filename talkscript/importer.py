"""ほかで作った台本・動作のひな形（YAML）を、作業フォルダに取り込む（画面の「読み込む」と、talkscript import）。

取り込めるもの（ファイルの format で見分ける）:
  talkscript/script@1   台本 → scripts/<id>.yaml
  talkscript/gesture@1  動作のひな形 → config/gestures/<id>.yaml
ファイルの文はそのまま書く（コメントも残す）。名前（id）を変えたときだけ、先頭の id: の行を書き換える。
同じ名前のものがあれば、上書きするか別の名前にするかを選ぶ（上書きの前の内容は <名前>.yaml.bak に残す）。
取り込む前に形式を検査し、誤りがあれば取り込まない。作業フォルダにないロボット・ひな形を使っていれば注意を返す。
"""
from __future__ import annotations

import pathlib
import re

import yaml

from . import io

KINDS = {"talkscript/script@1": "script", "talkscript/gesture@1": "gesture"}
ID_OK = re.compile(r"[A-Za-z0-9_-]+")


class ImportError_(Exception):
    pass


class Exists(ImportError_):
    def __init__(self, kind: str, sid: str):
        super().__init__(f"{'台本' if kind == 'script' else 'ひな形'} {sid} はもうある")
        self.kind, self.id = kind, sid


def _dest(kind: str, sid: str) -> pathlib.Path:
    return (io.SCRIPTS if kind == "script" else io.CONFIG / "gestures") / f"{sid}.yaml"


def _set_id(text: str, sid: str) -> str:
    """先頭の階層の id: の行だけを書き換える（なければ format: の次に足す）。コメントと並びは残す。"""
    pat = re.compile(r"^id:[^\n#]*", re.M)
    if pat.search(text):
        return pat.sub(f"id: {sid}", text, count=1)
    return re.sub(r"^(format:[^\n]*\n)", rf"\1id: {sid}\n", text, count=1, flags=re.M)


def _check_gesture(g: dict) -> list[str]:
    return io.check_gesture_points(g)


def inspect(text: str, filename: str = "") -> dict:
    """ファイルを読み、種類・名前・形式の誤りを返す（書かない）。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ImportError_(f"YAML として読めない: {e}") from e
    if not isinstance(data, dict):
        raise ImportError_("台本・ひな形の YAML でない（先頭が辞書でない）")
    kind = KINDS.get(data.get("format"))
    if kind is None:
        raise ImportError_(f"format が {' か '.join(KINDS)} でない（{data.get('format')!r}）")
    sid = str(data.get("id") or pathlib.Path(filename).stem)
    return {"kind": kind, "id": sid, "data": data}


def import_text(text: str, filename: str = "", *, new_id: str | None = None, overwrite: bool = False) -> dict:
    """取り込む。戻り値: {kind, id, path, warnings, replaced}。同じ名前があり overwrite でなければ Exists。"""
    if io.ROOT is None:
        raise ImportError_("作業フォルダが決まっていない")
    text = text.lstrip("﻿").replace("\r\n", "\n")
    info = inspect(text, filename)
    kind, sid = info["kind"], (new_id or info["id"]).strip()
    if not ID_OK.fullmatch(sid):
        raise ImportError_(f"名前（{sid}）は英数字と - _ だけ")
    if sid != info["data"].get("id"):
        text = _set_id(text, sid)
    data = yaml.safe_load(text)
    if kind == "script":
        try:
            io.validate_script(data, sid)
        except io.FormatError as e:
            raise ImportError_(f"台本の形式に誤りがある:\n{e}") from e
    else:
        errs = _check_gesture(data)
        if data.get("id") != sid:
            errs.append(f"id が {sid} でない")
        if errs:
            raise ImportError_("ひな形の形式に誤りがある:\n" + "\n".join(errs))
    dest = _dest(kind, sid)
    if dest.exists() and not overwrite:
        raise Exists(kind, sid)
    warnings = []
    have_robots = {p.stem for p in (io.CONFIG / "robots").glob("*.yaml")}
    used = data.get("robots") if kind == "script" else list((data.get("robots") or {}).keys())
    missing = [r for r in used or [] if r not in have_robots]
    if missing:
        warnings.append(f"この作業フォルダにないロボット: {', '.join(missing)}（config/robots/ に設定を置くまで検査で止まる）")
    if kind == "script":
        have_g = {p.stem for p in (io.CONFIG / "gestures").glob("*.yaml")}
        used_g = {g.get("gesture") for b in data.get("blocks") or [] for g in (b.get("groups") or {}).values() if isinstance(g, dict)}
        miss_g = sorted(x for x in used_g if x and x not in have_g)
        if miss_g:
            warnings.append(f"この作業フォルダにないひな形の名前が groups にある: {', '.join(miss_g)}（動きはそのまま使える）")
        if io.is_tbd(((data.get("voice") or {}).get("speaker") or {}).get("name")):
            warnings.append("話者が未決定（TBD）。声の欄で選ぶ")
    replaced = dest.exists()
    if replaced:
        dest.with_name(dest.name + ".bak").write_bytes(dest.read_bytes())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    return {"kind": kind, "id": sid, "path": str(dest), "warnings": warnings, "replaced": replaced}
