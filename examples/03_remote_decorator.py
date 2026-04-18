"""Example 3: @remote decorator — pass numpy array in, get result back.

Runs a small GPU matmul on Colab, returns the resulting numpy array to the
local process, then does local postprocessing.
"""

from __future__ import annotations

import numpy as np

from colab_cli import remote


@remote(gpu="t4")
def matmul_on_gpu(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    import numpy as _np
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ta = torch.as_tensor(a, device=device, dtype=torch.float32)
    tb = torch.as_tensor(b, device=device, dtype=torch.float32)
    out = (ta @ tb).cpu().numpy()
    return _np.asarray(out, dtype=_np.float32)


def main() -> None:
    rng = np.random.default_rng(42)
    a = rng.standard_normal((256, 256)).astype(np.float32)
    b = rng.standard_normal((256, 256)).astype(np.float32)

    print("[local]  shipping two 256x256 float32 matrices to Colab...")
    out = matmul_on_gpu(a, b)

    print(f"[local]  got back shape={out.shape} dtype={out.dtype}")
    print(f"[local]  frobenius norm = {np.linalg.norm(out):.4f}")


if __name__ == "__main__":
    main()
