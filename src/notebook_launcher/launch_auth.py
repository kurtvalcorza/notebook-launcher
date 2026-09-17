from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .errors import InvalidLaunchToken, LaunchTokenExpired


@dataclass(frozen=True, slots=True)
class LaunchTokenClaims:
    jti: str
    request_digest: str
    issued_at: int
    expires_at: int


class LaunchTokenCodec:
    def __init__(self, secret: bytes, ttl_seconds: int = 300):
        if len(secret) < 32:
            raise ValueError("launch token secret must be at least 32 bytes")
        self._secret = secret
        self._ttl = ttl_seconds

    def issue(self, request_digest: str) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._ttl)
        payload = {
            "jti": secrets.token_urlsafe(24),
            "request_digest": request_digest,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        }
        body = self._b64(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        signature = self._sign(body.encode())
        return f"{body}.{signature}", expires

    def decode(self, token: str) -> LaunchTokenClaims:
        try:
            body, supplied_sig = token.split(".", 1)
        except ValueError as exc:
            raise InvalidLaunchToken() from exc
        expected_sig = self._sign(body.encode())
        if not hmac.compare_digest(supplied_sig, expected_sig):
            raise InvalidLaunchToken()
        try:
            payload = json.loads(self._unb64(body))
            jti = str(payload["jti"])
            digest = str(payload["request_digest"])
            issued_at = int(payload["iat"])
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidLaunchToken() from exc
        if expires_at < int(datetime.now(UTC).timestamp()):
            raise LaunchTokenExpired()
        return LaunchTokenClaims(
            jti=jti,
            request_digest=digest,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def hash_jti(self, jti: str) -> str:
        return hashlib.sha256(jti.encode()).hexdigest()

    def _sign(self, body: bytes) -> str:
        digest = hmac.new(self._secret, body, hashlib.sha256).digest()
        return self._b64(digest)

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    @staticmethod
    def _unb64(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)


def request_digest(normalized: dict[str, object]) -> str:
    encoded = json.dumps(
        normalized,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
