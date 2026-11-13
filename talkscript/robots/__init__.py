"""ロボットのアダプタ。"""
from .base import RobotAdapter, AdapterError


def make_adapter(name: str, cfg: dict, settings: dict, on_event):
    kind = cfg.get("adapter", name)
    if kind == "self7":
        from .self7 import Self7Adapter
        return Self7Adapter(cfg, settings, on_event)
    if kind == "sota":
        from .sota import SotaAdapter
        return SotaAdapter(cfg, settings, on_event)
    if kind == "fake":
        from .fake import FakeAdapter
        return FakeAdapter(cfg, settings, on_event)
    raise AdapterError(f"知らないアダプタ {kind}")


__all__ = ["RobotAdapter", "AdapterError", "make_adapter"]
