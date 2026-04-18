"""Example 5: push a local file to Colab, transform it remotely, pull result."""

from __future__ import annotations

from pathlib import Path

from colab_cli import colab


def main() -> None:
    local_in = Path("/tmp/colab_cli_in.txt")
    local_out = Path("/tmp/colab_cli_out.txt")
    local_in.write_text("hello world\nthis is a test\n")

    with colab(gpu=None) as c:
        c.push(local_in, "/content/in.txt")
        r = c.run(
            "open('/content/out.txt', 'w').write("
            "open('/content/in.txt').read().upper())",
            source_name="transform.py",
        )
        assert r.status == "success", r.error
        c.pull("/content/out.txt", local_out)

    print(f"[local]  input:  {local_in.read_text()!r}")
    print(f"[local]  output: {local_out.read_text()!r}")


if __name__ == "__main__":
    main()
