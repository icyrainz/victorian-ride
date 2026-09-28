"""Command line: `victorian-ride play`, plus `bind` and `probe` from the rig layer."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    from . import app
    from .rig import bindings

    parser = argparse.ArgumentParser(prog="victorian-ride", description="Drive a hansom cab with a sim rig.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    app.add_cli(sub)
    bindings.add_cli(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
