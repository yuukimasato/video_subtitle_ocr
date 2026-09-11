# tests/test_engine_manager.py
"""Unit tests for core/ocr_engine_manager.py (offline, dummy engine class)."""

from __future__ import annotations

import pytest

from core import ocr_engine_manager as mgr
from core.ocr_engine_base import BaseOCREngine, OCREngineInfo, OCREngineRegistry


class DummyEngine(BaseOCREngine):
    """Engine double that records initialize kwargs and cleanup calls."""

    last_init_kwargs: dict | None = None
    registry_get_calls: list = []

    def __init__(self):
        self.init_kwargs: dict | None = None
        self.cleanup_calls = 0

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="dummy",
            name="Dummy",
            version="0.0",
            description="test double",
            supports_gpu=False,
        )

    @classmethod
    def is_available(cls) -> bool:
        return True

    def initialize(self, **kwargs) -> None:
        self.init_kwargs = dict(kwargs)
        DummyEngine.last_init_kwargs = dict(kwargs)

    def predict(self, img_input):
        return []

    def normalize_result(self, raw_result):
        return {}

    def cleanup(self) -> None:
        self.cleanup_calls += 1


@pytest.fixture
def dummy_registry(monkeypatch):
    """Make the registry resolve any id to DummyEngine (recording lookups)."""
    DummyEngine.registry_get_calls = []

    def _fake_get(engine_id):
        DummyEngine.registry_get_calls.append(engine_id)
        return DummyEngine

    monkeypatch.setattr(OCREngineRegistry, "get", _fake_get)
    return DummyEngine


def test_set_engine_saves_options():
    mgr.set_engine("dummy", {"lang": "japan", "model_tier": "tiny"})
    assert mgr.get_current_engine_id() == "dummy"
    assert mgr._engine_options == {"lang": "japan", "model_tier": "tiny"}


def test_set_engine_without_options_clears_options():
    mgr.set_engine("dummy", {"lang": "ch"})
    assert mgr._engine_options == {"lang": "ch"}
    mgr.set_engine("dummy")
    assert mgr.get_current_engine_id() == "dummy"
    assert mgr._engine_options == {}


def test_get_engine_initializes_with_saved_options(dummy_registry):
    options = {"lang": "korean", "model_tier": None}
    mgr.set_engine("dummy", options)
    engine = mgr.get_engine()

    assert isinstance(engine, DummyEngine)
    # initialize(**options) must receive exactly the saved options.
    assert engine.init_kwargs == options
    assert DummyEngine.registry_get_calls == ["dummy"]
    # Singleton: second call returns the same instance without re-initializing.
    assert mgr.get_engine() is engine
    assert len(DummyEngine.registry_get_calls) == 1


def test_get_engine_without_options_initializes_empty_kwargs(dummy_registry):
    mgr.set_engine("dummy")
    engine = mgr.get_engine()
    assert engine.init_kwargs == {}


def test_repeated_set_engine_cleans_up_old_instance(dummy_registry):
    mgr.set_engine("dummy")
    first = mgr.get_engine()
    assert first.cleanup_calls == 0

    mgr.set_engine("dummy", {"lang": "en"})
    # Old instance is cleaned up immediately on engine switch.
    assert first.cleanup_calls == 1

    second = mgr.get_engine()
    assert second is not first
    assert second.init_kwargs == {"lang": "en"}


def test_set_engine_same_id_and_options_keeps_instance(dummy_registry):
    """Idempotent set_engine: every OcrOptimizer calls this on its first OCR;
    re-selecting the already-active engine must not reload the model."""
    mgr.set_engine("dummy", {"lang": "ch"})
    engine = mgr.get_engine()

    mgr.set_engine("dummy", {"lang": "ch"})
    assert engine.cleanup_calls == 0
    assert mgr.get_engine() is engine


def test_set_engine_swallows_cleanup_errors(dummy_registry):
    mgr.set_engine("dummy")
    engine = mgr.get_engine()

    def _boom():
        raise RuntimeError("cleanup failed")

    engine.cleanup = _boom  # type: ignore[method-assign]
    # Must not raise even if cleanup() fails.
    mgr.set_engine("dummy", {"lang": "ch"})


def test_get_engine_raises_when_no_engine_available(monkeypatch):
    monkeypatch.setattr(OCREngineRegistry, "get_default", classmethod(lambda cls: ""))
    mgr._current_engine_id = None
    with pytest.raises(RuntimeError):
        mgr.get_engine()


def test_get_engine_raises_for_unknown_engine_id(monkeypatch):
    monkeypatch.setattr(OCREngineRegistry, "get", classmethod(lambda cls, engine_id: None))
    mgr.set_engine("does_not_exist")
    with pytest.raises(RuntimeError, match="does_not_exist"):
        mgr.get_engine()
