import pytest

from notebook_launcher.errors import ExecutionTimeout
from notebook_launcher.network import (
    FirewallPlan,
    NetworkPolicyApplier,
    NetworkPolicyError,
    all_destinations_allowed,
    classify_destination,
    docker_network_create_argv,
    docker_network_inspect_argv,
    firewall_rule_argv,
    firewall_verification_argv,
    network_plan_identity,
)
from notebook_launcher.orchestration import CommandResult


def command_result(argv, *, returncode=0, stdout="", stderr=""):
    return CommandResult(
        argv=tuple(argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=len(stdout.encode()),
        stderr_bytes=len(stderr.encode()),
        truncated=False,
        duration_ms=1,
    )


OWNED_NETWORK_ID = "a" * 64


def inspect_identity(plan, *, network_id=OWNED_NETWORK_ID, label=None):
    return f"{network_id}\t{label or network_plan_identity(plan)}\n"


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
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "2001:db8::1",
    ],
)
def test_non_global_destinations_are_denied(address):
    assert not classify_destination(address).allowed


def test_public_internet_is_allowed():
    decision = classify_destination("8.8.8.8")
    assert decision.allowed
    assert decision.reason == "public_internet"


def test_firewall_special_ranges_match_classifier_and_dns_is_restricted():
    plan = FirewallPlan(
        network_name="nl-session-with-shared-prefix-one",
        bridge_name="nls0002",
        ipv4_subnet="172.30.2.0/24",
        approved_dns_endpoints=("192.168.65.5",),
    )
    joined = [" ".join(command) for command in firewall_rule_argv(plan)]

    assert not classify_destination("2001::1").allowed
    assert not any(command.startswith("ip6tables ") for command in joined)
    assert classify_destination("192.0.0.9").allowed
    assert not any("-d 192.0.0.0/24" in command for command in joined)
    for address in ("192.88.99.0", "192.88.99.1", "192.88.99.2", "192.88.99.255"):
        assert not classify_destination(address).allowed
    assert any("-d 192.88.99.0/24" in command for command in joined)
    assert any("--dport 53 -j REJECT" in command for command in joined)

    other = FirewallPlan(
        network_name="nl-session-with-shared-prefix-two",
        bridge_name="nls0003",
        ipv4_subnet="172.30.3.0/24",
    )
    first_chain = firewall_rule_argv(plan)[0][-1]
    second_chain = firewall_rule_argv(other)[0][-1]
    assert first_chain != second_chain


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


def test_firewall_plan_has_dns_only_exception_before_non_global_denies():
    plan = FirewallPlan(
        network_name="nl-session",
        bridge_name="nls0001",
        ipv4_subnet="172.30.0.0/24",
        approved_dns_endpoints=("192.168.65.5",),
    )
    commands = firewall_rule_argv(plan)
    joined = [" ".join(command) for command in commands]

    dns4 = next(index for index, item in enumerate(joined) if "192.168.65.5" in item)
    private4 = next(index for index, item in enumerate(joined) if "10.0.0.0/8" in item)
    assert dns4 < private4
    assert all("--dport 53" in item for item in joined if "192.168.65.5" in item)
    assert not any("host-gateway" in item or "host.docker.internal" in item for item in joined)
    assert "--ipv6" not in docker_network_create_argv(plan)
    assert firewall_verification_argv(plan)
    verification = firewall_verification_argv(plan)
    assert verification[0] == ("ip", "link", "show", "dev", plan.bridge_name)
    assert all(command[2] == "-C" for command in verification[1:])
    input_jump = next(
        command
        for command in commands
        if command[2:5] == ("-I", "INPUT", "1")
    )
    input_chain = input_jump[-1]
    assert input_jump == (
        "iptables",
        "-w",
        "-I",
        "INPUT",
        "1",
        "-i",
        plan.bridge_name,
        "-s",
        plan.ipv4_subnet,
        "-j",
        input_chain,
    )
    input_rules = [command for command in commands if input_chain in command]
    assert any(
        "-d" in command
        and "192.168.65.5" in command
        and "--dport" in command
        and "53" in command
        and command[-1] == "ACCEPT"
        for command in input_rules
    )
    assert ("iptables", "-w", "-A", input_chain, "-j", "REJECT") in input_rules
    assert (
        "iptables",
        "-w",
        "-C",
        "INPUT",
        "-i",
        plan.bridge_name,
        "-s",
        plan.ipv4_subnet,
        "-j",
        input_chain,
    ) in verification
    create = docker_network_create_argv(plan)
    label_index = create.index("--label")
    assert create[label_index + 1].endswith(network_plan_identity(plan))


