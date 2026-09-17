from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .errors import ExecutionTimeout
from .orchestration import CommandResult, run_argv

Runner = Callable[..., CommandResult]

IPV4_DENY_CIDRS = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    # 192.0.0.9 and 192.0.0.10 are globally reachable protocol-assignment
    # anycast addresses; partition the surrounding non-global /24 around them.
    "192.0.0.0/29",
    "192.0.0.8/32",
    "192.0.0.11/32",
    "192.0.0.12/30",
    "192.0.0.16/28",
    "192.0.0.32/27",
    "192.0.0.64/26",
    "192.0.0.128/25",
    "192.0.2.0/24",
    # IANA IPv4 Special-Purpose Address Registry: the deprecated 6to4 Relay
    # Anycast block is reserved and not globally reachable.  Deny the whole
    # allocation even on Python releases that classify parts of it as global.
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
)

_NETWORK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_INTERFACE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_NETWORK_ID = re.compile(r"^[0-9a-f]{64}$")
_NETWORK_PLAN_LABEL = "io.notebook-launcher.plan"


@dataclass(frozen=True, slots=True)
class DestinationDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class FirewallPlan:
    network_name: str
    bridge_name: str
    ipv4_subnet: str
    ipv6_subnet: str | None = None
    approved_dns_endpoints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _NETWORK_NAME.fullmatch(self.network_name):
            raise ValueError("invalid Docker network name")
        if not _INTERFACE_NAME.fullmatch(self.bridge_name):
            raise ValueError("invalid bridge interface name")
        ipv4 = ipaddress.ip_network(self.ipv4_subnet, strict=True)
        if ipv4.version != 4 or ipv4.is_global:
            raise ValueError("runtime IPv4 subnet must be non-global IPv4")
        if self.ipv6_subnet is not None:
            raise ValueError(
                "IPv6 runtime networking is disabled until its deny policy is complete"
            )
        for endpoint in self.approved_dns_endpoints:
            if ipaddress.ip_address(endpoint).version != 4:
                raise ValueError("approved DNS endpoints must be IPv4")


@dataclass(frozen=True, slots=True)
class NetworkPolicyAttestation:
    network_name: str
    bridge_name: str
    verified: bool
    commands_verified: int
    deny_non_global: bool = True
    dns_exceptions_port_53_only: bool = True
    host_gateway_alias_present: bool = False
    verification_commands: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class _NetworkIdentity:
    network_id: str
    plan_identity: str


class NetworkPolicyError(RuntimeError):
    pass


IANA_NON_GLOBAL_NETWORKS = (
    ipaddress.ip_network("192.88.99.0/24"),
)


def _is_global_unicast(address: ipaddress._BaseAddress) -> bool:
    if any(address in network for network in IANA_NON_GLOBAL_NETWORKS):
        return False
    return bool(
        address.is_global and not address.is_multicast and not address.is_unspecified
    )


def _normalized_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


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
        address = _normalized_address(value)
    except ValueError:
        return DestinationDecision(False, "invalid_ip")

    try:
        approved = {_normalized_address(item) for item in approved_dns_endpoints}
    except ValueError:
        return DestinationDecision(False, "invalid_dns_endpoint")
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


def docker_network_create_argv(plan: FirewallPlan) -> tuple[str, ...]:
    argv = [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--opt",
        f"com.docker.network.bridge.name={plan.bridge_name}",
        "--label",
        f"{_NETWORK_PLAN_LABEL}={network_plan_identity(plan)}",
        "--subnet",
        plan.ipv4_subnet,
    ]
    argv.append(plan.network_name)
    return tuple(argv)


def docker_network_inspect_argv(plan: FirewallPlan) -> tuple[str, ...]:
    identity_template = (
        f'{{{{.Id}}}}\t{{{{ index .Labels "{_NETWORK_PLAN_LABEL}" }}}}'
    )
    return (
        "docker",
        "network",
        "inspect",
        "--format",
        identity_template,
        plan.network_name,
    )


