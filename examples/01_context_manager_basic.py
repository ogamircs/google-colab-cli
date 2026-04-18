"""Example 1: open a Colab runtime, run a string of Python, print stdout.

Demonstrates the context-manager primitive. No GPU required.
"""

from __future__ import annotations

from colab_cli import colab


def main() -> None:
    with colab(gpu=None) as c:
        print(f"[local]  connected — accelerator={c.accelerator}")
        result = c.run("import platform; print(platform.python_version())")
        print(f"[remote] python: {result.stdout.strip()}")
        print(f"[local]  status={result.status} duration={result.duration_seconds:.2f}s")


if __name__ == "__main__":
    main()