def test_ipv6_runtime_network_and_dns_are_rejected_until_policy_is_complete():
    with pytest.raises(ValueError, match="IPv6 runtime networking is disabled"):
        FirewallPlan(
            network_name="nl-ipv6",
            bridge_name="nlipv6",
            ipv4_subnet="172.30.4.0/24",
            ipv6_subnet="fd00:1234::/64",
        )
    with pytest.raises(ValueError, match="DNS endpoints must be IPv4"):
        FirewallPlan(
            network_name="nl-ipv6-dns",
            bridge_name="nlipv6dns",
            ipv4_subnet="172.30.5.0/24",
            approved_dns_endpoints=("fd00::53",),
        )


def test_network_policy_applier_attests_only_after_rule_checks():
    plan = FirewallPlan(
        network_name="nl-session",
        bridge_name="nls0001",
        ipv4_subnet="172.30.0.0/24",
        approved_dns_endpoints=("192.168.65.5",),
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        return command_result(argv, stdout="network-id\n")

    attestation = NetworkPolicyApplier(runner=runner).apply(plan)

    assert attestation.verified
    assert attestation.commands_verified == len(firewall_verification_argv(plan))
    assert calls[0] == docker_network_create_argv(plan)
    assert any(command[2] == "-C" for command in calls if command[0] == "iptables")
    assert ("ip", "link", "show", "dev", plan.bridge_name) in calls


def test_network_policy_verify_is_side_effect_free_and_exact():
    plan = FirewallPlan(
        network_name="nl-verify",
        bridge_name="nlverify",
        ipv4_subnet="172.30.8.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        return command_result(argv)

    attestation = NetworkPolicyApplier(runner=runner).verify(plan)

    assert calls == list(firewall_verification_argv(plan))
    assert attestation.commands_verified == len(calls)
    assert all(command[2] == "-C" for command in calls[1:])


def test_network_policy_failure_removes_unattested_network():
    plan = FirewallPlan(
        network_name="nl-failure",
        bridge_name="nlfail",
        ipv4_subnet="172.31.0.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[0] == "iptables" and argv[2] == "-A":
            return command_result(argv, returncode=2)
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(argv, stdout=inspect_identity(plan))
        return command_result(argv, stdout="ok\n")

    with pytest.raises(NetworkPolicyError, match="firewall_rule_failed"):
        NetworkPolicyApplier(runner=runner).apply(plan)

    removal_index = calls.index(("docker", "network", "rm", OWNED_NETWORK_ID))
    assert any(command[0] == "iptables" for command in calls[removal_index + 1 :])


def test_remove_attempts_every_cleanup_step_then_reports_nonzero_results():
    plan = FirewallPlan(
        network_name="nl-cleanup-failure",
        bridge_name="nlcleanup",
        ipv4_subnet="172.31.1.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[0] == "iptables" and argv[2] == "-D":
            return command_result(argv, returncode=1)
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(argv, stdout=inspect_identity(plan))
        return command_result(argv)

    with pytest.raises(NetworkPolicyError, match="network_cleanup_failed") as caught:
        NetworkPolicyApplier(runner=runner).remove(plan)

    assert "iptables:-D:1" in str(caught.value)
    assert any(command[0] == "iptables" and command[2] == "-X" for command in calls)
    assert calls[:2] == [
        docker_network_inspect_argv(plan),
        ("docker", "network", "rm", OWNED_NETWORK_ID),
    ]


def test_remove_is_idempotent_only_for_explicit_already_absent_results():
    plan = FirewallPlan(
        network_name="nl-cleanup-absent",
        bridge_name="nlabsent",
        ipv4_subnet="172.31.2.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[0] == "iptables":
            return command_result(
                argv,
                returncode=1,
                stderr="iptables: No chain/target/match by that name.",
            )
        assert tuple(argv) == docker_network_inspect_argv(plan)
        return command_result(
            argv,
            returncode=1,
            stderr=f"Error response from daemon: network {plan.network_name} not found",
        )

    NetworkPolicyApplier(runner=runner).remove(plan)

    assert calls[0] == docker_network_inspect_argv(plan)
    assert [command[2] for command in calls[1:]] == [
        "-D",
        "-D",
        "-F",
        "-X",
        "-F",
        "-X",
    ]
    input_jump = next(
        command
        for command in firewall_rule_argv(plan)
        if command[2:5] == ("-I", "INPUT", "1")
    )
    input_chain = input_jump[-1]
    assert (
        "iptables",
        "-w",
        "-D",
        "INPUT",
        "-i",
        plan.bridge_name,
        "-s",
        plan.ipv4_subnet,
        "-j",
        input_chain,
    ) in calls
    assert ("iptables", "-w", "-F", input_chain) in calls
    assert ("iptables", "-w", "-X", input_chain) in calls


def test_generic_or_other_target_not_found_does_not_authorize_network_cleanup():
    plan = FirewallPlan(
        network_name="nl-cleanup-specific",
        bridge_name="nlspecific",
        ipv4_subnet="172.31.3.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[0] == "iptables":
            return command_result(
                argv,
                returncode=1,
                stderr="iptables: No chain/target/match by that name.",
            )
        return command_result(
            argv,
            returncode=1,
            stderr="Error response from daemon: network unrelated not found",
        )

    with pytest.raises(NetworkPolicyError, match="network_inspect_failed"):
        NetworkPolicyApplier(runner=runner).remove(plan)

    assert not any(command[:3] == ("docker", "network", "rm") for command in calls)


def test_name_replacement_race_removes_only_previously_verified_network_id():
    plan = FirewallPlan(
        network_name="nl-name-replaced",
        bridge_name="nlreplaced",
        ipv4_subnet="172.31.6.0/24",
    )
    old_network_id = "b" * 64
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[0] == "iptables":
            return command_result(
                argv,
                returncode=1,
                stderr="iptables: No chain/target/match by that name.",
            )
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(
                argv,
                stdout=inspect_identity(plan, network_id=old_network_id),
            )
        assert tuple(argv) == ("docker", "network", "rm", old_network_id)
        return command_result(
            argv,
            returncode=1,
            stderr=f"Error response from daemon: network {old_network_id} not found",
        )

    NetworkPolicyApplier(runner=runner).remove(plan)

    assert calls[:2] == [
        docker_network_inspect_argv(plan),
        ("docker", "network", "rm", old_network_id),
    ]
    assert ("docker", "network", "rm", plan.network_name) not in calls


def test_network_remove_failure_retains_all_firewall_rules():
    plan = FirewallPlan(
        network_name="nl-active-endpoint",
        bridge_name="nlactive",
        ipv4_subnet="172.31.12.0/24",
    )
    network_id = "e" * 64
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(
                argv,
                stdout=inspect_identity(plan, network_id=network_id),
            )
        if tuple(argv) == ("docker", "network", "rm", network_id):
            return command_result(
                argv,
                returncode=1,
                stderr="Error response from daemon: network has active endpoints",
            )
        raise AssertionError("firewall cleanup must not run after network removal fails")

    with pytest.raises(NetworkPolicyError, match="network_remove_failed_1"):
        NetworkPolicyApplier(runner=runner).remove(plan)

    assert calls == [
        docker_network_inspect_argv(plan),
        ("docker", "network", "rm", network_id),
    ]


def test_absence_diagnostic_for_name_does_not_cover_verified_network_id():
    plan = FirewallPlan(
        network_name="nl-wrong-id-diagnostic",
        bridge_name="nlwrongid",
        ipv4_subnet="172.31.7.0/24",
    )
    verified_id = "c" * 64

    def runner(argv, **_kwargs):
        if argv[0] == "iptables":
            return command_result(
                argv,
                returncode=1,
                stderr="iptables: No chain/target/match by that name.",
            )
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(
                argv,
                stdout=inspect_identity(plan, network_id=verified_id),
            )
        return command_result(
            argv,
            returncode=1,
            stderr=f"Error response from daemon: network {plan.network_name} not found",
        )

    with pytest.raises(NetworkPolicyError, match="network_remove_failed_1"):
        NetworkPolicyApplier(runner=runner).remove(plan)


def test_uncertain_create_removes_only_network_with_matching_plan_identity():
    plan = FirewallPlan(
        network_name="nl-uncertain-owned",
        bridge_name="nluncertain",
        ipv4_subnet="172.31.4.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == docker_network_create_argv(plan):
            raise ExecutionTimeout()
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(argv, stdout=inspect_identity(plan))
        return command_result(argv)

    with pytest.raises(NetworkPolicyError, match="ExecutionTimeout"):
        NetworkPolicyApplier(runner=runner).apply(plan)

    assert calls[-1] == ("docker", "network", "rm", OWNED_NETWORK_ID)


def test_uncertain_create_preserves_foreign_network():
    plan = FirewallPlan(
        network_name="nl-uncertain-foreign",
        bridge_name="nlforeign",
        ipv4_subnet="172.31.5.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == docker_network_create_argv(plan):
            raise OSError("connection lost after create request")
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(
                argv,
                stdout=inspect_identity(plan, label="foreign-plan-identity"),
            )
        return command_result(argv)

    with pytest.raises(NetworkPolicyError, match="OSError") as caught:
        NetworkPolicyApplier(runner=runner).apply(plan)

    assert "network_plan_identity_mismatch" in " ".join(caught.value.__notes__)
    assert not any(command[:3] == ("docker", "network", "rm") for command in calls)


def test_nonzero_create_reconciles_owned_network_by_verified_id():
    plan = FirewallPlan(
        network_name="nl-create-rc-owned",
        bridge_name="nlrcowned",
        ipv4_subnet="172.31.8.0/24",
    )
    network_id = "d" * 64
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == docker_network_create_argv(plan):
            return command_result(argv, returncode=1)
        if tuple(argv) == docker_network_inspect_argv(plan):
            return command_result(
                argv,
                stdout=inspect_identity(plan, network_id=network_id),
            )
        return command_result(argv)

    with pytest.raises(NetworkPolicyError, match="docker_network_create_failed_1"):
        NetworkPolicyApplier(runner=runner).apply(plan)

    assert calls == [
        docker_network_create_argv(plan),
        docker_network_inspect_argv(plan),
        ("docker", "network", "rm", network_id),
    ]


def test_nonzero_create_preserves_foreign_network_and_original_failure():
    plan = FirewallPlan(
        network_name="nl-create-rc-foreign",
        bridge_name="nlrcforeign",
        ipv4_subnet="172.31.9.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == docker_network_create_argv(plan):
            return command_result(argv, returncode=1)
        return command_result(
            argv,
            stdout=inspect_identity(plan, label="foreign-plan-identity"),
        )

    with pytest.raises(
        NetworkPolicyError,
        match="docker_network_create_failed_1",
    ) as caught:
        NetworkPolicyApplier(runner=runner).apply(plan)

    assert "network_plan_identity_mismatch" in " ".join(caught.value.__notes__)
    assert calls == [docker_network_create_argv(plan), docker_network_inspect_argv(plan)]


def test_nonzero_create_accepts_exact_target_absence_during_reconciliation():
    plan = FirewallPlan(
        network_name="nl-create-rc-absent",
        bridge_name="nlrcabsent",
        ipv4_subnet="172.31.10.0/24",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == docker_network_create_argv(plan):
            return command_result(argv, returncode=1)
        return command_result(
            argv,
            returncode=1,
            stderr=f"Error response from daemon: network {plan.network_name} not found",
        )

    with pytest.raises(NetworkPolicyError, match="docker_network_create_failed_1"):
        NetworkPolicyApplier(runner=runner).apply(plan)

    assert calls == [docker_network_create_argv(plan), docker_network_inspect_argv(plan)]


def test_nonzero_create_preserves_inspection_uncertainty_as_failure_note():
    plan = FirewallPlan(
        network_name="nl-create-rc-uncertain",
        bridge_name="nlrcunknown",
        ipv4_subnet="172.31.11.0/24",
    )

    def runner(argv, **_kwargs):
        if tuple(argv) == docker_network_create_argv(plan):
            return command_result(argv, returncode=1)
        return command_result(
            argv,
            returncode=2,
            stderr="Error response from daemon: control plane unavailable",
        )

    with pytest.raises(
        NetworkPolicyError,
        match="docker_network_create_failed_1",
    ) as caught:
        NetworkPolicyApplier(runner=runner).apply(plan)

    assert "network_inspect_failed_2" in " ".join(caught.value.__notes__)
