from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .mcp import MIN_MCP_OUTPUT_BYTES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOTEBOOK_LAUNCHER_",
        case_sensitive=False,
    )

    root: Path = Field(default_factory=lambda: Path.home() / ".notebook-launcher")
    host: str = "127.0.0.1"
    port: int = 8080
    launch_token_ttl_seconds: int = 300
    trust_challenge_ttl_seconds: int = 600
    execution_timeout_seconds: int = 1800
    execution_cancel_grace_seconds: int = 10
    max_agent_output_bytes: int = Field(
        default=1024 * 1024,
        ge=MIN_MCP_OUTPUT_BYTES,
        description=(
            "Maximum MCP wire response bytes, including the newline; must fit "
            f"tool discovery ({MIN_MCP_OUTPUT_BYTES} bytes for current capabilities)."
        ),
    )
    writable_lease_stale_seconds: int = Field(default=30, ge=3)
    writable_lease_heartbeat_seconds: int = Field(default=10, ge=1)
    runtime_memory: str = "8g"
    runtime_cpus: float = 4.0
    runtime_pids_limit: int = 1024
    approved_dns_endpoints: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_lease_timing(self) -> Self:
        if self.writable_lease_heartbeat_seconds >= self.writable_lease_stale_seconds:
            raise ValueError(
                "writable lease heartbeat interval must be shorter than stale TTL"
            )
        return self

    @property
    def state_db(self) -> Path:
        return self.root / "state.db"

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def workspaces_dir(self) -> Path:
        return self.root / "workspaces"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"

    def ensure_directories(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.sources_dir.mkdir(mode=0o700, exist_ok=True)
        self.workspaces_dir.mkdir(mode=0o700, exist_ok=True)
        self.runtime_dir.mkdir(mode=0o700, exist_ok=True)
