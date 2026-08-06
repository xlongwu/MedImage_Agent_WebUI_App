from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.backend.app.api.advisor_routes import router as advisor_router
from src.backend.app.api.agent_lifecycle_routes import router as agent_lifecycle_router
from src.backend.app.api.agent_routes import router as agent_router
from src.backend.app.api.agent_task_routes import router as agent_task_router
from src.backend.app.api.approval_gate_routes import router as approval_gate_router
from src.backend.app.api.artifact_routes import router as artifact_router
from src.backend.app.api.audit_record_routes import router as audit_record_router
from src.backend.app.api.conversion_routes import router as conversion_router
from src.backend.app.api.dashboard_routes import router as dashboard_router
from src.backend.app.api.dependencies import get_project_store
from src.backend.app.api.desktop_routes import router as desktop_router
from src.backend.app.api.dpabi_routes import router as dpabi_router
from src.backend.app.api.execute_reviewed_routes import router as execute_reviewed_router
from src.backend.app.api.execution_ticket_routes import router as execution_ticket_router
from src.backend.app.api.experiment_routes import router as experiment_router
from src.backend.app.api.external_smoke_routes import router as external_smoke_router
from src.backend.app.api.gpu_routes import router as gpu_router
from src.backend.app.api.image_routes import router as image_router
from src.backend.app.api.llm_planner_routes import router as llm_planner_router
from src.backend.app.api.middleware import (
    APIVersionMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    register_exception_handlers,
)
from src.backend.app.api.memory_routes import router as memory_router
from src.backend.app.api.pipeline_routes import router as pipeline_router
from src.backend.app.api.plan_validator_routes import router as plan_validator_router
from src.backend.app.api.planner_routes import router as planner_router
from src.backend.app.api.preprocessing_routes import router as preprocessing_router
from src.backend.app.api.preset_routes import router as preset_router
from src.backend.app.api.project_history_routes import router as project_history_router
from src.backend.app.api.project_routes import router as project_router
from src.backend.app.api.qc_routes import router as qc_router
from src.backend.app.api.realdata_routes import router as realdata_router
from src.backend.app.api.routes import router
from src.backend.app.api.rsfmri_routes import router as rsfmri_router
from src.backend.app.api.session_routes import router as session_router
from src.backend.app.api.task_routes import router as task_router
from src.backend.app.api.tool_catalog_routes import router as tool_catalog_router
from src.backend.app.core.logging_config import setup_logging
from src.backend.app.core.config import ConfigService
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.runtime.agent_harness_scheduler import AgentHarnessScheduler
from src.backend.app.services.memory_candidate_service import MemoryCandidateService
from src.backend.app.services.memory_consolidation_service import MemoryConsolidationService
from src.backend.app.services.memory_maintenance_service import MemoryMaintenanceService
from src.backend.app.services.memory_llm_proposal_service import (
    MemoryLLMProposalService,
    build_memory_llm_provider_from_env,
)
from src.backend.app.services.memory_projection_service import MemoryProjectionService
from src.backend.app.services.memory_repository import MemoryRepository
from src.backend.app.version import API_DESCRIPTION, API_TITLE, APP_VERSION


logger = logging.getLogger(__name__)


def _run_memory_startup_reconcile() -> None:
    """Run one bounded, single-owner recovery pass; never a daemon loop."""

    config = ConfigService().memory
    if not config.enabled:
        return
    store = get_project_store()
    repository = MemoryRepository(config.store_path)
    if not repository.health_check().get("ok"):
        logger.error("memory_startup_degraded", extra={"error_code": "MEMORY_STORE_UNHEALTHY"})
        return
    maintenance = MemoryMaintenanceService(
        project_store=store, memory_repository=repository
    )
    llm_provider, llm_model = build_memory_llm_provider_from_env()
    candidate_service = MemoryCandidateService(
        project_store=store,
        memory_repository=repository,
        config=config,
        llm_proposal_service=MemoryLLMProposalService(
            config=config, provider=llm_provider, model_name=llm_model
        ),
    )
    consolidator = MemoryConsolidationService(
        project_store=store,
        memory_repository=repository,
        config=config,
        llm_proposal_service=MemoryLLMProposalService(
            config=config, provider=llm_provider, model_name=llm_model
        ),
    )
    projector = MemoryProjectionService(
        project_store=store, memory_repository=repository, config=config
    )
    for project in store.list_projects()[:100]:
        try:
            maintenance.reconcile_project(project_id=project.id)
            candidate_service.process_project(project_id=project.id, limit=100)
            consolidator.consolidate_project(project_id=project.id)
            if config.projection_enabled:
                projector.rebuild(project_id=project.id)
        except Exception as exc:
            logger.error(
                "memory_startup_project_failed",
                extra={"project_id": project.id, "error_code": type(exc).__name__},
            )


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Run one bounded recovery pass only when explicitly enabled."""
    if os.getenv("MEDIMAGE_AGENT_STARTUP_RECONCILE", "0") == "1":
        AgentTaskReconciler(get_project_store()).reconcile_incomplete_on_startup()
    if ConfigService().harness.enabled:
        AgentHarnessScheduler(get_project_store()).recover_once_on_startup()
    _run_memory_startup_reconcile()
    yield


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(
        title=API_TITLE,
        version=APP_VERSION,
        description=API_DESCRIPTION,
        lifespan=_lifespan,
    )

    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(APIVersionMiddleware)

    app.include_router(router)
    app.include_router(dpabi_router)
    app.include_router(rsfmri_router)
    app.include_router(agent_router)
    app.include_router(gpu_router)
    app.include_router(pipeline_router)
    app.include_router(session_router)
    app.include_router(advisor_router)
    app.include_router(experiment_router)
    app.include_router(artifact_router)
    app.include_router(realdata_router)
    app.include_router(conversion_router)
    app.include_router(preprocessing_router)
    app.include_router(qc_router)
    app.include_router(task_router)
    app.include_router(image_router)
    app.include_router(dashboard_router)
    app.include_router(planner_router)
    app.include_router(tool_catalog_router)
    app.include_router(plan_validator_router)
    app.include_router(llm_planner_router)
    app.include_router(approval_gate_router)
    app.include_router(execute_reviewed_router)
    app.include_router(execution_ticket_router)
    app.include_router(agent_lifecycle_router)
    app.include_router(agent_task_router)
    app.include_router(memory_router)
    app.include_router(audit_record_router)
    app.include_router(desktop_router)
    app.include_router(external_smoke_router)
    app.include_router(preset_router)
    app.include_router(project_router)
    app.include_router(project_history_router)

    return app


app = create_app()
