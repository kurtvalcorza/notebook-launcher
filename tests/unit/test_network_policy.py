import pytest

from notebook_launcher.network import (
    all_destinations_allowed,
    classify_destination,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "fe80::1",
        "224.0.0.1",
    ],
)
def test_non_global_destinations_are_denied(address):
    assert not classify_destination(address).allowed


def test_public_internet_is_allowed():
    decision = classify_destination("8.8.8.8")
    assert decision.allowed
    assert decision.reason == "public_internet"


def test_explicit_dns_endpoint_exception():
    decision = classify_destination(
        "192.168.65.5",
        approved_dns_endpoints=["192.168.65.5"],
    )
    assert decision.allowed
    assert decision.reason == "approved_dns_endpoint"


def test_dns_answer_does_not_bypass_private_target_rule():
    assert not all_destinations_allowed(["93.184.216.34", "10.1.2.3"])


def test_empty_resolution_is_not_allowed():
    assert not all_destinations_allowed([])
