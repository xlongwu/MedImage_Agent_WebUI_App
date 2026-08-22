from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from src.backend.app.schemas.desktop import (
    PipelineRunRequest,
    TaskDetail,
    TaskEvent,
    TaskStreamMessage,
)
from src.backend.app.services.mock_store import mock_store, utc_now_iso


class TaskManager:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[TaskStreamMessage]]] = {}

    def create_pipeline_task(self, request: PipelineRunRequest) -> TaskDetail:
        project = mock_store.get_project(request.project_id)
        if not project:
            raise KeyError(request.project_id)
        task_id = f"task-{uuid4().hex[:10]}"
        now = datetime.now(UTC)
        task = TaskDetail(
            id=task_id,
            run_name=f"Run_{now.strftime('%Y_%m%d_%H%M%S')}",
            pipeline=request.pipeline_id.replace("-", " ").title(),
            dataset=project.name,
            status="running",
            progress=0,
            started_at=now.strftime("%H:%M"),
            duration="00:00:00",
            owner="Dr. Alex Morgan",
            logs=["Pipeline request accepted"],
            result_path=None,
            execution_mode=request.execution_mode,
            project_id=request.project_id,
            pipeline_id=request.pipeline_id,
            model_id=request.model_id,
            input_sequences=request.input_sequences,
            output_type=request.output_type,
            updated_at=utc_now_iso(),
        )
        created = mock_store.add_task(task)
        mock_store.append_task_event(
            task_id=created.id,
            status=created.status,
            progress=created.progress,
            message="Pipeline request accepted",
            result_path=created.result_path,
            source="task_manager",
            metadata={"execution_mode": request.execution_mode},
        )
        return created

    async def update_task(
        self,
        task_id: str,
        *,
        status: str,
        progress: int,
        message: str,
        result_path: str | None = None,
        source: str = "task_manager",
        metadata: dict[str, object] | None = None,
    ) -> TaskDetail:
        current = mock_store.get_task(task_id)
        if not current:
            raise KeyError(task_id)
        logs = [*current.logs, message]
        updated = mock_store.update_task(
            task_id,
            status=status,
            progress=progress,
            logs=logs,
            result_path=result_path if result_path is not None else current.result_path,
        )
        if not updated:
            raise KeyError(task_id)
        mock_store.append_task_event(
            task_id=task_id,
            status=updated.status,
            progress=updated.progress,
            message=message,
            result_path=updated.result_path,
            source=source,
            metadata=metadata,
        )
        await self.publish(
            TaskStreamMessage(
                task_id=task_id,
                status=updated.status,
                progress=updated.progress,
                message=message,
                timestamp=utc_now_iso(),
                result_path=updated.result_path,
            )
        )
        return updated

    def list_events(self, task_id: str) -> list[TaskEvent]:
        return mock_store.list_task_events(task_id)

    async def publish(self, message: TaskStreamMessage) -> None:
        queues = list(self._subscribers.get(message.task_id, set()))
        for queue in queues:
            await queue.put(message)

    async def subscribe(self, task_id: str) -> asyncio.Queue[TaskStreamMessage]:
        queue: asyncio.Queue[TaskStreamMessage] = asyncio.Queue()
        self._subscribers.setdefault(task_id, set()).add(queue)
        task = mock_store.get_task(task_id)
        if task:
            await queue.put(
                TaskStreamMessage(
                    task_id=task.id,
                    status=task.status,
                    progress=task.progress,
                    message=task.logs[-1] if task.logs else "Task stream connected",
                    timestamp=task.updated_at,
                    result_path=task.result_path,
                )
            )
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[TaskStreamMessage]) -> None:
        queues = self._subscribers.get(task_id)
        if not queues:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(task_id, None)


task_manager = TaskManager()
