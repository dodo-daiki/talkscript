"""ファイルの読み書きと形式の検査（検査 K1）。

データは dict のまま扱う（ファイル形式と 1 対 1）。置き場所は作業フォルダ（workspace.py）の中:
- 台本:           scripts/<id>.yaml
- ロボットの設定: config/robots/<id>.yaml
- ひな形:         config/gestures/<id>.yaml
- 組み立ての出力: build/<台本 id>/
- 実行の記録:     runs/<実行 ID>/
- 作業フォルダの設定: talkscript.toml
作業フォルダは set_workspace() で決める（コマンドは起動時に workspace.find_workspace() で探す）。
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib

import yaml

DEFAULTS = pathlib.Path(__file__).resolve().parent / "defaults"   # 同梱の既定値（talkscript init が写す）
ROOT = None            # 作業フォルダ
CONFIG = SCRIPTS = BUILD = RUNS = None


def set_workspace(path) -> pathlib.Path:
    global ROOT, CONFIG, SCRIPTS, BUILD, RUNS
    ROOT = pathlib.Path(path).resolve()
    CONFIG, SCRIPTS, BUILD, RUNS = ROOT / "config", ROOT / "scripts", ROOT / "build", ROOT / "runs"
    return ROOT


def require_workspace() -> pathlib.Path:
    if ROOT is None:
        raise FormatError("作業フォルダが決まっていない（talkscript init <フォルダ> で作るか、--workspace で指定する）")
    return ROOT

VOICE_KEYS = {"speaker", "speed", "pitch", "intonation", "volume", "pre_silence_s", "post_silence_s", "sampling_rate"}
DEFAULT_VOICE = {"speaker": {"name": "TBD", "style": "ノーマル"}, "speed": 1.0, "pitch": 0.0, "intonation": 1.0,
                 "volume": 1.0, "pre_silence_s": 0.1, "post_silence_s": 0.1, "sampling_rate": 24000}
DEFAULT_GAP_MS = 300          # 行の間の既定（提案値）
DEFAULT_LEAD_IN_MS = 500      # 最初の行の前の既定（提案値。動作を発話より先に始められるように）


class FormatError(Exception):
    pass


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: pathlib.Path) -> str:
    return sha256_bytes(pathlib.Path(p).read_bytes())


def read_yaml(p) -> dict:
    p = pathlib.Path(p)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise FormatError(f"{p.name}: YAML として読めない: {e}") from e
    if not isinstance(d, dict):
        raise FormatError(f"{p.name}: 中身が辞書でない")
    return d


def write_yaml(p, d: dict) -> None:
    pathlib.Path(p).write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False, width=140, default_flow_style=None), encoding="utf-8")


def read_json(p) -> dict:
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def write_json(p, d) -> None:
    pathlib.Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _fmt(d: dict, kind: str, name: str) -> None:
    f = d.get("format")
    if f != f"talkscript/{kind}@1":
        raise FormatError(f"{name}: format が talkscript/{kind}@1 でない（{f!r}）")


# --- 台本 ---------------------------------------------------------------------------------

def script_path(name_or_path: str) -> pathlib.Path:
    p = pathlib.Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return p.resolve()
    q = SCRIPTS / f"{name_or_path}.yaml"
    if q.exists():
        return q
    raise FormatError(f"台本 {name_or_path} が見つからない（{p} も {q} もない）")


def load_script(name_or_path: str) -> tuple[dict, pathlib.Path]:
    p = script_path(name_or_path)
    s = read_yaml(p)
    validate_script(s, p.stem)
    return s, p


def is_num(x) -> bool:
    """有限な数（NaN・無限大・真偽値は数とみなさない。NaN との比較は常に偽になり、範囲や速度の検査をすり抜けるため）。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def check_gesture_points(g: dict) -> list[str]:
    """ひな形の robots.<ロボット>.<関節> が [[ms, 度], …]（有限な数か TBD、時刻は増えていく）か。"""
    errs = []
    robots = g.get("robots")
    if not isinstance(robots, dict) or not robots:
        return ["robots（ロボットごとの関節の [ms, 度] の列）がない"]
    for r, joints in robots.items():
        if not isinstance(joints, dict):
            errs.append(f"robots.{r} が関節の辞書でない")
            continue
        for j, pts in joints.items():
            ok = isinstance(pts, list) and pts and all(
                isinstance(p, list) and len(p) == 2 and is_num(p[0]) and (is_num(p[1]) or is_tbd(p[1])) for p in pts)
            if not ok:
                errs.append(f"robots.{r}.{j} は [[ms, 度], …] の列（有限な数か TBD）")
            elif any(b[0] < a[0] for a, b in zip(pts, pts[1:])):
                errs.append(f"robots.{r}.{j} の時刻が増えていない")
    return errs


