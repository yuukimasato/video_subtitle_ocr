# tests/test_secret_store.py
"""Tests for the secure API-key storage (utils/secret_store + app_qsettings).

Qt resolves (and caches) the QSettings file location once per process, so the
XDG redirection has to be in place before the first QSettings is constructed
and must point at ONE session-wide directory; each test then scrubs the state
left over from the previous one.
"""

from __future__ import annotations

import os
import tempfile
import types

import pytest

from utils import secret_store

# Redirect before any QSettings can be constructed in this process.
_SESSION_TMP = tempfile.mkdtemp(prefix="vso_secret_test_")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SESSION_TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_SESSION_TMP, "data")


@pytest.fixture(autouse=True)
def isolated_env():
    """Clean QSettings groups + master key so each test starts from scratch."""
    from PySide6.QtCore import QCoreApplication, QSettings

    QCoreApplication.setOrganizationName("VSOCTest")
    QCoreApplication.setApplicationName("VSOCTest")

    s = QSettings()
    for group in ("secrets", "llm"):
        s.beginGroup(group)
        s.remove("")
        s.endGroup()
    s.sync()

    key_path = secret_store._master_key_path()
    if os.path.exists(key_path):
        os.remove(key_path)

    # Default to the local (encrypted) backend; keyring tests monkeypatch it.
    secret_store._keyring = None
    yield _SESSION_TMP


def _ini_text() -> str:
    from PySide6.QtCore import QSettings

    ini = QSettings().fileName()
    try:
        with open(ini, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# ── local encrypted backend ─────────────────────────────────────

def test_local_backend_roundtrip(isolated_env):
    backend = secret_store.save_secret("llm_api_key", "sk-secret-123")
    assert backend == "local"
    assert secret_store.load_secret("llm_api_key") == "sk-secret-123"


def test_stored_value_is_not_plaintext(isolated_env):
    secret_store.save_secret("llm_api_key", "sk-plaintext-must-not-appear")
    ini = _ini_text()
    assert "sk-plaintext-must-not-appear" not in ini
    assert "enc:v1:" in ini


def test_master_key_file_permissions(isolated_env):
    secret_store.save_secret("llm_api_key", "sk-x")
    key_path = secret_store._master_key_path()
    assert os.path.isfile(key_path)
    assert (os.stat(key_path).st_mode & 0o777) == 0o600


def test_tampered_ciphertext_is_rejected(isolated_env):
    from PySide6.QtCore import QSettings

    secret_store.save_secret("llm_api_key", "sk-original")
    # Flip the trailing digest bytes of the stored blob.
    s = QSettings()
    s.beginGroup("secrets")
    try:
        blob = s.value("llm_api_key", "", type=str)
        tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
        s.setValue("llm_api_key", tampered)
        s.sync()
    finally:
        s.endGroup()
    assert secret_store.load_secret("llm_api_key") is None


def test_delete_secret_removes_everything(isolated_env):
    secret_store.save_secret("llm_api_key", "sk-to-delete")
    secret_store.delete_secret("llm_api_key")
    assert secret_store.load_secret("llm_api_key") is None
    assert "llm_api_key" not in _ini_text()


def test_save_empty_deletes(isolated_env):
    secret_store.save_secret("llm_api_key", "sk-temp")
    assert secret_store.save_secret("llm_api_key", "") == ""
    assert secret_store.load_secret("llm_api_key") is None


# ── keyring backend (fake module) ───────────────────────────────

def test_keyring_backend_preferred(isolated_env, monkeypatch):
    store: dict = {}

    fake = types.SimpleNamespace(
        set_password=lambda service, name, value: store.__setitem__((service, name), value),
        get_password=lambda service, name: store.get((service, name)),
        delete_password=lambda service, name: store.pop((service, name), None),
    )
    monkeypatch.setattr(secret_store, "_keyring", fake)

    assert secret_store.save_secret("llm_api_key", "sk-keyring") == "keyring"
    assert store[(secret_store.SERVICE_NAME, "llm_api_key")] == "sk-keyring"
    assert secret_store.load_secret("llm_api_key") == "sk-keyring"
    # Nothing should have been persisted locally (no ciphertext in the INI).
    assert "secrets" not in _ini_text()


def test_keyring_failure_falls_back_to_local(isolated_env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no keyring daemon")

    fake = types.SimpleNamespace(
        set_password=boom,
        get_password=boom,
        delete_password=boom,
    )
    monkeypatch.setattr(secret_store, "_keyring", fake)
    assert secret_store.save_secret("llm_api_key", "sk-fallback") == "local"
    assert secret_store.load_secret("llm_api_key") == "sk-fallback"


# ── app_qsettings integration & legacy migration ────────────────

def test_save_and_load_llm_key_via_secret_store(isolated_env):
    from utils.app_qsettings import clear_saved_api_key, load_saved_llm, save_llm

    save_llm(api_key="sk-integration", api_base="https://api.deepseek.com", model="m", provider_index=1)
    key, base, model, prov = load_saved_llm()
    assert key == "sk-integration"
    assert base == "https://api.deepseek.com"
    assert model == "m"
    assert prov == 1
    assert "sk-integration" not in _ini_text()

    clear_saved_api_key()
    key, _, _, _ = load_saved_llm()
    assert key == ""


def test_legacy_plaintext_key_is_migrated_and_scrubbed(isolated_env):
    from PySide6.QtCore import QSettings

    from utils.app_qsettings import load_saved_llm

    # Simulate an old-version config: plaintext key inside the llm group.
    s = QSettings()
    s.beginGroup("llm")
    s.setValue("api_key", "sk-legacy-plaintext")
    s.setValue("api_base", "https://api.deepseek.com")
    s.sync()
    s.endGroup()
    assert "sk-legacy-plaintext" in _ini_text()

    key, base, _, _ = load_saved_llm()
    assert key == "sk-legacy-plaintext"
    assert base == "https://api.deepseek.com"
    # Plaintext must be gone from the INI; only the encrypted blob remains.
    ini = _ini_text()
    assert "sk-legacy-plaintext" not in ini
    # And a second load reads it back from the secure store.
    assert load_saved_llm()[0] == "sk-legacy-plaintext"


def test_save_llm_scrubs_leftover_plaintext(isolated_env):
    from PySide6.QtCore import QSettings

    from utils.app_qsettings import save_llm

    s = QSettings()
    s.beginGroup("llm")
    s.setValue("api_key", "sk-old-plaintext")
    s.sync()
    s.endGroup()

    save_llm(api_key="sk-new", api_base="b", model="m", provider_index=0)
    ini = _ini_text()
    assert "sk-old-plaintext" not in ini
    assert "sk-new" not in ini
