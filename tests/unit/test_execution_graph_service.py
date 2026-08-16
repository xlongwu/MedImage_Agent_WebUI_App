from __future__ import annotations

from types import SimpleNamespace

from src.backend.app.runtime.state_store import write_node_state
from src.backend.app.services.execution_graph_service import ExecutionGraphService


def _plan() -> dict:
    return {
        "pipeline_id": "graph-test",
        "nodes": [
            {"id": "contract_smoke", "name": "Inspect", "depends_on": []},
            {"id": "data_inspection", "name": "Analyse", "depends_on": ["contract_smoke"]},
        ],
    }


class _Store:
    def __init__(self, project, plan, link=None):
        self.project, self.plan, self.link = project, plan, link

    def get_project(self, project_id):
        return self.project if project_id == self.project.id else None

    def get_reviewed_plan(self, reviewed_plan_id):
        return self.plan if reviewed_plan_id == self.plan.reviewed_plan_id else None

    def get_run_link_by_run_id(self, project_id, run_id):
        return self.link if self.link and project_id == self.project.id and run_id == self.link.run_id else None


def test_preview_graph_preserves_branch_structure_and_is_deterministic(tmp_path):
    project = SimpleNamespace(id="project-1", metadata={"project_dir": str(tmp_path)})
    graph = ExecutionGraphService(_Store(project, None)).build_preview_graph(project_id="project-1", plan=_plan())

    assert [node.node_id for node in graph.nodes] == ["contract_smoke", "data_inspection"]
    assert [(edge.source_node_id, edge.target_node_id) for edge in graph.edges] == [("contract_smoke", "data_inspection")]
    assert graph.run_id is None
    assert all(node.state == "pending" for node in graph.nodes)
    assert graph.structure_hash == ExecutionGraphService(_Store(project, None)).build_preview_graph(project_id="project-1", plan=_plan()).structure_hash


def test_run_graph_aggregates_subject_states_and_marks_stale_running(tmp_path):
    config = tmp_path / "project.yaml"
    config.write_text("""runtime:\n  work_dir: ./work\n  log_dir: ./logs\nthird_party:\n  spm_dir: ./spm\n  dpabi_dir: ./dpabi\n""", encoding="utf-8")
    dataset_index = tmp_path / "dataset_index.json"
    dataset_index.write_text('{"subjects": [{"subject_id": "sub-01"}, {"subject_id": "sub-02"}, {"subject_id": "sub-03"}]}', encoding="utf-8")
    project = SimpleNamespace(id="project-1", metadata={"project_dir": str(tmp_path), "rawdata_dir": str(tmp_path / "rawdata")})
    reviewed = SimpleNamespace(reviewed_plan_id="plan-1", project_id="project-1", plan_hash="hash-1", payload={"plan": _plan()}, dataset_index_path=str(dataset_index))
    link = SimpleNamespace(project_id="project-1", reviewed_plan_id="plan-1", run_id="run-1", project_config_path=str(config), status="SUCCEEDED", payload={"plan_hash": "hash-1"}, summary_path=None)
    write_node_state("run-1", "contract_smoke", "sub-01", "SUCCESS", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:02+00:00", {"ok": True}, str(tmp_path / "work"))
    write_node_state("run-1", "contract_smoke", "sub-02", "RUNNING", "2026-01-01T00:00:01+00:00", None, {}, str(tmp_path / "work"))

    graph = ExecutionGraphService(_Store(project, reviewed, link)).build_run_graph(project_id="project-1", run_id="run-1")
    node = graph.nodes[0]

    assert node.state == "running"
    assert node.subject_summary is not None
    assert node.subject_summary.observed == 2
    assert node.subject_summary.total == 3
    assert node.subject_summary.succeeded == 1
    assert node.subject_summary.running == 1
    assert "EXECUTION_GRAPH_STALE_RUNNING_NODE" in graph.warnings
    assert graph.graph_status == "partial"


def test_running_record_is_atomically_replaced_without_losing_started_at(tmp_path):
    path = write_node_state("run-1", "contract_smoke", "project", "RUNNING", "2026-01-01T00:00:00+00:00", None, {}, str(tmp_path))
    write_node_state("run-1", "contract_smoke", "project", "SUCCESS", "2026-01-01T00:00:10+00:00", "2026-01-01T00:00:11+00:00", {"ok": True, "outputs": ["safe-output"]}, str(tmp_path))
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "SUCCESS"
    assert payload["started_at"] == "2026-01-01T00:00:00+00:00"
    assert payload["ended_at"] == "2026-01-01T00:00:11+00:00"
