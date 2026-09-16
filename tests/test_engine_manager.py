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
        self._ready = False

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
        self._ready = True

    def is_initialized(self) -> bool:
        return self._ready

    def predict(self, img_input):
        return []

    def normalize_result(self, raw_result):
        return {}

    def cleanup(self) -> None:
        self.cleanup_calls += 1
        self._ready = False


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


def test_build_standalone_engine_resolves_empty_id(monkeypatch):
    """Empty engine id must resolve via the registry, not raise NameError.

    build_standalone_engine used to call an undefined module-level
    get_default(), so every parallel-refinement thread started with an
    empty ctx.ocr_engine_id crashed (NameError) and the pipeline silently
    fell back to serial refinement.
    """
    monkeypatch.setattr(OCREngineRegistry, "get_default", classmethod(lambda cls: "dummy"))
    monkeypatch.setattr(OCREngineRegistry, "get", classmethod(lambda cls, engine_id: DummyEngine))
    engine = mgr.build_standalone_engine("", {"lang": "zh"})
    assert DummyEngine.registry_get_calls[-1] == "dummy"
    assert engine.init_kwargs == {"lang": "zh"}
    engine.cleanup()


def test_build_standalone_engine_resolves_auto_id(monkeypatch):
    """'auto' must resolve to the registry default like the singleton path."""
    monkeypatch.setattr(OCREngineRegistry, "get_default", classmethod(lambda cls: "dummy"))
    monkeypatch.setattr(OCREngineRegistry, "get", classmethod(lambda cls, engine_id: DummyEngine))
    engine = mgr.build_standalone_engine("auto", None)
    assert DummyEngine.registry_get_calls[-1] == "dummy"
    engine.cleanup()


def test_build_standalone_engine_keeps_explicit_id(monkeypatch):
    """An explicit engine id is passed through unchanged."""
    monkeypatch.setattr(OCREngineRegistry, "get", classmethod(lambda cls, engine_id: DummyEngine))
    engine = mgr.build_standalone_engine("dummy", None)
    assert DummyEngine.registry_get_calls[-1] == "dummy"
    engine.cleanup()


# ---------------------------------------------------------------------------
# Regression: standalone cleanup() must not brick the manager engine
# ---------------------------------------------------------------------------

class SingletonEngine(BaseOCREngine):
    """Engine double that forces a __new__ singleton.

    Mirrors the pre-fix PaddleOCREngine: every construction returns the SAME
    object, so a "standalone" instance obtained via build_standalone_engine()
    is the very object the manager caches, and cleanup() on it unloads the
    shared engine behind the manager's back.
    """

    _shared: "SingletonEngine | None" = None

    def __new__(cls):
        if SingletonEngine._shared is None:
            SingletonEngine._shared = super().__new__(cls)
        return SingletonEngine._shared

    def __init__(self):
        if getattr(self, "_ready", False):
            return
        self._ready = False

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="singleton",
            name="Singleton",
            version="0.0",
            description="test double with __new__ singleton",
            supports_gpu=False,
        )

    @classmethod
    def is_available(cls) -> bool:
        return True

    def initialize(self, **kwargs) -> None:
        self._ready = True

    def is_initialized(self) -> bool:
        return self._ready

    def predict(self, img_input):
        if not self._ready:
            raise RuntimeError("SingletonEngine not initialized")
        return []

    def normalize_result(self, raw_result):
        return {}

    def cleanup(self) -> None:
        self._ready = False


def test_manager_rebuilds_engine_after_external_cleanup(dummy_registry):
    """cleanup() through a foreign reference must not brick the manager.

    Historical failure: refine threads shared the __new__-singleton
    PaddleOCREngine and cleaned it up after boundary refinement, so the NEXT
    pipeline run crashed on its first predict() with "PaddleOCR engine not
    initialized". get_engine() must detect the dead cached instance and
    rebuild it.
    """
    mgr.set_engine("dummy", {"lang": "ch"})
    engine = mgr.get_engine()
    engine.cleanup()  # behind the manager's back

    rebuilt = mgr.get_engine()
    assert rebuilt is not engine
    assert rebuilt.init_kwargs == {"lang": "ch"}
    assert rebuilt.is_initialized()


def test_set_engine_idempotence_does_not_preserve_dead_instance(dummy_registry):
    """Same id + options must still rebuild when the cached instance is dead.

    The idempotence short-circuit in set_engine() used to keep the cleaned-up
    instance cached, so every later get_engine() kept returning it.
    """
    mgr.set_engine("dummy", {"lang": "ch"})
    engine = mgr.get_engine()
    engine.cleanup()

    mgr.set_engine("dummy", {"lang": "ch"})  # identical selection
    assert mgr.get_engine().is_initialized()


def test_singleton_engine_survives_standalone_cleanup(monkeypatch):
    """End-to-end replay of the paddle singleton scenario.

    With a __new__-singleton engine class, build_standalone_engine() hands
    out the same object the manager caches (the pre-fix paddle hazard);
    standalone cleanup() must not leave the manager holding a dead engine.
    """
    SingletonEngine._shared = None
    monkeypatch.setattr(
        OCREngineRegistry, "get", classmethod(lambda cls, engine_id: SingletonEngine)
    )
    mgr.set_engine("singleton", {"lang": "ch"})

    main = mgr.get_engine()
    standalone = mgr.build_standalone_engine("singleton", {"lang": "ch"})
    assert standalone is main  # premise: the singleton hazard exists here

    standalone.cleanup()  # what refine_executor did at thread teardown
    assert not main.is_initialized()

    healed = mgr.get_engine()
    assert healed.is_initialized()
    healed.predict(object())  # must not raise
