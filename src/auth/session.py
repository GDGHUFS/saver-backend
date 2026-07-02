import base64
import hashlib
import hmac
import time


SESSION_COOKIE_NAME = "saver_session"


class InvalidSession(ValueError):
    """Raised when a signed session cookie cannot be trusted."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_session_cookie(
    user_id: int,
    secret: str,
    max_age: int,
    *,
    now: int | None = None,
) -> str:
    if user_id < 0 or max_age <= 0 or not secret:
        raise ValueError("Invalid session configuration")

    expires_at = (int(time.time()) if now is None else now) + max_age
    payload = f"{user_id}:{expires_at}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_encode(payload)}.{_encode(signature)}"


def read_session_cookie(
    value: str,
    secret: str,
    *,
    now: int | None = None,
) -> int:
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload = _decode(encoded_payload)
        signature = _decode(encoded_signature)
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidSession("Invalid signature")

        user_id_text, expires_at_text = payload.decode("ascii").split(":", 1)
        user_id = int(user_id_text)
        expires_at = int(expires_at_text)
    except (UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, InvalidSession):
            raise
        raise InvalidSession("Malformed session") from exc

    current_time = int(time.time()) if now is None else now
    if user_id < 0 or expires_at <= current_time:
        raise InvalidSession("Expired session")
    return user_id
