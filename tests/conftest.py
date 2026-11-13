"""テストの共通部品: 擬似 VOICEVOX と、一時的な作業フォルダ。"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from talkscript import io  # noqa: E402
from talkscript.testing import FakeVoicevox, make_workspace  # noqa: E402


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """同梱の既定値で作った一時的な作業フォルダ（demo の話者は「テスト」）。"""
    monkeypatch.setattr(io, "ROOT", None)
    make_workspace(tmp_path / "ws")
    return tmp_path / "ws"


@pytest.fixture
def vv():
    return FakeVoicevox()
