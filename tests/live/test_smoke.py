from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from colab_cli.config import load_app_config
from colab_cli.core.auth.credentials import CredentialManager
from colab_cli.core.auth.oauth import fetch_user_info
from colab_cli.core.runtime import create_runtime_manager

pytestmark = pytest.mark.live


def _require_live_setup() -> None:
    if os.getenv("COLAB_LIVE") != "1":
        pytest.skip("Set COLAB_LIVE=1 to enable Google Colab smoke tests.")
    config_path = Path.home() / ".config" / "colab-cli" / "config.toml"
    token_path = Path.home() / ".config" / "colab-cli" / "token.json"
    if not config_path.exists() or not token_path.exists():
        pytest.skip("Expected ~/.config/colab-cli/config.toml and token.json for live tests.")


def test_live_whoami() -> None:
    _require_live_setup()
    config = load_app_config()
    token = CredentialManager(config=config).get_valid_token()
    user = fetch_user_info(token.access_token)

    assert user.email


def test_live_connect_run_files_and_disconnect(tmp_path: Path) -> None:
    _require_live_setup()
    manager = create_runtime_manager(spawn_keepalive=False)
    local_script = tmp_path / "smoke.py"
    local_script.write_text("print('hello from script')\n")
    local_notebook = tmp_path / "smoke.ipynb"
    local_notebook.write_text(
        '{"cells":[{"cell_type":"code","source":["print(\\"hello from notebook\\")"]}]}'
    )
    upload_path = tmp_path / "upload.txt"
    upload_path.write_text("hello")
    download_path = tmp_path / "download.txt"

    async def run_flow() -> None:
        status = await manager.connect(accelerator="t4")
        assert status.connected is True
        assert manager.status().connected is True

        inline_result = await manager.run_code("print('hello from inline')")
        script_result = await manager.run_script(local_script)
        notebook_result = await manager.run_notebook(local_notebook)

        assert inline_result.status == "success"
        assert script_result.status == "success"
        assert notebook_result.status == "success"

        await manager.push_file(upload_path, "/content/upload.txt")
        items = await manager.list_files("/content")
        assert any(item.name == "upload.txt" for item in items)

        await manager.pull_file("/content/upload.txt", download_path)
        assert download_path.read_text() == "hello"

        disconnected = await manager.disconnect()
        assert disconnected.connected is False

    asyncio.run(run_flow())


def test_live_api_roundtrip() -> None:
    _require_live_setup()
    from colab_cli import RemoteExecutionError, colab, remote

    with colab(gpu="t4") as c:
        result = c.run("import torch; print(torch.cuda.is_available())")
        assert result.status == "success"
        assert "True" in result.stdout

    @remote(gpu="t4")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(3, 4) == 7

    @remote(gpu="t4")
    def boom() -> None:
        raise RuntimeError("remote boom")

    with pytest.raises(RemoteExecutionError):
        boom()
