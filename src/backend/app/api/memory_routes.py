"""Thin project-scoped HTTP adapter for the independent Memory Domain."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.api.memory_dependencies import (
    MemoryStore,
    get_memory_config,
    get_memory_store,
)
from src.backend.app.core.exceptions import NotFoundError, PipelineError
from src.backend.app.schemas.memory import (
    MemoryCandidatePage,
    MemoryConsentStatus,
    MemoryContext,
    MemoryContextPreviewRequest,
    MemoryEventPage,
    MemoryItemDetail,
    MemoryPage,
    MutateMemoryItemRequest,
    RememberMemoryRequest,
    ReviewMemoryCandidateRequest,
    SetMemoryConsentRequest,
)
from src.backend.app.services.memory_consolidation_service import (
    MemoryConsolidationService,
)
from src.backend.app.services.memory_management_service import MemoryManagementService
from src.backend.app.services.memory_llm_proposal_service import (
    MemoryLLMProposalService,
    build_memory_llm_provider_from_env,
)
from src.backend.app.services.memory_repository import MemoryRepositoryError
from src.backend.app.services.memory_retrieval_service import MemoryRetrievalService

router = APIRouter(prefix="/api/projects/{project_id}/memory", tags=["memory"])
_PRINCIPAL = "desktop-local-user"


def _map_error(exc: Exception):
    if isinstance(exc, MemoryRepositoryError):
        if exc.code.endswith("NOT_FOUND"):
            raise NotFoundError(str(exc), code=exc.code) from exc
        raise PipelineError(str(exc), code=exc.code) from exc
    raise_api_error(exc)


def _manager(store, repository, config) -> MemoryManagementService:
    return MemoryManagementService(
        project_store=store, memory_repository=repository, config=config
    )


def _require_project(store, project_id: str) -> None:
    if store.get_project(project_id) is None:
        raise NotFoundError("MEMORY_PROJECT_NOT_FOUND", code="MEMORY_PROJECT_NOT_FOUND")


@router.get("/consent", response_model=MemoryConsentStatus)
def get_memory_consent(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
    repository: MemoryStore = Depends(get_memory_store),
    config=Depends(get_memory_config),
) -> MemoryConsentStatus:
    _require_project(store, project_id)
    consent = store.get_memory_consent(project_id)
    health = MemoryRetrievalService(
        repository=repository, project_store=store, config=config
    ).operational_health(project_id=project_id, consent=consent)
    return MemoryConsentStatus(
        project_id=project_id,
        status=health["status"],
        available=bool(config.enabled and health["store_healthy"]),
        generation_available=bool(health["generation_available"]),
        use_available=bool(health["use_available"]),
        generate_enabled=bool(consent.get("generate_enabled")),
        use_enabled=bool(consent.get("use_enabled")),
        consent_epoch=int(consent.get("consent_epoch") or 0),
        outbox_cutoff_sequence=int(consent.get("outbox_cutoff_sequence") or 0),
        updated_at=consent.get("updated_at"),
        degraded_reason=health["degraded_reason"],
        retrieval_policy_version="memory-retrieval-v1",
        store_healthy=bool(health["store_healthy"]),
        outbox_max_sequence=int(health["outbox_max_sequence"]),
        processed_outbox_sequence=int(health["processed_outbox_sequence"]),
        outbox_lag=int(health["outbox_lag"]),
        retry_jobs=int(health["retry_jobs"]),
        dead_letter_jobs=int(health["dead_letter_jobs"]),
        active_leases=int(health["active_leases"]),
        expired_leases=int(health["expired_leases"]),
        pending_forget_records=int(health["pending_forget_records"]),
        last_forget_wal_truncate_at=health["last_forget_wal_truncate_at"],
    )


@router.post("/consent", response_model=MemoryConsentStatus)
def set_memory_consent(
    project_id: str,
    request: SetMemoryConsentRequest,
    store: ProjectStore = Depends(get_project_store),
    repository: MemoryStore = Depends(get_memory_store),
    config=Depends(get_memory_config),
) -> MemoryConsentStatus:
    try:
        if not config.enabled:
            raise MemoryRepositoryError("MEMORY_DISABLED")
        store.set_memory_consent(
            project_id=project_id,
            command_id=request.command_id,
            principal=_PRINCIPAL,
            generate_enabled=request.generate_enabled,
            use_enabled=request.use_enabled,
        )
        return get_memory_consent(project_id, store, repository, config)
    except Exception as exc:
        _map_error(exc)


@router.get("/items", response_model=MemoryPage)
def list_memory_items(
    project_id: str,
    status: str | None = Query(default="active"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    store: ProjectStore = Depends(get_project_store),
    repository: MemoryStore = Depends(get_memory_store),
) -> MemoryPage:
    _require_project(store, project_id)
    items = repository.list_items(
        project_id=project_id, status=status, limit=limit, offset=offset
    )
    return MemoryPage(
        items=tuple(items),
        total=repository.count_items(project_id=project_id, status=status),
    )


@router.get("/items/{memory_id}", response_model=MemoryItemDetail)
def get_memory_item(
    project_id: str,
    memory_id: str,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
) -> MemoryItemDetail:
    _require_project(store, project_id)
    item = repository.get_item(project_id=project_id, memory_id=memory_id)
    if item is None:
        raise NotFoundError("MEMORY_ITEM_NOT_FOUND", code="MEMORY_ITEM_NOT_FOUND")
    events = tuple(
        event
        for event in repository.list_events(project_id=project_id, limit=200)
        if event.memory_id == memory_id
    )
    return MemoryItemDetail(
        item=item,
        revisions=tuple(
            repository.list_item_revisions(
                project_id=project_id, memory_id=memory_id
            )
        ),
        events=events,
    )


@router.get("/candidates", response_model=MemoryCandidatePage)
def list_memory_candidates(
    project_id: str,
    status: str = Query(default="proposed"),
    limit: int = Query(default=100, ge=1, le=200),
    store: ProjectStore = Depends(get_project_store),
    repository: MemoryStore = Depends(get_memory_store),
) -> MemoryCandidatePage:
    _require_project(store, project_id)
    items = repository.list_candidates(
        project_id=project_id, status=status, limit=limit
    )
    return MemoryCandidatePage(
        items=tuple(items),
        total=repository.count_candidates(project_id=project_id, status=status),
    )


@router.get("/events", response_model=MemoryEventPage)
def list_memory_events(
    project_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    store: ProjectStore = Depends(get_project_store),
    repository: MemoryStore = Depends(get_memory_store),
) -> MemoryEventPage:
    _require_project(store, project_id)
    items = repository.list_events(
        project_id=project_id, after_sequence=after, limit=limit
    )
    return MemoryEventPage(
        items=tuple(items),
        total=repository.count_events(project_id=project_id, after_sequence=after),
    )


@router.post("/remember")
def remember_memory(
    project_id: str,
    request: RememberMemoryRequest,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
    config=Depends(get_memory_config),
):
    try:
        return _manager(store, repository, config).remember(
            project_id=project_id,
            command_id=request.command_id,
            principal=_PRINCIPAL,
            kind=request.kind,
            key=request.key,
            value=request.value,
            summary=request.summary,
            impact_class=request.impact_class,
        )
    except Exception as exc:
        _map_error(exc)


def _review_candidate(
    *, project_id, candidate_id, request, accept, store, repository, config
):
    result = _manager(store, repository, config).review_candidate(
        project_id=project_id,
        candidate_id=candidate_id,
        command_id=request.command_id,
        principal=_PRINCIPAL,
        accept=accept,
        expected_candidate_version=request.expected_candidate_version,
        candidate_hash=request.candidate_hash,
        edited_value=request.edited_value,
        edited_summary=request.edited_summary,
        reason=request.reason,
    )
    if accept:
        llm_provider, llm_model = build_memory_llm_provider_from_env()
        result = {
            **result,
            "consolidation": MemoryConsolidationService(
                project_store=store,
                memory_repository=repository,
                config=config,
                llm_proposal_service=MemoryLLMProposalService(
                    config=config, provider=llm_provider, model_name=llm_model
                ),
            ).consolidate_project(project_id=project_id),
        }
    return result


@router.post("/candidates/{candidate_id}/accept")
def accept_memory_candidate(
    project_id: str,
    candidate_id: str,
    request: ReviewMemoryCandidateRequest,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
    config=Depends(get_memory_config),
):
    try:
        return _review_candidate(
            project_id=project_id,
            candidate_id=candidate_id,
            request=request,
            accept=True,
            store=store,
            repository=repository,
            config=config,
        )
    except Exception as exc:
        _map_error(exc)


@router.post("/candidates/{candidate_id}/reject")
def reject_memory_candidate(
    project_id: str,
    candidate_id: str,
    request: ReviewMemoryCandidateRequest,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
    config=Depends(get_memory_config),
):
    try:
        return _review_candidate(
            project_id=project_id,
            candidate_id=candidate_id,
            request=request,
            accept=False,
            store=store,
            repository=repository,
            config=config,
        )
    except Exception as exc:
        _map_error(exc)


@router.post("/items/{memory_id}/pin")
def pin_memory_item(
    project_id: str,
    memory_id: str,
    request: MutateMemoryItemRequest,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
    config=Depends(get_memory_config),
):
    try:
        if request.pinned is None:
            raise MemoryRepositoryError("MEMORY_PIN_TARGET_REQUIRED")
        return _manager(store, repository, config).set_pinned(
            project_id=project_id,
            memory_id=memory_id,
            command_id=request.command_id,
            principal=_PRINCIPAL,
            expected_item_version=request.expected_item_version,
            pinned=request.pinned,
        )
    except Exception as exc:
        _map_error(exc)


@router.post("/items/{memory_id}/forget")
def forget_memory_item(
    project_id: str,
    memory_id: str,
    request: MutateMemoryItemRequest,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
    config=Depends(get_memory_config),
):
    try:
        if not request.expected_revision_hash:
            raise MemoryRepositoryError("MEMORY_REVISION_HASH_REQUIRED")
        return _manager(store, repository, config).forget(
            project_id=project_id,
            memory_id=memory_id,
            command_id=request.command_id,
            principal=_PRINCIPAL,
            expected_item_version=request.expected_item_version,
            expected_revision_hash=request.expected_revision_hash,
        )
    except Exception as exc:
        _map_error(exc)


@router.post("/items/{memory_id}/restore")
def restore_memory_item(
    project_id: str,
    memory_id: str,
    request: MutateMemoryItemRequest,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
    config=Depends(get_memory_config),
):
    try:
        if not request.expected_revision_hash or request.value is None or not request.summary:
            raise MemoryRepositoryError("MEMORY_RESTORE_CONTENT_REQUIRED")
        return _manager(store, repository, config).restore(
            project_id=project_id,
            memory_id=memory_id,
            command_id=request.command_id,
            principal=_PRINCIPAL,
            expected_item_version=request.expected_item_version,
            expected_revision_hash=request.expected_revision_hash,
            value=request.value,
            summary=request.summary,
        )
    except Exception as exc:
        _map_error(exc)


@router.post("/context-preview", response_model=MemoryContext)
def preview_memory_context(
    project_id: str,
    request: MemoryContextPreviewRequest,
    store: ProjectStore = Depends(get_project_store),
    repository=Depends(get_memory_store),
    config=Depends(get_memory_config),
) -> MemoryContext:
    # Intentionally read-only: no extraction, consolidation, projection, or
    # reconcile is permitted from this endpoint.
    _require_project(store, project_id)
    try:
        return MemoryRetrievalService(
            repository=repository, project_store=store, config=config
        ).build_context(project_id=project_id, goal=request.goal)
    except Exception as exc:
        _map_error(exc)