def _check_keyframe(kf, where: str, line_ids: set) -> list[str]:
    errs = []
    if not isinstance(kf, dict):
        return [f"{where}: キーフレームが辞書でない（{kf!r}）"]
    has_t, has_at = "t_ms" in kf, "at" in kf
    if has_t == has_at:
        errs.append(f"{where}: t_ms と at のどちらか 1 つを書く")
    if not is_num(kf.get("deg")):
        errs.append(f"{where}: deg（体の向きの角度、度）が有限な数でない（{kf.get('deg')!r}）")
    if has_t and not is_num(kf["t_ms"]):
        errs.append(f"{where}: t_ms が有限な数でない（{kf['t_ms']!r}）")
    if has_at:
        at = kf["at"]
        if not isinstance(at, dict) or "line" not in at:
            errs.append(f"{where}: at には line が要る")
        else:
            if at["line"] not in line_ids:
                errs.append(f"{where}: at.line {at['line']} がこのブロックにない")
            if sum(k in at for k in ("phrase", "mora", "text")) != 1:
                errs.append(f"{where}: at には phrase・mora・text のどれか 1 つを書く")
            if at.get("edge", "start") not in ("start", "end"):
                errs.append(f"{where}: at.edge は start か end")
            if "mora" in at:
                m = at["mora"]
                if not is_num(m) or m < 0:
                    errs.append(f"{where}: at.mora は 0 以上の有限な数")
                elif isinstance(m, float) and not m.is_integer() and at.get("edge", "start") != "start":
                    errs.append(f"{where}: 小数の at.mora（発話の中の位置）には edge を書かない")
    if "offset_ms" in kf and not is_num(kf["offset_ms"]):
        errs.append(f"{where}: offset_ms が有限な数でない（{kf['offset_ms']!r}）")
    return errs


