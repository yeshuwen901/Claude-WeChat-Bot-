"""API key encryption at rest using Fernet (AES-128-CBC + HMAC).

The encryption key is persisted in data/.keyfile. If someone steals the .db
without the keyfile, the stored keys are unreadable. Both files live in the
same data/ directory (already gitignored), so this raises the bar from
"accidental leak via copying .db" without adding operational complexity.
"""

import base64
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

KEY_FILENAME = ".keyfile"


def _key_path() -> Path:
    from config import config
    return Path(config.data_dir) / KEY_FILENAME


def _load_or_create_key() -> bytes:
    kp = _key_path()
    if kp.exists():
        return kp.read_bytes()
    key = Fernet.generate_key()
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_bytes(key)
    try:
        os.chmod(kp, 0o600)
    except OSError:
        pass
    return key


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def _looks_encrypted(token: str) -> bool:
    """Heuristic: Fernet tokens are long URL-safe base64 strings with a version byte."""
    if len(token) < 80:
        return False
    try:
        base64.urlsafe_b64decode(token.encode("ascii"))
        return True
    except Exception:
        return False


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string, returning a base64 token."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to plaintext.

    Raises InvalidToken if the token is encrypted but the key is wrong or
    the data is corrupted. Returns the token unchanged only when it clearly
    looks like plaintext (migration path for existing unencrypted databases).
    """
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        if not _looks_encrypted(token):
            logger.warning(
                "Database contains a plaintext (unencrypted) API key. "
                "Re-save the key in the admin panel to encrypt it at rest."
            )
            return token
        raise
