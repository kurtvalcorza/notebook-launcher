from __future__ import annotations

import argparse

import uvicorn

from .config import Settings
from .state import StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notebook-launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("mcp")
    sub.add_parser("trust")
    sub.add_parser("workspace")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    settings.ensure_directories()
    StateStore(settings.state_db).initialize()

    if args.command == "serve":
        uvicorn.run(
            "notebook_launcher.app:app",
            host=settings.host,
            port=settings.port,
            reload=False,
        )
        return 0

    raise SystemExit(f"{args.command!r} is not implemented in this tranche")


if __name__ == "__main__":
    raise SystemExit(main())
