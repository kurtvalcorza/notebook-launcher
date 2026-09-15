import pytest

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
    with pytest.raises(ValueError):
        codec.decode(body + "x." + signature)


def test_short_secret_is_rejected():
    with pytest.raises(ValueError):
        LaunchTokenCodec(b"short")
