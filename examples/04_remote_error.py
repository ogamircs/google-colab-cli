"""Example 4: remote exception re-raised locally with traceback preserved."""

from __future__ import annotations

from colab_cli import RemoteExecutionError, remote


@remote(gpu=None)
def explode(x: int) -> int:
    if x < 0:
        raise ValueError(f"negative input: {x}")
    return x * x


def main() -> None:
    print(f"[local]  explode(5) = {explode(5)}")
    try:
        explode(-1)
    except RemoteExecutionError as exc:
        print(f"[local]  got RemoteExecutionError: {exc}")
        print(f"[local]  __cause__ is {type(exc.__cause__).__name__}: {exc.__cause__}")
        print("[local]  remote traceback:")
        for line in exc.remote_traceback:
            print("  " + line.rstrip())


if __name__ == "__main__":
    main()