def validate_script(s: dict, stem: str | None = None) -> None:
    """形式の検査（K1 のうち、ロボットの設定を見ないで済む部分）。違反はまとめて FormatError。"""
    errs = []
    try:
        _fmt(s, "script", "台本")
    except FormatError as e:
        errs.append(str(e))
    if stem and s.get("id") != stem:
        errs.append(f"id（{s.get('id')}）がファイル名（{stem}）と違う")
    v = s.get("voice", {})
    if not isinstance(v, dict) or set(v) - VOICE_KEYS:
        errs.append(f"voice の項目が違う（使える項目: {sorted(VOICE_KEYS)}）")
    if not isinstance(s.get("robots"), list) or not s["robots"]:
        errs.append("robots（使うロボットの一覧）がない")
    if s.get("purpose", "demo") not in ("demo", "experiment"):
        errs.append("purpose は demo か experiment")
    blocks = s.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errs.append("blocks がない")
        blocks = []
    seen_b, seen_l = set(), set()
    for b in blocks:
        bid = b.get("id")
        if not bid or bid in seen_b:
            errs.append(f"ブロックの id がないか重複（{bid}）")
        seen_b.add(bid)
        if b.get("start", "auto") not in ("auto", "operator"):
            errs.append(f"{bid}: start は auto か operator")
        lines = b.get("lines", []) or []
        ids = set()
        for ln in lines:
            lid = ln.get("id")
            if not lid or lid in seen_l:
                errs.append(f"{bid}: 行の id がないか重複（{lid}）。行の id は台本全体で一意にする")
            seen_l.add(lid)
            ids.add(lid)
            if not isinstance(ln.get("say"), str) or not ln["say"].strip():
                errs.append(f"{bid}.{lid}: say（発話の文）がない")
            if "voice" in ln and (not isinstance(ln["voice"], dict) or set(ln["voice"]) - VOICE_KEYS):
                errs.append(f"{bid}.{lid}: voice の項目が違う")
            for r in ln.get("reading", []) or []:
                if not isinstance(r, dict) or not r.get("text") or not r.get("kana"):
                    errs.append(f"{bid}.{lid}: reading は {{text: 文字, kana: 読み}} の列")
        tracks = b.get("tracks", {}) or {}
        if not isinstance(tracks, dict):
            errs.append(f"{bid}: tracks が辞書でない")
            tracks = {}
        for robot, joints in tracks.items():
            if robot not in s.get("robots", []):
                errs.append(f"{bid}: tracks のロボット {robot} が robots にない")
            for j, kfs in (joints or {}).items():
                if not isinstance(kfs, list):
                    errs.append(f"{bid}.{robot}.{j}: キーフレームの列でない")
                    continue
                for k, kf in enumerate(kfs):
                    errs += _check_keyframe(kf, f"{bid}.{robot}.{j}[{k}]", ids)
    if errs:
        raise FormatError("台本の形式の誤り:\n  " + "\n  ".join(errs))


def voice_for_line(s: dict, line: dict) -> dict:
    v = dict(DEFAULT_VOICE)
    v.update(s.get("voice", {}) or {})
    v.update(line.get("voice", {}) or {})
    sp = dict(DEFAULT_VOICE["speaker"])
    sp.update(v.get("speaker") or {})
    v["speaker"] = sp
    return v


def iter_lines(s: dict):
    for b in s["blocks"]:
        for ln in b.get("lines", []) or []:
            yield b, ln


# --- ロボットの設定・ひな形 -------------------------------------------------------------------

def is_tbd(x) -> bool:
    return x is None or (isinstance(x, str) and x.strip().upper() == "TBD")


def load_robot(name: str) -> dict:
    p = CONFIG / "robots" / f"{name}.yaml"
    if not p.exists():
        raise FormatError(f"ロボットの設定 {p} がない")
    r = read_yaml(p)
    _fmt(r, "robot", p.name)
    if r.get("id") != name:
        raise FormatError(f"{p.name}: id が {name} でない")
    if not isinstance(r.get("joints"), dict) or not r["joints"]:
        raise FormatError(f"{p.name}: joints がない")
    rest = r.get("rest", {})
    for j in r["joints"]:
        if j not in rest:
            raise FormatError(f"{p.name}: rest に {j} がない")
    return r


def load_gesture(name: str) -> dict:
    p = CONFIG / "gestures" / f"{name}.yaml"
    if not p.exists():
        have = sorted(x.stem for x in (CONFIG / "gestures").glob("*.yaml"))
        raise FormatError(f"ひな形 {name} がない（あるもの: {have}）")
    g = read_yaml(p)
    _fmt(g, "gesture", p.name)
    if g.get("id") != name:
        raise FormatError(f"{p.name}: id が {name} でない")
    errs = check_gesture_points(g)
    if errs:
        raise FormatError(f"{p.name}: " + "; ".join(errs))
    return g


def build_dir(script_id: str) -> pathlib.Path:
    d = BUILD / script_id
    d.mkdir(parents=True, exist_ok=True)
    return d
