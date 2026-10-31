import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sourcetrail",
        description="Browse class diagrams of a Python project in the terminal.",
    )
    parser.add_argument("directory", type=Path, help="root directory of the Python project")
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error(f"not a directory: {args.directory}")

    from .app import run

    run(args.directory)
