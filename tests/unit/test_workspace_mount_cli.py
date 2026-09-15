from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from notebook_launcher.cli import main
from notebook_launcher.config import Settings
from notebook_launcher.state import StateStore


def insert_workspace(state: StateStore, workspace_id):
    now = datetime.now(UTC).isoformat()
    with state.transaction() as conn:
        conn.execute(
            """
            INSERT INTO workspaces (
                id, source_repository_id, source_owner, source_repository,
                source_commit_sha, source_notebook_path, root_path, work_path,
                outputs_path, active_notebook_path, created_at, updated_at
            ) VALUES (?, 1, 'owner', 'repo', ?, 'a.ipynb', ?, ?, ?, 'a.ipynb', ?, ?)
            """,
            (
                str(workspace_id),
                "a" * 40,
                "/tmp/root",
                "/tmp/work",
                "/tmp/out",
                now,
                now,
            ),
        )


def test_workspace_mount_and_unmount_cli(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("NOTEBOOK_LAUNCHER_ROOT", str(tmp_path / "launcher"))
    settings = Settings()
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()
    workspace_id = uuid4()
    insert_workspace(state, workspace_id)
    data = tmp_path / "data"
    data.mkdir()

    assert main(["workspace", "mount", str(workspace_id), str(data), "--mode", "rw"]) == 0
    output = capsys.readouterr().out
    assert " rw " in output
    assert str(data.resolve()) in output

    assert main(["workspace", "mount-status", str(workspace_id)]) == 0
    assert " rw " in capsys.readouterr().out

    assert main(["workspace", "unmount", str(workspace_id)]) == 0
    assert main(["workspace", "mount-status", str(workspace_id)]) == 0
    assert capsys.readouterr().out.strip() == "none"
