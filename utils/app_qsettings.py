# utils/app_qsettings.py
"""应用级 QSettings 键名与读写（需在 QApplication 中已设置 organization/application 名称）。

API Key 一律经 utils.secret_store 保存（系统钥匙串优先，回退本地加密），
QSettings INI 中不再出现明文密钥；读取时自动迁移历史明文并从 INI 清除。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QSettings

from utils import secret_store

logger = logging.getLogger(__name__)

SETTINGS_GROUP = "llm"
API_KEY_SECRET_NAME = "llm_api_key"


def _settings() -> QSettings:
    s = QSettings()
    s.beginGroup(SETTINGS_GROUP)
    return s


def _load_legacy_plaintext_key() -> str:
    """历史版本的明文 api_key（若存在），用于一次性迁移。"""
    s = _settings()
    try:
        raw = s.value("api_key", "", type=str) or ""
    finally:
        s.endGroup()
    raw = str(raw)
    if not raw or raw.startswith("enc:"):
        # enc: 前缀密文由 secret_store 的本地后端管理，不算明文遗留。
        return ""
    return raw


def _scrub_legacy_key() -> None:
    s = _settings()
    try:
        s.remove("api_key")
        s.sync()
    finally:
        s.endGroup()


def load_saved_llm() -> tuple[str, str, str, int]:
    """读取上次保存的 DeepSeek/LLM 相关字段（无记录则为空串 / 0）。"""
    key = secret_store.load_secret(API_KEY_SECRET_NAME) or ""

    if not key:
        # 一次性迁移：老版本把明文 key 写在 INI 里。
        legacy = _load_legacy_plaintext_key()
        if legacy:
            backend = secret_store.save_secret(API_KEY_SECRET_NAME, legacy)
            _scrub_legacy_key()
            if backend:
                key = legacy
                logger.info(
                    "Saved API key migrated from plaintext settings to secure store (%s).",
                    backend,
                )
            else:
                logger.warning(
                    "Plaintext API key found in settings but no secure backend is "
                    "available; it was removed from the INI file and not persisted."
                )

    s = _settings()
    try:
        base = s.value("api_base", "", type=str) or ""
        model = s.value("model", "", type=str) or ""
        prov = int(s.value("provider_index", 0, type=int) or 0)
    finally:
        s.endGroup()
    return (str(key), str(base), str(model), prov)


def save_llm(
    *,
    api_key: str,
    api_base: str,
    model: str,
    provider_index: int,
) -> None:
    # 密钥走安全存储；INI 中残留的明文（历史版本写入）一并清除。
    if api_key:
        secret_store.save_secret(API_KEY_SECRET_NAME, api_key)
    else:
        secret_store.delete_secret(API_KEY_SECRET_NAME)
    _scrub_legacy_key()

    s = _settings()
    try:
        s.setValue("api_base", api_base)
        s.setValue("model", model)
        s.setValue("provider_index", int(provider_index))
        s.sync()
    finally:
        s.endGroup()


def clear_saved_api_key() -> None:
    secret_store.delete_secret(API_KEY_SECRET_NAME)
    _scrub_legacy_key()
