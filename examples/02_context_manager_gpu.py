"""Example 2: allocate a T4, run a torch GPU workload, stream output.

Falls back to CPU mode if a GPU isn't available on this account right now.
"""

from __future__ import annotations

import sys

from colab_cli import colab


GPU_CHECK = """
import torch
print("cuda_available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
    x = torch.randn(2000, 2000, device="cuda")
    y = torch.randn(2000, 2000, device="cuda")
    print("matmul_sum:", float((x @ y).sum().item()))
else:
    print("no cuda — skipping matmul")
"""


def main() -> None:
    accelerator = "t4" if "--cpu" not in sys.argv else None
    with colab(gpu=accelerator) as c:
        print(f"[local]  attached — accelerator={c.accelerator}")
        result = c.run(GPU_CHECK, source_name="gpu_check.py")
        sys.stdout.write(result.stdout)
        if result.status != "success":
            print(f"[local]  REMOTE ERROR: {result.error}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