def network_plan_identity(plan: FirewallPlan) -> str:
    material = "\x00".join(
        (
            plan.network_name,
            plan.bridge_name,
            plan.ipv4_subnet,
            plan.ipv6_subnet or "",
            *sorted(plan.approved_dns_endpoints),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def firewall_rule_argv(plan: FirewallPlan) -> tuple[tuple[str, ...], ...]:
    """Build host-side rules; the notebook container receives no firewall capability."""
    families: list[tuple[str, str, Sequence[str]]] = [
        ("iptables", plan.ipv4_subnet, IPV4_DENY_CIDRS),
    ]

    commands: list[tuple[str, ...]] = []
    for executable, source_subnet, denied in families:
        chain = _chain_name(plan, executable)
        commands.append((executable, "-w", "-N", chain))
        commands.append((executable, "-w", "-F", chain))
        for endpoint in plan.approved_dns_endpoints:
            address = ipaddress.ip_address(endpoint)
            if (address.version == 4) != (executable == "iptables"):
                continue
            for protocol in ("udp", "tcp"):
                commands.append(
                    (
                        executable,
                        "-w",
                        "-A",
                        chain,
                        "-p",
                        protocol,
                        "-d",
                        endpoint,
                        "--dport",
                        "53",
                        "-j",
                        "ACCEPT",
                    )
                )
        # DNS is only permitted to the configured resolver endpoints above.
        # Rejecting port 53 before the general destination rules prevents a
        # notebook from selecting an arbitrary public resolver as a bypass.
        for protocol in ("udp", "tcp"):
            commands.append(
                (
                    executable,
                    "-w",
                    "-A",
                    chain,
                    "-p",
                    protocol,
                    "--dport",
                    "53",
                    "-j",
                    "REJECT",
                )
            )
        for destination in denied:
            commands.append(
                (
                    executable,
                    "-w",
                    "-A",
                    chain,
                    "-d",
                    destination,
                    "-j",
                    "REJECT",
                )
            )
        commands.append((executable, "-w", "-A", chain, "-j", "RETURN"))
        commands.append(
            (
                executable,
                "-w",
                "-I",
                "DOCKER-USER",
                "1",
                "-s",
                source_subnet,
                "-j",
                chain,
            )
        )
        input_chain = _chain_name(plan, executable, purpose="input")
        commands.append((executable, "-w", "-N", input_chain))
        commands.append((executable, "-w", "-F", input_chain))
        for endpoint in plan.approved_dns_endpoints:
            address = ipaddress.ip_address(endpoint)
            if (address.version == 4) != (executable == "iptables"):
                continue
            for protocol in ("udp", "tcp"):
                commands.append(
                    (
                        executable,
                        "-w",
                        "-A",
                        input_chain,
                        "-p",
                        protocol,
                        "-d",
                        endpoint,
                        "--dport",
                        "53",
                        "-j",
                        "ACCEPT",
                    )
                )
        commands.append((executable, "-w", "-A", input_chain, "-j", "REJECT"))
        commands.append(
            (
                executable,
                "-w",
                "-I",
                "INPUT",
                "1",
                "-i",
                plan.bridge_name,
                "-s",
                source_subnet,
                "-j",
                input_chain,
            )
        )
    return tuple(commands)


def firewall_verification_argv(plan: FirewallPlan) -> tuple[tuple[str, ...], ...]:
    # The named bridge must exist in the launcher's own network namespace.
    # This rejects remote Docker daemons and cross-WSL/context topologies where
    # local iptables rules would not govern the container network.
    commands: list[tuple[str, ...]] = [
        ("ip", "link", "show", "dev", plan.bridge_name)
    ]
    for command in firewall_rule_argv(plan):
        action = command[2]
        if action in {"-N", "-F"}:
            continue
        check = list(command)
        check[2] = "-C"
        if action == "-I":
            del check[4]
        commands.append(tuple(check))
    return tuple(commands)


def _chain_name(
    plan: FirewallPlan,
    executable: str,
    *,
    purpose: str = "egress",
) -> str:
    family = "4" if executable == "iptables" else "6"
    role = "I" if purpose == "input" else "E"
    safe = re.sub(r"[^A-Za-z0-9]", "", plan.network_name).upper()
    suffix = hashlib.sha256(f"{purpose}:{plan.network_name}".encode()).hexdigest()[:8].upper()
    return f"NL{family}{role}{safe[:9]}{suffix}"


class NetworkPolicyApplier:
    def __init__(self, *, runner: Runner = run_argv) -> None:
        self.runner = runner

    def apply(self, plan: FirewallPlan) -> NetworkPolicyAttestation:
        try:
            created = self._run(docker_network_create_argv(plan))
        except NetworkPolicyError as exc:
            self._reconcile_create_failure(plan, exc)
            raise
        if created.returncode != 0:
            exc = NetworkPolicyError(
                f"docker_network_create_failed_{created.returncode}"
            )
            self._reconcile_create_failure(plan, exc)
            raise exc

        try:
            for command in firewall_rule_argv(plan):
                result = self._run(command)
                if result.returncode != 0:
                    raise NetworkPolicyError(
                        f"firewall_rule_failed_{result.returncode}:{command[0]}:{command[2]}"
                    )
            return self.verify(plan)
        except Exception as exc:
            try:
                self.remove(plan)
            except NetworkPolicyError as cleanup_error:
                exc.add_note(f"network cleanup also failed: {cleanup_error}")
            raise

    def verify(self, plan: FirewallPlan) -> NetworkPolicyAttestation:
        """Re-attest every exact host rule without mutating network state."""
        verification_commands = firewall_verification_argv(plan)
        verified = 0
        for command in verification_commands:
            result = self._run(command)
            if result.returncode != 0:
                raise NetworkPolicyError(
                    f"firewall_verification_failed_{result.returncode}:{command[0]}"
                )
            verified += 1
        return NetworkPolicyAttestation(
            network_name=plan.network_name,
            bridge_name=plan.bridge_name,
            verified=True,
            commands_verified=verified,
            verification_commands=verification_commands,
        )

    def remove(self, plan: FirewallPlan) -> None:
        """Attempt every cleanup step and fail if any resource was not removed."""
        try:
            self._remove_owned_network(plan)
        except NetworkPolicyError as exc:
            raise NetworkPolicyError(
                f"network_cleanup_failed:docker:network-rm:{exc}"
            ) from exc

        failures: list[str] = []
        families = [("iptables", plan.ipv4_subnet)]
        for executable, source_subnet in families:
            egress_chain = _chain_name(plan, executable)
            input_chain = _chain_name(plan, executable, purpose="input")
            commands = (
                (
                    executable,
                    "-w",
                    "-D",
                    "DOCKER-USER",
                    "-s",
                    source_subnet,
                    "-j",
                    egress_chain,
                ),
                (
                    executable,
                    "-w",
                    "-D",
                    "INPUT",
                    "-i",
                    plan.bridge_name,
                    "-s",
                    source_subnet,
                    "-j",
                    input_chain,
                ),
                (executable, "-w", "-F", egress_chain),
                (executable, "-w", "-X", egress_chain),
                (executable, "-w", "-F", input_chain),
                (executable, "-w", "-X", input_chain),
            )
            for command in commands:
                try:
                    result = self._run(command)
                    if result.returncode != 0 and not _cleanup_target_absent(
                        command, result
                    ):
                        failures.append(
                            f"{command[0]}:{command[2]}:{result.returncode}"
                        )
                except NetworkPolicyError as exc:
                    failures.append(f"{command[0]}:{command[2]}:{exc}")
        if failures:
            raise NetworkPolicyError(
                "network_cleanup_failed:" + ",".join(failures)
            )

    def _reconcile_create_failure(
        self,
        plan: FirewallPlan,
        original_error: NetworkPolicyError,
    ) -> None:
        try:
            self._remove_owned_network(plan)
        except NetworkPolicyError as cleanup_error:
            original_error.add_note(
                f"uncertain network create reconciliation failed: {cleanup_error}"
            )

    def _remove_owned_network(self, plan: FirewallPlan) -> None:
        identity = self._inspect_network_identity(plan)
        if identity is None:
            return
        if identity.plan_identity != network_plan_identity(plan):
            raise NetworkPolicyError("network_plan_identity_mismatch")
        self._remove_verified_network(identity.network_id)

    def _remove_verified_network(self, network_id: str) -> None:
        command = ("docker", "network", "rm", network_id)
        result = self._run(command)
        if result.returncode != 0 and not _cleanup_target_absent(command, result):
            raise NetworkPolicyError(f"network_remove_failed_{result.returncode}")

    def _inspect_network_identity(
        self,
        plan: FirewallPlan,
    ) -> _NetworkIdentity | None:
        command = docker_network_inspect_argv(plan)
        result = self._run(command)
        if result.returncode != 0:
            if _cleanup_target_absent(command, result):
                return None
            raise NetworkPolicyError(f"network_inspect_failed_{result.returncode}")
        fields = result.stdout.rstrip("\r\n").split("\t", 1)
        if len(fields) != 2 or not _NETWORK_ID.fullmatch(fields[0]):
            raise NetworkPolicyError("network_inspect_invalid_identity")
        return _NetworkIdentity(network_id=fields[0], plan_identity=fields[1])

    def _run(self, argv: Sequence[str]) -> CommandResult:
        try:
            return self.runner(
                tuple(argv),
                timeout_seconds=30,
                max_output_bytes=4096,
            )
        except (ExecutionTimeout, OSError) as exc:
            raise NetworkPolicyError(type(exc).__name__) from exc


def _cleanup_target_absent(
    command: Sequence[str],
    result: CommandResult,
) -> bool:
    """Treat only explicit already-absent diagnostics as idempotent cleanup."""
    message = f"{result.stdout}\n{result.stderr}".casefold()
    if command[0] == "iptables":
        return any(
            marker in message
            for marker in (
                "bad rule (does a matching rule exist",
                "no chain/target/match by that name",
            )
        )
    if tuple(command[:3]) in {
        ("docker", "network", "inspect"),
        ("docker", "network", "rm"),
    }:
        target = str(command[-1]).casefold()
        return any(
            marker in message
            for marker in (
                f"network {target} not found",
                f"no such network: {target}",
            )
        )
    return False
