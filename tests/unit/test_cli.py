from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from colab_cli.cli import app
from colab_cli.models import RunResult, StatusResult, UserInfo

runner = CliRunner()


class FakeRuntimeManager:
    def status(self) -> StatusResult:
        return StatusResult(
            connected=True,
            endpoint="endpoint-123",
            accelerator="T4",
            proxy_expires_at=datetime.now(UTC) + timedelta(hours=1),
            notebook_hash="hash",
        )

    async def connect(self, accelerator: str | None = None) -> StatusResult:
        assert accelerator == "t4"
        return self.status()

    async def disconnect(self) -> StatusResult:
        return StatusResult(connected=False)

    async def run_code(self, code: str, source_name: str = "inline.py", on_stream=None) -> RunResult:
        assert code == "print('hi')"
        return RunResult(
            status="success",
            exit_code=0,
            stdout="hi\n",
            duration_seconds=0.1,
            cells=[],
        )

    async def run_script(self, path: Path, on_stream=None) -> RunResult:
        return RunResult(
            status="success",
            exit_code=0,
            stdout=path.read_text(),
            duration_seconds=0.1,
            cells=[],
        )

    async def run_notebook(self, path: Path, on_stream=None, on_cell_start=None) -> RunResult:
        return RunResult(
            status="success",
            exit_code=0,
            stdout=path.read_text(),
            duration_seconds=0.1,
            cells=[],
        )

    async def push_file(self, local_path: Path, remote_path: str) -> None:
        assert local_path.exists()
        assert remote_path.startswith("/content/")

    async def pull_file(self, remote_path: str, local_path: Path) -> Path:
        local_path.write_text("downloaded")
        return local_path

    async def list_files(self, remote_path: str = ""):
        from colab_cli.models import JupyterContent

        return [JupyterContent(name="file.txt", path=f"{remote_path}/file.txt", type="file")]


class FakeCredentialManager:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def save_token(self, token) -> None:
        self.saved = token

    def clear(self) -> None:
        return None

    def get_valid_token(self):
        from colab_cli.models import TokenData

        return TokenData(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="openid",
            token_type="Bearer",
        )


def test_status_json(monkeypatch) -> None:
    monkeypatch.setattr("colab_cli.cli.connect.create_runtime_manager", lambda **kwargs: FakeRuntimeManager())

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    assert '"connected": true' in result.stdout


def test_connect_command(monkeypatch) -> None:
    monkeypatch.setattr("colab_cli.cli.connect.create_runtime_manager", lambda **kwargs: FakeRuntimeManager())

    result = runner.invoke(app, ["connect", "--gpu", "t4"])

    assert result.exit_code == 0
    assert "Connected to endpoint-123" in result.stdout


def test_run_inline_json(monkeypatch) -> None:
    monkeypatch.setattr("colab_cli.cli.run.create_runtime_manager", lambda **kwargs: FakeRuntimeManager())

    result = runner.invoke(app, ["run", "--code", "print('hi')", "--json"])

    assert result.exit_code == 0
    assert '"status": "success"' in result.stdout


def test_push_pull_and_ls(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("colab_cli.cli.files.create_runtime_manager", lambda **kwargs: FakeRuntimeManager())
    upload = tmp_path / "upload.txt"
    upload.write_text("hello")
    download = tmp_path / "download.txt"

    push_result = runner.invoke(app, ["push", str(upload), "/content/upload.txt"])
    pull_result = runner.invoke(app, ["pull", "/content/upload.txt", str(download)])
    ls_result = runner.invoke(app, ["ls", "/content", "--json"])

    assert push_result.exit_code == 0
    assert pull_result.exit_code == 0
    assert ls_result.exit_code == 0
    assert download.read_text() == "downloaded"
    assert '"items"' in ls_result.stdout


def test_login_and_whoami(monkeypatch) -> None:
    from colab_cli.models import AppConfig, OAuthConfig, TokenData

    monkeypatch.setattr(
        "colab_cli.cli.auth.load_app_config",
        lambda: AppConfig(oauth=OAuthConfig(client_id="client.apps.googleusercontent.com", client_secret="secret")),
    )
    monkeypatch.setattr("colab_cli.cli.auth.CredentialManager", FakeCredentialManager)
    monkeypatch.setattr(
        "colab_cli.cli.auth.run_oauth_login",
        lambda config, open_browser=True: TokenData(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="openid email profile",
            token_type="Bearer",
        ),
    )
    monkeypatch.setattr(
        "colab_cli.cli.auth.fetch_user_info",
        lambda access_token: UserInfo(email="user@example.com", name="Example User"),
    )

    login_result = runner.invoke(app, ["login", "--no-browser"])
    whoami_result = runner.invoke(app, ["whoami", "--json"])

    assert login_result.exit_code == 0
    assert whoami_result.exit_code == 0
    assert "Logged in as user@example.com" in login_result.stderr
    assert '"email": "user@example.com"' in whoami_result.stdout
