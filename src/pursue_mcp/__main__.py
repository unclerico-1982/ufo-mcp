"""Entrypoint: ``python -m pursue_mcp`` or the ``pursue-mcp`` console script."""

from __future__ import annotations

from .server import serve


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
