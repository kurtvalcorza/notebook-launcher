from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class GPU(StrEnum):
    AUTO = "auto"
    ON = "on"
    OFF = "off"


class AgentMode(StrEnum):
    WRITE = "write"
    READONLY = "readonly"


class TrustScope(StrEnum):
    EXACT_COMMIT = "exact_commit"
    REPOSITORY = "repository"


class SessionState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class ExecutionState(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    CANCEL_PENDING = "cancel_pending"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class LaunchRequest(BaseModel):
    url: str | None = None
    repo: str | None = None
    ref: str | None = None
    path: str | None = None
    workspace_id: UUID | None = None
    gpu: GPU = GPU.AUTO
    agent_mode: AgentMode = AgentMode.WRITE

    @model_validator(mode="after")
    def validate_source(self) -> "LaunchRequest":
        remote = any((self.url, self.repo, self.ref, self.path))
        if self.workspace_id and remote:
            raise ValueError("workspace_id is mutually exclusive with remote-source fields")
        if not self.workspace_id and not (self.url or self.repo):
            raise ValueError("remote source or workspace_id is required")
        if self.repo and not (self.ref and self.path):
            raise ValueError("repo launches require ref and path")
        return self


class ResolvedSource(BaseModel):
    repository_id: int
    repository_node_id: str | None = None
    owner: str
    repository: str
    requested_ref: str
    commit_sha: str = Field(min_length=40, max_length=40)
    notebook_path: str
    clone_url: str
    resolved_at: datetime


class LaunchPreview(BaseModel):
    request_digest: str
    source: ResolvedSource | None = None
    workspace_id: UUID | None = None
    trust_covered: bool
    launch_token: str
    expires_at: datetime


class TrustRecord(BaseModel):
    id: UUID
    repository_id: int
    repository_node_id: str | None = None
    grant_owner: str
    grant_repository: str
    scope: TrustScope
    commit_sha: str | None
    created_at: datetime
    revoked_at: datetime | None = None

    @model_validator(mode="after")
    def validate_commit_scope(self) -> "TrustRecord":
        if self.scope is TrustScope.EXACT_COMMIT and not self.commit_sha:
            raise ValueError("exact_commit trust requires commit_sha")
        if self.scope is TrustScope.REPOSITORY and self.commit_sha is not None:
            raise ValueError("repository trust must not include commit_sha")
        return self


class Workspace(BaseModel):
    id: UUID
    source_repository_id: int
    source_owner: str
    source_repository: str
    source_commit_sha: str
    source_notebook_path: str
    root_path: Path
    work_path: Path
    outputs_path: Path
    active_notebook_path: str
    user_data_grant_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class UserDataGrant(BaseModel):
    id: UUID
    workspace_id: UUID
    display_path: Path
    canonical_root: Path
    container_path: str = "/mnt/user-data"
    mode: str
    created_at: datetime
    revoked_at: datetime | None = None


class NotebookSession(BaseModel):
    id: UUID
    workspace_id: UUID
    runtime_id: str
    host_port: int
    gpu_enabled: bool
    agent_mode: AgentMode
    sandbox_verified: bool = False
    network_verified: bool = False
    collaboration_ready: bool = False
    state: SessionState
    notebook_url: str
    started_at: datetime
    stopped_at: datetime | None = None


class ExecutionOperation(BaseModel):
    operation_id: UUID
    session_id: UUID
    lease_id: UUID | None = None
    state: ExecutionState
    jupyter_msg_id: str | None = None
    active_parent_msg_id: str | None = None
    queued_at: datetime
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    deadline_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    finished_at: datetime | None = None


class OutputEnvelope(BaseModel):
    mime_type: str | None = None
    serialized_size: int | None = None
    payload: Any | None = None
    truncated: bool = False
    authoritative_location: str | None = None


class NetworkPolicyState(BaseModel):
    public_internet_allowed: bool = True
    approved_dns_endpoints: list[str] = Field(default_factory=list)
    deny_non_global: bool = True
    host_gateway_alias_present: bool = False
    verified_at: datetime | None = None
