"""Command-line interface package."""


def main() -> None:
    """保留 ``bili_agent_cli.cli:main`` 的兼容调用入口。"""
    from .main import main as run

    run()


__all__ = ["main"]
