import pytest

from notebook_launcher.errors import InvalidLaunchToken, LaunchTokenExpired
from notebook_launcher.launch_auth import LaunchTokenCodec, request_digest


def test_launch_token_is_request_bound():
    codec = LaunchTokenCodec(b"x" * 32)
    first = request_digest({"repo": "a/b", "gpu": "off"})
    second = request_digest({"repo": "a/b", "gpu": "on"})
    token, _ = codec.issue(first)
    claims = codec.decode(token)
    assert claims.request_digest == first
    assert claims.request_digest != second


def test_tampered_launch_token_is_rejected():
    codec = LaunchTokenCodec(b"x" * 32)
    token, _ = codec.issue("abc")
    body, signature = token.split(".", 1)
    with pytest.raises(InvalidLaunchToken):
        codec.decode(body + "x." + signature)


def test_expired_launch_token_has_stable_error_code():
    codec = LaunchTokenCodec(b"x" * 32, ttl_seconds=-1)
    token, _ = codec.issue("abc")

    with pytest.raises(LaunchTokenExpired) as caught:
        codec.decode(token)

    assert caught.value.code == "launch_token_expired"


def test_short_secret_is_rejected():
    with pytest.raises(ValueError):
        LaunchTokenCodec(b"short")
