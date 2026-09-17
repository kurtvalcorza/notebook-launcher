from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from .errors import ConflictError, PermissionDenied, RepositoryIdentityChanged
from .safe_paths import (
    RootIdentity,
    atomic_write,
    atomic_write_under_root,
    read_bytes_under_root,
    rename_under_root,
    root_identity,
    safe_open_under_root,
    unlink_under_root,
    validate_relative_path,
)
from .safe_paths import canonical_grant_root as canonical_grant_root  # noqa: PLC0414
from .state import StateStore, WorkspaceRecord, document_path_key
from .versions import content_hash, new_version

if TYPE_CHECKING:
    from .grants import UserDataGrantStore
    from .repository import SourceSnapshot


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path
    work: Path
    outputs: Path


class WorkspaceManager:
    """Own persistent workspace files while source snapshots remain immutable."""

    def __init__(
        self,
        state: StateStore,
        root: Path,
        *,
        grants: UserDataGrantStore | None = None,
    ) -> None:
        self.state = state
        self.root = root
        self.grants = grants

    def create(
        self,
        source: SourceSnapshot,
        *,
        source_owner: str,
        source_repository: str,
        source_notebook_path: str,
        workspace_id: UUID | None = None,
    ) -> WorkspaceRecord:
        from .repository import SourceCache, source_tree_digest

        workspace_id = workspace_id or uuid4()
        paths = self._paths(workspace_id)
        if paths.root.exists():
            raise FileExistsError(paths.root)
        paths.root.mkdir(mode=0o700, parents=True)
        try:
            SourceCache.verify_unchanged(source)
            materialize_workspace(source.root, paths.work)
            if source_tree_digest(paths.work) != source.tree_digest:
                raise RepositoryIdentityChanged()
            paths.outputs.mkdir(mode=0o700)
            SourceCache.verify_unchanged(source)
            now = datetime.now(UTC).isoformat()
            return self.state.create_workspace(
                workspace_id=workspace_id,
                source_repository_id=source.repository_id,
                source_owner=source_owner,
                source_repository=source_repository,
                source_commit_sha=source.commit_sha,
                source_notebook_path=source_notebook_path,
                root_path=paths.root,
                work_path=paths.work,
                outputs_path=paths.outputs,
                active_notebook_path=source_notebook_path,
                now=now,
            )
        except Exception as exc:
            try:
                _remove_workspace_tree(paths.root)
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve both failures
                raise ExceptionGroup(
                    "workspace creation and cleanup both failed",
                    [exc, cleanup_exc],
                ) from exc
            raise

    def reopen(self, workspace_id: UUID) -> WorkspaceRecord:
        record = self.state.get_workspace(workspace_id)
        if record is None:
            raise KeyError(str(workspace_id))
        if not record.work_path.is_dir() or not record.outputs_path.is_dir():
            raise FileNotFoundError("persistent workspace storage is unavailable")
        identity = root_identity(record.work_path)
        try:
            descriptor = safe_open_under_root(
                record.work_path,
                record.active_notebook_path,
                flags=os.O_RDONLY,
                expected_identity=identity,
                allow_windows_managed_root=True,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError("active notebook is missing from the workspace") from exc
        else:
            os.close(descriptor)
        return record

    def write_file(
        self,
        workspace_id: UUID,
        relative_path: str,
        data: bytes,
        *,
        expected_version: str | None = None,
        overwrite: bool = True,
        allow_active_notebook: bool = False,
    ) -> str:
        record = self._workspace(workspace_id)
        identity = root_identity(record.work_path)
        normalized_path = validate_relative_path(relative_path).as_posix()
        active_path = validate_relative_path(record.active_notebook_path).as_posix()
        if (
            document_path_key(normalized_path) == document_path_key(active_path)
            and not allow_active_notebook
        ):
            raise ConflictError(
                "The active notebook must be changed through notebook-aware operations."
            )
        version = new_version()
        self._write_workspace_document(
            record,
            normalized_path,
            data,
            kind="notebook" if normalized_path.endswith(".ipynb") else "file",
            version=version,
            expected_version=expected_version,
            overwrite=overwrite,
            allow_active_notebook=allow_active_notebook,
            identity=identity,
        )
        return version

    def save_copy(
        self,
        workspace_id: UUID,
        *,
        source_path: str,
        destination_scope: str,
        destination_path: str,
        include_outputs: bool = True,
        overwrite: bool = False,
    ) -> None:
        record = self._workspace(workspace_id)
        identity = root_identity(record.work_path)
        source_path = validate_relative_path(source_path).as_posix()
        destination_path = validate_relative_path(destination_path).as_posix()
        if not source_path.casefold().endswith(".ipynb"):
            raise ValueError("source_path must name a notebook")
        if not destination_path.casefold().endswith(".ipynb"):
            raise ValueError("destination_path must end in .ipynb")
        if destination_scope == "workspace":
            if document_path_key(source_path) == document_path_key(destination_path):
                raise ValueError("Save a Copy requires a distinct destination")
            if document_path_key(destination_path) == document_path_key(
                validate_relative_path(record.active_notebook_path).as_posix()
            ):
                raise ConflictError(
                    "Save a Copy cannot replace the active notebook."
                )
        data = read_bytes_under_root(
            record.work_path,
            source_path,
            expected_identity=identity,
            allow_windows_managed_root=True,
        )
        if not include_outputs:
            data = _without_notebook_outputs(data)
        if destination_scope == "workspace":
            existing = self.state.get_document_version(workspace_id, destination_path)
            if existing is not None and not overwrite:
                raise FileExistsError(destination_path)
            version = new_version()
            self._write_workspace_document(
                record,
                destination_path,
                data,
                kind="notebook",
                version=version,
                expected_version=existing.version if existing is not None else None,
                overwrite=overwrite,
                allow_active_notebook=False,
                identity=identity,
            )
            return
        if destination_scope != "user_data":
            raise ValueError("destination_scope must be workspace or user_data")
        if self.grants is None:
            raise PermissionDenied("No local data directory is granted.")
        grant = self.grants.revalidate(workspace_id)
        if grant is None:
            raise PermissionDenied("No local data directory is granted.")
        if grant.mode != "rw":
            raise PermissionDenied("The local data grant is read-only.")
        atomic_write_under_root(
            grant.canonical_root,
            destination_path,
            data,
            overwrite=overwrite,
            expected_identity=RootIdentity(grant.root_dev, grant.root_ino),
        )

    def rename_active_notebook(
        self,
        workspace_id: UUID,
        destination_path: str,
    ) -> WorkspaceRecord:
        record = self._workspace(workspace_id)
        identity = root_identity(record.work_path)
        old_path = validate_relative_path(record.active_notebook_path).as_posix()
        destination_path = validate_relative_path(destination_path).as_posix()
        if not destination_path.casefold().endswith(".ipynb"):
            raise ValueError("destination_path must end in .ipynb")
        moved = False
        current_path = old_path

        def compensate_rename() -> None:
            if moved:
                rename_under_root(
                    record.work_path,
                    destination_path,
                    current_path,
                    expected_identity=identity,
                    allow_windows_managed_root=True,
                )

        with self.state.active_notebook_rename_guard(
            workspace_id,
            expected_path=old_path,
            new_path=destination_path,
            now=datetime.now(UTC).isoformat(),
            rollback_compensation=compensate_rename,
        ) as guarded_path:
            current_path = guarded_path
            rename_under_root(
                record.work_path,
                current_path,
                destination_path,
                expected_identity=identity,
                allow_windows_managed_root=True,
            )
            moved = True
        updated = self.state.get_workspace(workspace_id)
        assert updated is not None
        return updated

    def _write_workspace_document(
        self,
        record: WorkspaceRecord,
        path: str,
        data: bytes,
        *,
        kind: str,
        version: str,
        expected_version: str | None,
        overwrite: bool,
        allow_active_notebook: bool,
        identity: RootIdentity,
    ) -> None:
        previous: bytes | None = None
        previous_exists = False
        write_attempted = False

        def compensate_write() -> None:
            if not write_attempted:
                return
            try:
                current = read_bytes_under_root(
                    record.work_path,
                    path,
                    expected_identity=identity,
                    allow_windows_managed_root=True,
                )
                current_exists = True
            except FileNotFoundError:
                current = None
                current_exists = False
            unchanged = current_exists == previous_exists and (
                not previous_exists or current == previous
            )
            if unchanged:
                return
            if previous_exists:
                assert previous is not None
                atomic_write_under_root(
                    record.work_path,
                    path,
                    previous,
                    overwrite=True,
                    expected_identity=identity,
                    allow_windows_managed_root=True,
                )
            else:
                unlink_under_root(
                    record.work_path,
                    path,
                    missing_ok=True,
                    expected_identity=identity,
                    allow_windows_managed_root=True,
                )

        with self.state.document_write_guard(
            workspace_id=record.id,
            path=path,
            kind=kind,
            version=version,
            content_digest=content_hash(data),
            expected_version=expected_version,
            now=datetime.now(UTC).isoformat(),
            allow_active_notebook=allow_active_notebook,
            rollback_compensation=compensate_write,
        ):
            try:
                previous = read_bytes_under_root(
                    record.work_path,
                    path,
                    expected_identity=identity,
                    allow_windows_managed_root=True,
                )
                previous_exists = True
            except FileNotFoundError:
                pass
            write_attempted = True
            atomic_write_under_root(
                record.work_path,
                path,
                data,
                overwrite=overwrite,
                expected_identity=identity,
                allow_windows_managed_root=True,
            )

    def _workspace(self, workspace_id: UUID) -> WorkspaceRecord:
        record = self.state.get_workspace(workspace_id)
        if record is None:
            raise KeyError(str(workspace_id))
        return record

    def _paths(self, workspace_id: UUID) -> WorkspacePaths:
        workspace_root = self.root / str(workspace_id)
        return WorkspacePaths(
            root=workspace_root,
            work=workspace_root / "work",
            outputs=workspace_root / "outputs",
        )


def save_copy(source: Path, destination: Path, *, overwrite: bool = False) -> None:
    atomic_write(destination, source.read_bytes(), overwrite=overwrite)


def materialize_workspace(source_root: Path, work_root: Path) -> None:
    if work_root.exists():
        raise FileExistsError(work_root)
    work_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_root,
        work_root,
        symlinks=True,
        ignore=shutil.ignore_patterns(".notebook-launcher-source.sha256"),
    )


def _remove_workspace_tree(path: Path) -> None:
    if not path.exists():
        return

    def make_writable_and_retry(function, filename, _error) -> None:
        os.chmod(filename, 0o700)
        function(filename)

    shutil.rmtree(path, onexc=make_writable_and_retry)


def _without_notebook_outputs(data: bytes) -> bytes:
    try:
        document = json.loads(data)
        cells = document["cells"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source_path does not contain a valid notebook") from exc
    if not isinstance(cells, list):
        raise ValueError(  # noqa: TRY004 - stable public validation error
            "source_path does not contain a valid notebook"
        )
    for cell in cells:
        if isinstance(cell, dict) and cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
