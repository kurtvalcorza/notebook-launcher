from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DestinationDecision:
    allowed: bool
    reason: str


def _is_global_unicast(address: ipaddress._BaseAddress) -> bool:
    return bool(address.is_global and not address.is_multicast and not address.is_unspecified)


def classify_destination(
    value: str,
    *,
    approved_dns_endpoints: Iterable[str] = (),
) -> DestinationDecision:
    """Classify an IP destination under the default egress policy.

    Public globally-routable unicast destinations are allowed. Non-global,
    loopback, private, link-local, multicast, unspecified, and reserved/local
    destinations are denied unless the exact address is an approved DNS
    resolver endpoint.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return DestinationDecision(False, "invalid_ip")

    approved = {ipaddress.ip_address(item) for item in approved_dns_endpoints}
    if address in approved:
        return DestinationDecision(True, "approved_dns_endpoint")

    if address.is_loopback:
        return DestinationDecision(False, "loopback")
    if address.is_private:
        return DestinationDecision(False, "private")
    if address.is_link_local:
        return DestinationDecision(False, "link_local")
    if address.is_multicast:
        return DestinationDecision(False, "multicast")
    if address.is_unspecified:
        return DestinationDecision(False, "unspecified")
    if not _is_global_unicast(address):
        return DestinationDecision(False, "non_global")
    return DestinationDecision(True, "public_internet")


def validate_resolved_addresses(
    addresses: Iterable[str],
    *,
    approved_dns_endpoints: Iterable[str] = (),
) -> list[DestinationDecision]:
    """Classify every resolved IP; callers must reject if any target is denied."""
    return [
        classify_destination(value, approved_dns_endpoints=approved_dns_endpoints)
        for value in addresses
    ]


def all_destinations_allowed(
    addresses: Iterable[str],
    *,
    approved_dns_endpoints: Iterable[str] = (),
) -> bool:
    decisions = validate_resolved_addresses(
        addresses,
        approved_dns_endpoints=approved_dns_endpoints,
    )
    return bool(decisions) and all(item.allowed for item in decisions)
