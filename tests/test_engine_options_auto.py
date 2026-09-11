# tests/test_engine_options_auto.py
"""Regression tests for engine options with the ""/"auto" engine id.

Before the fix, OcrOptimizer skipped set_engine entirely for ""/"auto",
so lang/model_tier never reached initialize() — auto-engine runs were
silently pinned to the version-default (medium) models regardless of
--model-tier.
"""

from __future__ import annotations

import pytest

from core import ocr_engine_manager as mgr
from core.ocr_engine_base import BaseOCREngine, OCREngineInfo
from core.ocr_optimizer import OcrOptimizer


class _SentinelEngine(BaseOCREngine):
    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="sentinel", name="Sentinel", version="0",
            description="live-engine double", supports_gpu=False)

    @classmethod
    def is_available(cls) -> bool:
        return True

    def initialize(self, **kwargs):
        pass

    def predict(self, img_input):
        return []

    def normalize_result(self, raw_result):
        return {}

    def cleanup(self) -> None:
        pass


@pytest.fixture
def fresh_manager(monkeypatch):
    monkeypatch.setattr(mgr, "_engine_instance", None)
    monkeypatch.setattr(mgr, "_current_engine_id", None)
    monkeypatch.setattr(mgr, "_engine_options", {})
    return mgr


def _optimizer(**kw):
    return OcrOptimizer(
        work_dir="", visualize=False, in_memory_mode=True,
        save_ocr_json=False, **kw)


def test_auto_engine_applies_options_when_uninitialized(fresh_manager):
    opt = _optimizer(ocr_engine_id="", engine_options={"lang": "ch", "model_tier": "tiny"})
    opt._ensure_engine_selected()
    assert fresh_manager._engine_options == {"lang": "ch", "model_tier": "tiny"}


def test_auto_engine_keeps_live_engine_untouched(fresh_manager, monkeypatch):
    live = _SentinelEngine()
    monkeypatch.setattr(mgr, "_engine_instance", live)
    monkeypatch.setattr(mgr, "_current_engine_id", "sentinel")
    monkeypatch.setattr(mgr, "_engine_options", {"lang": "japan"})

    opt = _optimizer(ocr_engine_id="", engine_options={"lang": "ch", "model_tier": "tiny"})
    opt._ensure_engine_selected()
    # The live engine (deliberately initialized elsewhere) wins: instance
    # preserved, its options untouched.
    assert fresh_manager.is_engine_initialized()
    assert fresh_manager._engine_instance is live
    assert fresh_manager._engine_options == {"lang": "japan"}


def test_concrete_engine_still_switches_with_options(fresh_manager):
    opt = _optimizer(ocr_engine_id="paddle", engine_options={"lang": "japan"})
    opt._ensure_engine_selected()
    assert fresh_manager.get_current_engine_id() == "paddle"
    assert fresh_manager._engine_options == {"lang": "japan"}
