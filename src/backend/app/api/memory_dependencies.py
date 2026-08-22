"""Dependency boundaries for the independent Memory Domain."""

from __future__ import annotations

from typing import Protocol

from src.backend.app.core.config import ConfigService
from src.backend.app.schemas.memory import MemoryCandidate, MemoryEvent, MemoryItem
from src.backend.app.services.memory_repository import MemoryRepository


class MemoryStore(Protocol):
    def health_check(self) -> dict[str, object]: ...

    def list_items(
        self,
        *,
        project_id: str,
        status: str | None = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryItem]: ...

    def count_items(self, *, project_id: str, status: str | None = "active") -> int: ...

    def get_item(self, *, project_id: str, memory_id: str) -> MemoryItem | None: ...

    def list_candidates(
        self, *, project_id: str, status: str = "proposed", limit: int = 100
    ) -> list[MemoryCandidate]: ...

    def count_candidates(self, *, project_id: str, status: str = "proposed") -> int: ...

    def get_candidate(
        self, *, project_id: str, candidate_id: str
    ) -> MemoryCandidate | None: ...

    def list_events(
        self, *, project_id: str, limit: int = 100, after_sequence: int = 0
    ) -> list[MemoryEvent]: ...

    def count_events(self, *, project_id: str, after_sequence: int = 0) -> int: ...


def get_memory_config():
    return ConfigService().memory


def get_memory_store() -> MemoryStore:
    """Create a short-lived repository facade; connections are transaction-scoped."""

    return MemoryRepository(get_memory_config().store_path)


def get_readonly_memory_store() -> MemoryStore:
    """Open the existing Memory store without creating or migrating it."""

    return MemoryRepository(get_memory_config().store_path, read_only=True)
