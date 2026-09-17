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


class LaunchPhase(StrEnum):
    RECEIVED = "received"
    AWAITING_TRUST = "awaiting_trust"
    ACQUIRING = "acquiring"
    WORKSPACE_PREPARING = "workspace_preparing"
    BUILDING = "building"
    CACHE_HIT = "cache_hit"
    STARTING = "starting"
    MCP_PREPARING = "mcp_preparing"
    READY = "ready"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class McpStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"
    ATTACHING = "attaching"
    ATTACHED = "attached"
    ERROR = "error"
    INVALIDATED = "invalidated"


class DestinationScope(StrEnum):
    WORKSPACE = "workspace"
    USER_DATA = "user_data"


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
    def validate_source(self) -> LaunchRequest:
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


class LaunchStatus(BaseModel):
    id: UUID
    phase: LaunchPhase
    created_at: datetime
    updated_at: datetime
    message: str | None = None
    error_code: str | None = None
    repository_id: int | None = None
    repository: str | None = None
    commit_sha: str | None = None
    workspace_id: UUID | None = None
    notebook_path: str | None = None
    session_id: UUID | None = None
    existing_session_id: UUID | None = None
    ready_url: str | None = None
    gpu_enabled: bool | None = None
    agent_mode: AgentMode | None = None
    mcp_available: bool | None = None


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
    def validate_commit_scope(self) -> TrustRecord:
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


class EnvironmentIdentity(BaseModel):
    repository_id: int
    commit_sha: str = Field(min_length=40, max_length=40)
    builder_version: str
    strategy_version: str
    environment_digest: str
    image_tag: str


class GPUDeviceStatus(BaseModel):
    uuid: str
    name: str


class EffectiveGPUStatus(BaseModel):
    requested_mode: GPU
    enabled: bool
    devices: list[GPUDeviceStatus] = Field(default_factory=list)
    host_available: bool
    container_available: bool
    reason: str | None = None


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


class DocumentVersion(BaseModel):
    workspace_id: UUID
    path: str
    kind: str
    version: str
    updated_at: datetime


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


class NotebookCopyRequest(BaseModel):
    source_path: str
    destination_scope: DestinationScope
    destination_path: str
    include_outputs: bool = True
    overwrite: bool = False


class McpAttachment(BaseModel):
    session_id: UUID
    notebook_path: str
    available: bool
    status: McpStatus
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    backend: str | None = None
    agent_mode: AgentMode
    write_lease_available: bool | None = None
    capabilities: list[str] = Field(default_factory=list)
    message: str | None = None


class AgentExecutionEvent(BaseModel):
    session_id: UUID
    operation_id: UUID | None = None
    lease_id: UUID | None = None
    operation: str
    target: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    outcome: str | None = None
    error_type: str | None = None
