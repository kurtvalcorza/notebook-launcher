from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LauncherError(Exception):
    code: str
    message: str
    http_status: int = 400

    def __str__(self) -> str:
        return self.message

    def public_dict(self) -> dict[str, object]:
        return {"error": self.code, "message": self.message}


class ConflictError(LauncherError):
    def __init__(self, message: str = "The resource changed; refresh and retry."):
        super().__init__("conflict", message, 409)


class PermissionDenied(LauncherError):
    def __init__(self, message: str = "Operation is not permitted."):
        super().__init__("permission_denied", message, 403)


class LaunchAuthorizationError(LauncherError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, 403)


class WritableAgentBusy(LauncherError):
    def __init__(self):
        super().__init__(
            "writable_agent_busy",
            "Another writable agent is already attached.",
            409,
        )


class WorkspaceActive(LauncherError):
    def __init__(self):
        super().__init__(
            "workspace_active",
            "The workspace already has an active session.",
            409,
        )


class StorageFull(LauncherError):
    def __init__(self):
        super().__init__(
            "storage_full",
            "The persistent write could not complete because storage is full.",
            507,
        )


class HostPathEscape(LauncherError):
    def __init__(self):
        super().__init__(
            "host_path_escape",
            "The requested path resolves outside the granted directory.",
            403,
        )
