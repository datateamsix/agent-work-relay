from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

AuthMode = Literal["oauth", "static"]
RuntimeEnv = Literal["local", "test", "production"]

AWR_SCOPES = ("awr:plan", "awr:read", "awr:refresh")
TOOL_SCOPES = {
    "submit_prompt_for_planning": "awr:plan",
    "refresh_planning": "awr:refresh",
    "get_plan": "awr:read",
    "get_work_order_timeline": "awr:read",
}


class SettingsError(ValueError):
    """Runtime configuration is incomplete or unsafe for the selected profile."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer.") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    env: RuntimeEnv = "local"
    auth_mode: AuthMode = "static"
    public_base_url: str = "http://127.0.0.1:8080"
    oauth_issuer: str | None = None
    oauth_audience: str | None = None
    oauth_jwks_url: str | None = None
    static_token: str | None = None
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    storage: str = "sqlite"
    sqlite_path: str = ".awr/awr.db"
    gcp_project: str | None = None
    firestore_database: str = "(default)"
    executor: str = "recording_cursor"
    repository_url: str | None = None
    base_ref: str = "main"
    cursor_api_key: str | None = None
    cursor_api_base_url: str = "https://api.cursor.com"
    log_level: str = "INFO"
    artifact_root: str = ".awr/artifacts"
    artifact_max_bytes: int = 10 * 1024 * 1024

    extra_jwks: dict[str, object] = field(default_factory=dict, compare=False)

    @property
    def resource_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/mcp"

    @property
    def resource_metadata_path(self) -> str:
        return "/.well-known/oauth-protected-resource/mcp"

    @property
    def resource_metadata_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.resource_metadata_path}"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def validate(self) -> None:
        if self.auth_mode == "static":
            if self.is_production:
                raise SettingsError("Static token authentication cannot be enabled in production.")
            if not self.static_token:
                raise SettingsError("AWR_STATIC_TOKEN is required when AWR_AUTH_MODE=static.")
            return
        if self.auth_mode != "oauth":
            raise SettingsError(f"Unsupported AWR_AUTH_MODE: {self.auth_mode!r}.")
        if not self.oauth_issuer:
            raise SettingsError("AWR_OAUTH_ISSUER is required when AWR_AUTH_MODE=oauth.")
        if not self.oauth_audience:
            raise SettingsError("AWR_OAUTH_AUDIENCE is required when AWR_AUTH_MODE=oauth.")
        if self.is_production:
            if not self.public_base_url.startswith("https://"):
                raise SettingsError("AWR_PUBLIC_BASE_URL must be HTTPS in production.")
            if self.storage != "firestore":
                raise SettingsError("Production must use AWR_STORAGE=firestore.")
            if self.static_token:
                raise SettingsError("AWR_STATIC_TOKEN must not be set in production.")

    def protected_resource_metadata(self) -> dict[str, object]:
        authorization_servers = [self.oauth_issuer] if self.oauth_issuer else [self.public_base_url]
        return {
            "resource": self.resource_url,
            "authorization_servers": authorization_servers,
            "scopes_supported": list(AWR_SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "Agent Work Relay",
            "resource_documentation": f"{self.public_base_url.rstrip('/')}/",
        }

    @classmethod
    def from_env(cls) -> Settings:
        env = (_env("AWR_ENV", "local") or "local").lower()
        if env not in {"local", "test", "production"}:
            raise SettingsError("AWR_ENV must be local, test, or production.")
        auth_mode = (_env("AWR_AUTH_MODE", "static") or "static").lower()
        if auth_mode not in {"oauth", "static"}:
            raise SettingsError("AWR_AUTH_MODE must be oauth or static.")
        public_base_url = (
            _env("AWR_PUBLIC_BASE_URL", "http://127.0.0.1:8080") or "http://127.0.0.1:8080"
        )
        hosts = _env("AWR_ALLOWED_HOSTS")
        allowed_hosts = (
            tuple(part.strip() for part in hosts.split(",") if part.strip())
            if hosts
            else ("127.0.0.1", "localhost", "testserver")
        )
        settings = cls(
            env=env,  # type: ignore[arg-type]
            auth_mode=auth_mode,  # type: ignore[arg-type]
            public_base_url=public_base_url,
            oauth_issuer=_env("AWR_OAUTH_ISSUER"),
            oauth_audience=_env("AWR_OAUTH_AUDIENCE", public_base_url.rstrip("/") + "/mcp"),
            oauth_jwks_url=_env("AWR_OAUTH_JWKS_URL"),
            static_token=_env("AWR_STATIC_TOKEN"),
            allowed_hosts=allowed_hosts,
            storage=_env("AWR_STORAGE", "sqlite") or "sqlite",
            sqlite_path=_env("AWR_SQLITE_PATH", ".awr/awr.db") or ".awr/awr.db",
            gcp_project=_env("GOOGLE_CLOUD_PROJECT") or _env("AWR_GCP_PROJECT"),
            firestore_database=_env("FIRESTORE_DATABASE", "(default)") or "(default)",
            executor=_env("AWR_EXECUTOR", "recording_cursor") or "recording_cursor",
            repository_url=_env("AWR_REPOSITORY_URL"),
            base_ref=_env("AWR_BASE_REF", "main") or "main",
            cursor_api_key=_env("CURSOR_API_KEY"),
            cursor_api_base_url=_env("CURSOR_API_BASE_URL", "https://api.cursor.com")
            or "https://api.cursor.com",
            log_level=_env("AWR_LOG_LEVEL", "INFO") or "INFO",
            artifact_root=_env("AWR_ARTIFACT_ROOT", ".awr/artifacts") or ".awr/artifacts",
            artifact_max_bytes=_int_env("AWR_ARTIFACT_MAX_BYTES", 10 * 1024 * 1024),
        )
        settings.validate()
        return settings
