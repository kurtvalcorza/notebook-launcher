from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from notebook_launcher.models import (
    GPU,
    AgentMode,
    DestinationScope,
    EffectiveGPUStatus,
    GPUDeviceStatus,
    LaunchPhase,
    LaunchRequest,
    LaunchStatus,
    McpAttachment,
    McpStatus,
    NotebookCopyRequest,
    TrustRecord,
    TrustScope,
)


def test_workspace_reopen_cannot_mix_remote_source():
    with pytest.raises(ValidationError):
        LaunchRequest(
            workspace_id=uuid4(),
            repo="owner/repo",
            ref="main",
            path="x.ipynb",
        )


def test_repo_launch_requires_ref_and_path():
    with pytest.raises(ValidationError):
        LaunchRequest(repo="owner/repo")


def test_repository_trust_cannot_include_commit():
    with pytest.raises(ValidationError):
        TrustRecord(
            id=uuid4(),
            repository_id=1,
            grant_owner="owner",
            grant_repository="repo",
            scope=TrustScope.REPOSITORY,
            commit_sha="a" * 40,
            created_at=datetime.now(UTC),
        )


def test_public_contract_models_use_stable_enum_values():
    now = datetime.now(UTC)
    session_id = uuid4()
    launch = LaunchStatus(
        id=uuid4(),
        phase=LaunchPhase.READY,
        created_at=now,
        updated_at=now,
        session_id=session_id,
        agent_mode=AgentMode.WRITE,
    )
    descriptor = McpAttachment(
        session_id=session_id,
        notebook_path="tutorial.ipynb",
        available=True,
        status=McpStatus.AVAILABLE,
        agent_mode=AgentMode.WRITE,
        capabilities=["notebook.read"],
    )
    copy = NotebookCopyRequest(
        source_path="tutorial.ipynb",
        destination_scope=DestinationScope.WORKSPACE,
        destination_path="copies/tutorial.ipynb",
    )

    assert launch.model_dump(mode="json")["phase"] == "ready"
    assert descriptor.model_dump(mode="json")["status"] == "available"
    assert copy.model_dump(mode="json")["destination_scope"] == "workspace"


def test_effective_gpu_status_is_serializable():
    status = EffectiveGPUStatus(
        requested_mode=GPU.AUTO,
        enabled=True,
        devices=[GPUDeviceStatus(uuid="GPU-1", name="Test GPU")],
        host_available=True,
        container_available=True,
    )

    assert status.model_dump(mode="json") == {
        "requested_mode": "auto",
        "enabled": True,
        "devices": [{"uuid": "GPU-1", "name": "Test GPU"}],
        "host_available": True,
        "container_available": True,
        "reason": None,
    }
