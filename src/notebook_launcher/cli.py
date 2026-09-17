from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

import uvicorn

from .config import Settings
from .grants import UserDataGrantStore
from .state import StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notebook-launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("mcp")
    sub.add_parser("trust")

    workspace = sub.add_parser("workspace")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)

    mount = workspace_sub.add_parser("mount")
    mount.add_argument("workspace_id", type=UUID)
    mount.add_argument("path", type=Path)
    mount.add_argument("--mode", choices=("ro", "rw"), default="ro")

    unmount = workspace_sub.add_parser("unmount")
    unmount.add_argument("workspace_id", type=UUID)

    show = workspace_sub.add_parser("mount-status")
    show.add_argument("workspace_id", type=UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()

    if args.command == "serve":
        uvicorn.run(
            "notebook_launcher.app:app",
            host=settings.host,
            port=settings.port,
            reload=False,
        )
        return 0

    if args.command == "workspace":
        grants = UserDataGrantStore(state)
        if args.workspace_command == "mount":
            grant = grants.grant(
                args.workspace_id,
                args.path,
                mode=args.mode,
                home=Path.home(),
            )
            print(f"{grant.id} {grant.mode} {grant.canonical_root}")
            return 0
        if args.workspace_command == "unmount":
            if not grants.revoke(args.workspace_id):
                raise SystemExit("workspace has no active data grant")
            return 0
        if args.workspace_command == "mount-status":
            grant = grants.active(args.workspace_id)
            if grant is None:
                print("none")
            else:
                print(f"{grant.id} {grant.mode} {grant.canonical_root}")
            return 0

    raise SystemExit(f"{args.command!r} is not implemented in this tranche")


if __name__ == "__main__":
    raise SystemExit(main())
