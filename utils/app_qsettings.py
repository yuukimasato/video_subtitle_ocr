# utils/app_qsettings.py
"""应用级 QSettings 键名与读写（需在 QApplication 中已设置 organization/application 名称）。"""
from __future__ import annotations

from PySide6.QtCore import QSettings

SETTINGS_GROUP = "llm"


def _settings() -> QSettings:
    s = QSettings()
    s.beginGroup(SETTINGS_GROUP)
    return s


def load_saved_llm() -> tuple[str, str, str, int]:
    """读取上次保存的 DeepSeek/LLM 相关字段（无记录则为空串 / 0）。"""
    s = _settings()
    try:
        key = s.value("api_key", "", type=str) or ""
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
    s = _settings()
    try:
        s.setValue("api_key", api_key)
        s.setValue("api_base", api_base)
        s.setValue("model", model)
        s.setValue("provider_index", int(provider_index))
    finally:
        s.endGroup()


def clear_saved_api_key() -> None:
    s = _settings()
    try:
        s.remove("api_key")
    finally:
        s.endGroup()
