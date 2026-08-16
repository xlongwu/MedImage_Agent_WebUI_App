from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.backend.app.config.settings import ProjectSettings
from src.backend.app.core.config_schema import (
    AgentHarnessConfig,
    AgentModelPublicConfig,
    AgentModelRuntimeConfig,
    AppConfig,
    MemoryConfig,
    ServerConfig,
)


@dataclass(frozen=True)
class BackendSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    service_name: str = "medimage-agent-backend"
    api_version: str = "0.1.0"


class ConfigService:
    """Unified backend configuration loader with backwards-compatible sources."""

    def __init__(self, project_config_path: str | Path | None = None) -> None:
        self.server = ServerConfig.from_env()
        self.memory = MemoryConfig.from_env()
        self.harness = AgentHarnessConfig.from_env()
        self.model = AgentModelRuntimeConfig.from_env()
        self.project = (
            ProjectSettings.from_yaml(project_config_path)
            if project_config_path is not None
            else None
        )
        self.project_config_path = (
            str(Path(project_config_path).resolve())
            if project_config_path is not None
            else None
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> ConfigService:
        return cls(project_config_path=path)

    def snapshot(self) -> AppConfig:
        project_payload = None
        if self.project is not None:
            project_payload = {
                "runtime": self.project.runtime.__dict__,
                "third_party": self.project.third_party.__dict__,
                "safety": self.project.safety.__dict__,
                "source_path": self.project.source_path,
            }
        return AppConfig(
            server=self.server,
            memory=self.memory,
            harness=self.harness,
            model=AgentModelPublicConfig.from_runtime(self.model),
            project=project_payload,
            project_config_path=self.project_config_path,
        )


def get_backend_settings() -> BackendSettings:
    server = ConfigService().server
    return BackendSettings(
        host=server.host,
        port=server.port,
        service_name=server.service_name,
        api_version=server.api_version,
    )
