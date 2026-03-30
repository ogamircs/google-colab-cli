from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from colab_cli.cli import app
from colab_cli.models import RunResult, StatusResult, TokenData, UserInfo

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

    async def run_code(self, code: str, source_name: str = "inline.py", on_stream=None, secrets=None) -> RunResult:
        self.last_secrets = secrets
        return RunResult(
            status="success",
            exit_code=0,
            stdout="ok\n",
            duration_seconds=0.1,
            cells=[],
        )

    async def run_script(self, path: Path, on_stream=None, secrets=None) -> RunResult:
        self.last_secrets = secrets
        return RunResult(
            status="success",
            exit_code=0,
            stdout=path.read_text(),
            duration_seconds=0.1,
            cells=[],
        )

    async def run_notebook(self, path: Path, on_stream=None, on_cell_start=None, secrets=None) -> RunResult:
        self.last_secrets = secrets
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


class FakeTokenStore:
    def __init__(self, token: TokenData | None = None) -> None:
        self._token = token

    def load(self) -> TokenData | None:
        return self._token


class FakeCredentialManager:
    def __init__(self, *args, **kwargs) -> None:
        self.token_store = FakeTokenStore(
            TokenData(
                access_token="access",
                refresh_token="refresh",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                scope="openid",
                token_type="Bearer",
            )
        )

    def save_token(self, token) -> None:
        self.saved = token

    def clear(self) -> None:
        return None

    def get_valid_token(self):
        return self.token_store.load()


def _patch_auth(monkeypatch):
    """Apply common auth monkeypatches and return them for further customisation."""
    from colab_cli.models import AppConfig, OAuthConfig

    monkeypatch.setattr(
        "colab_cli.cli.auth.load_app_config",
        lambda: AppConfig(oauth=OAuthConfig(client_id="client.apps.googleusercontent.com", client_secret="secret")),
    )
    monkeypatch.setattr("colab_cli.cli.auth.CredentialManager", FakeCredentialManager)
    monkeypatch.setattr(
        "colab_cli.cli.auth.fetch_user_info",
        lambda access_token: UserInfo(email="user@example.com", name="Example User"),
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


def test_run_with_secret_flag(monkeypatch) -> None:
    mgr = FakeRuntimeManager()
    monkeypatch.setattr("colab_cli.cli.run.create_runtime_manager", lambda **kwargs: mgr)

    result = runner.invoke(app, ["run", "--code", "x", "--secret", "KEY=val", "--json"])

    assert result.exit_code == 0
    assert mgr.last_secrets == {"KEY": "val"}


def test_run_with_multiple_secrets(monkeypatch) -> None:
    mgr = FakeRuntimeManager()
    monkeypatch.setattr("colab_cli.cli.run.create_runtime_manager", lambda **kwargs: mgr)

    result = runner.invoke(app, ["run", "--code", "x", "-s", "A=1", "-s", "B=2", "--json"])

    assert result.exit_code == 0
    assert mgr.last_secrets == {"A": "1", "B": "2"}


def test_run_with_secrets_file(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=abc123\n")
    mgr = FakeRuntimeManager()
    monkeypatch.setattr("colab_cli.cli.run.create_runtime_manager", lambda **kwargs: mgr)

    result = runner.invoke(app, ["run", "--code", "x", "--secrets-file", str(env_file), "--json"])

    assert result.exit_code == 0
    assert mgr.last_secrets == {"TOKEN": "abc123"}


def test_run_secret_missing_equals(monkeypatch) -> None:
    monkeypatch.setattr("colab_cli.cli.run.create_runtime_manager", lambda **kwargs: FakeRuntimeManager())

    result = runner.invoke(app, ["run", "--code", "x", "--secret", "NOEQUALS"])

    assert result.exit_code != 0


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
    _patch_auth(monkeypatch)
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

    login_result = runner.invoke(app, ["auth", "login", "--no-browser"])
    whoami_result = runner.invoke(app, ["auth", "whoami", "--json"])

    assert login_result.exit_code == 0
    assert whoami_result.exit_code == 0
    assert "Logged in as user@example.com" in login_result.stderr
    assert '"email": "user@example.com"' in whoami_result.stdout


def test_auth_status_authenticated(monkeypatch) -> None:
    _patch_auth(monkeypatch)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "Authenticated as user@example.com" in result.stdout


def test_auth_status_refreshes_expired_token(monkeypatch) -> None:
    _patch_auth(monkeypatch)

    class RefreshingCredentialManager(FakeCredentialManager):
        def __init__(self, *args, **kwargs) -> None:
            self.token_store = FakeTokenStore(
                TokenData(
                    access_token="expired-access",
                    refresh_token="refresh",
                    expires_at=datetime.now(UTC) - timedelta(minutes=5),
                    scope="openid",
                    token_type="Bearer",
                )
            )

        def get_valid_token(self) -> TokenData:
            return TokenData(
                access_token="fresh-access",
                refresh_token="refresh",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                scope="openid",
                token_type="Bearer",
            )

    monkeypatch.setattr("colab_cli.cli.auth.CredentialManager", RefreshingCredentialManager)

    result = runner.invoke(app, ["auth", "status", "--json"])

    assert result.exit_code == 0
    import json

    data = json.loads(result.stdout)
    assert data["authenticated"] is True
    assert data["email"] == "user@example.com"
    assert data["expires_at"] is not None


def test_auth_status_not_authenticated(monkeypatch) -> None:
    _patch_auth(monkeypatch)

    # Override with a FakeCredentialManager whose token_store returns None
    class NoTokenCredentialManager(FakeCredentialManager):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.token_store = FakeTokenStore(None)

    monkeypatch.setattr("colab_cli.cli.auth.CredentialManager", NoTokenCredentialManager)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "Not authenticated" in result.stdout


def test_auth_status_json(monkeypatch) -> None:
    _patch_auth(monkeypatch)

    result = runner.invoke(app, ["auth", "status", "--json"])

    assert result.exit_code == 0
    import json

    data = json.loads(result.stdout)
    assert data["authenticated"] is True
    assert data["email"] == "user@example.com"
    assert data["expires_at"] is not None
