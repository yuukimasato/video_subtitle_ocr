# tests/conftest.py
"""Shared pytest fixtures for the video_subtitle_ocr unit tests.

Notes:
- core/ocr_engine_base.py auto-registers engine adapters on import. Both
  adapters only import numpy at module level, so importing them is safe even
  without paddlepaddle/paddleocr/rapidocr installed (the heavy imports happen
  lazily inside is_available()/initialize()).
- core/ocr_optimizer.py imports cv2 / skimage / Levenshtein /
  PySide6.QtCore at module level; those are real dependencies of the test
  venv and are NOT stubbed here.
- openai / tenacity are required by core/llm_client.py. If the venv does not
  have them yet, minimal offline stubs are injected so the backoff-policy test
  can still verify the bounded-retry constants (see task spec: bounded
  backoff for 429 Too Many Requests).
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Optional dependency fallbacks (only injected when the real package is absent)
# ---------------------------------------------------------------------------

def _install_llm_stubs_if_needed() -> None:
    """Inject minimal openai/tenacity stubs when they are not installed.

    core.llm_client only needs: openai.OpenAI / openai.RateLimitError and the
    tenacity decorator building blocks used at import time.
    """
    try:
        import openai  # noqa: F401
    except ImportError:
        openai_stub = types_module.ModuleType("openai")

        class _OpenAI:  # pragma: no cover - never constructed in tests
            def __init__(self, *args, **kwargs):
                raise RuntimeError("Stub OpenAI client must not be constructed")

        class RateLimitError(Exception):
            pass

        openai_stub.OpenAI = _OpenAI
        openai_stub.RateLimitError = RateLimitError
        sys.modules["openai"] = openai_stub

    try:
        import tenacity  # noqa: F401
    except ImportError:
        tenacity_stub = types_module.ModuleType("tenacity")

        class _RetryPolicySpec:
            """Marker base for stubbed tenacity policy objects (supports ``|``)."""

            def __or__(self, other):
                stops = []
                for policy in (self, other):
                    if isinstance(policy, _StopAny):
                        stops.extend(policy.stops)
                    else:
                        stops.append(policy)
                return _StopAny(*stops)

        class stop_after_attempt(_RetryPolicySpec):
            def __init__(self, max_attempt_number):
                self.max_attempt_number = max_attempt_number

        class stop_after_delay(_RetryPolicySpec):
            def __init__(self, max_delay):
                self.max_delay = max_delay

        class _StopAny(_RetryPolicySpec):
            def __init__(self, *stops):
                self.stops = stops

        class wait_random_exponential(_RetryPolicySpec):
            def __init__(self, multiplier=1, min=0, max=None):
                self.multiplier = multiplier
                self.min = min
                self.max = max

        class retry_if_exception_type(_RetryPolicySpec):
            def __init__(self, exception_types=Exception):
                self.exception_types = exception_types

        def retry(*dargs, **dkw):
            """Identity decorator that records the retry policy on the function."""

            def decorator(fn):
                fn._retry_policy = dkw
                fn.retry = None  # real tenacity would attach a Retrying object
                return fn

            return decorator

        class RetryCallState:  # pragma: no cover - unused in tests
            pass

        tenacity_stub.retry = retry
        tenacity_stub.stop_after_attempt = stop_after_attempt
        tenacity_stub.stop_after_delay = stop_after_delay
        tenacity_stub.wait_random_exponential = wait_random_exponential
        tenacity_stub.retry_if_exception_type = retry_if_exception_type
        tenacity_stub.RetryCallState = RetryCallState
        sys.modules["tenacity"] = tenacity_stub


import types as types_module  # noqa: E402

_install_llm_stubs_if_needed()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def qcore_app():
    """Headless QCoreApplication for QCoreApplication.translate() calls."""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture(autouse=True)
def reset_engine_manager_state():
    """Reset ocr_engine_manager module-level singleton state around each test."""
    from core import ocr_engine_manager as mgr

    def _reset():
        if mgr._engine_instance is not None:
            try:
                mgr._engine_instance.cleanup()
            except Exception:
                pass
        mgr._engine_instance = None
        mgr._current_engine_id = None
        mgr._engine_options = {}
        for engine in getattr(mgr, "_lang_engines", {}).values():
            try:
                engine.cleanup()
            except Exception:
                pass
        mgr._lang_engines = {}

    _reset()
    yield
    _reset()
