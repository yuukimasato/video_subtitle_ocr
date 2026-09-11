# utils/secret_store.py
"""敏感配置（API Key 等）的安全存储。

写入策略（save_secret 返回实际使用的后端）：

1. ``keyring``（首选）：OS 级钥匙串（Linux gnome-keyring/KWallet、macOS
   Keychain、Windows 凭据管理器）。`keyring` 为可选依赖：未安装或运行时
   不可用（如无钥匙串守护进程的无头环境）时自动降级，不影响运行。
2. ``local``（回退）：密文写入 QSettings（INI 随配置文件走），加密密钥是
   独立保存在用户数据目录下的随机文件（权限 0600，不随配置同步/备份）。
   算法为 SHA-256 计数器模式密钥流 XOR + 摘要校验，仅标准库。

注意：``local`` 回退能防止配置文件被直接读取/同步/共享时泄露明文，但无法
防御能读取同一用户数据目录的本地攻击者——该场景请安装 `keyring` 使用系统
钥匙串（``pip install keyring``）。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets as pysecrets
import tempfile
from typing import Optional

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

SERVICE_NAME = "video-subtitle-ocr"
_CIPHER_PREFIX = "enc:v1:"
_MASTER_KEY_BYTES = 32

_keyring = None
try:  # optional dependency; degrade gracefully when missing/broken
    import keyring as _keyring_mod  # noqa: F401
    _keyring = _keyring_mod
except Exception:  # pragma: no cover - environment dependent
    _keyring = None


# ── master key management (local backend) ───────────────────────

def _master_key_path() -> str:
    data_home = os.environ.get("XDG_DATA_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(data_home, "video_subtitle_ocr", "secret.key")


def _load_or_create_master_key() -> Optional[bytes]:
    path = _master_key_path()
    try:
        with open(path, "rb") as f:
            key = f.read()
        if len(key) == _MASTER_KEY_BYTES:
            return key
        logger.warning("Secret store master key has unexpected size; recreating.")
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"Cannot read secret store master key: {e}")
        return None

    fresh = pysecrets.token_bytes(_MASTER_KEY_BYTES)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".secret_key_", dir=os.path.dirname(path))
        try:
            os.write(fd, fresh)
        finally:
            os.close(fd)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning(f"Cannot create secret store master key: {e}")
        return None
    return fresh


# ── primitives ──────────────────────────────────────────────────

def _keystream(key: bytes, iv: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + iv + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _encrypt_value(key: bytes, plaintext: str) -> str:
    data = plaintext.encode("utf-8")
    iv = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _keystream(key, iv, len(data))))
    digest = hashlib.sha256(key + iv + ct).digest()
    return (
        _CIPHER_PREFIX
        + base64.b64encode(iv).decode("ascii") + ":"
        + base64.b64encode(ct).decode("ascii") + ":"
        + base64.b64encode(digest).decode("ascii")
    )


def _decrypt_value(key: bytes, blob: str) -> Optional[str]:
    if not blob.startswith(_CIPHER_PREFIX):
        return None
    try:
        iv_b64, ct_b64, digest_b64 = blob[len(_CIPHER_PREFIX):].split(":")
        iv = base64.b64decode(iv_b64)
        ct = base64.b64decode(ct_b64)
        digest = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return None
    if hashlib.sha256(key + iv + ct).digest() != digest:
        logger.warning("Secret store integrity check failed; dropping stored secret.")
        return None
    data = bytes(a ^ b for a, b in zip(ct, _keystream(key, iv, len(ct))))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ── QSettings-backed ciphertext slot ────────────────────────────

def _settings() -> QSettings:
    s = QSettings()
    s.beginGroup("secrets")
    return s


# ── public API ──────────────────────────────────────────────────

def save_secret(name: str, value: str) -> str:
    """保存敏感值。空值等价于删除。返回实际使用的后端（"keyring"/"local"/""）。"""
    name = str(name)
    if not value:
        delete_secret(name)
        return ""

    if _keyring is not None:
        try:
            _keyring.set_password(SERVICE_NAME, name, value)
            # Prefer the keyring from now on: drop any older local ciphertext.
            _local_delete(name)
            return "keyring"
        except Exception as e:
            logger.warning(
                f"OS keyring unavailable ({e}); falling back to local encrypted storage."
            )

    key = _load_or_create_master_key()
    if key is None:
        logger.warning("No secure backend available; secret not persisted.")
        return ""
    s = _settings()
    try:
        s.setValue(name, _encrypt_value(key, value))
        s.sync()
    finally:
        s.endGroup()
    return "local"


def load_secret(name: str) -> Optional[str]:
    """读取敏感值；不存在或校验失败返回 None。"""
    name = str(name)
    if _keyring is not None:
        try:
            value = _keyring.get_password(SERVICE_NAME, name)
            if value:
                return str(value)
        except Exception as e:
            logger.warning(f"OS keyring read failed ({e}); trying local encrypted storage.")

    s = _settings()
    try:
        blob = str(s.value(name, "", type=str) or "")
    finally:
        s.endGroup()
    if not blob.startswith(_CIPHER_PREFIX):
        return None
    key = _load_or_create_master_key()
    if key is None:
        return None
    return _decrypt_value(key, blob)


def delete_secret(name: str) -> None:
    """从所有后端删除敏感值。"""
    name = str(name)
    if _keyring is not None:
        try:
            _keyring.delete_password(SERVICE_NAME, name)
        except Exception:
            pass  # not present / backend unavailable
    _local_delete(name)


def _local_delete(name: str) -> None:
    s = _settings()
    try:
        s.remove(name)
        s.sync()
    finally:
        s.endGroup()
