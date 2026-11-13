"""Windows のインストーラー（talkscript-setup.exe）の入口: 中の ps1 に引数を渡すこと、--wait-pid で待つこと。"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows だけ（csc.exe で作る）")


@pytest.fixture(scope="module")
def setup_exe(tmp_path_factory):
    import build_setup
    d = tmp_path_factory.mktemp("setup")
    ps1 = d / "fake.ps1"
    ps1.write_text('\ufeffparam([switch]$Update, [string]$Source = "")\n'
                   'Write-Host "ARGS Update=$Update Source=[$Source]"\nexit 7\n', encoding="utf-8")
    assert build_setup.main([str(d / "out"), "--script", str(ps1)]) == 0
    return d / "out" / "talkscript-setup.exe"


def test_passes_args_and_exit_code(setup_exe):
    r = subprocess.run([str(setup_exe), "-Update", "-Source", r"C:\a b"], input=b"\n", capture_output=True, timeout=60)
    out = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 7 and "ARGS Update=True Source=[C:\\a b]" in out


def test_waits_for_pid(setup_exe):
    sleeper = subprocess.Popen(["powershell", "-NoProfile", "-Command", "Start-Sleep -Seconds 3"])
    t0 = time.monotonic()
    r = subprocess.run([str(setup_exe), "--wait-pid", f"{sleeper.pid},999999", "-Update"], input=b"\n",
                       capture_output=True, timeout=60)
    took = time.monotonic() - t0
    out = r.stdout.decode("utf-8", "replace")
    assert took >= 2.0, took                                    # 終わるまで待った
    assert "ARGS Update=True" in out and "--wait-pid" not in out
    assert r.returncode == 7
