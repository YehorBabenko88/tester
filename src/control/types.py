"""Static wire types; contracts.py validates them at every untrusted boundary."""
from typing import Literal, TypedDict

Action = Literal["instrument.search", "history.coverage", "history.download",
                 "chart.navigate", "chart.overlay", "analytics.run", "strategy.validate",
                 "backtest.run", "backtest.compare", "system.health",
                 "system.config.preview", "system.config.apply", "dev.change.request"]
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]


class ConfigChanges(TypedDict, total=False):
    max_workers: int
    max_downloads: int
    memory_mb: int


class Parameters(TypedDict, total=False):
    query: str
    limit: int
    cursor: str
    interval: str
    data_kind: Literal["trade", "mark", "index", "premium"]
    run_id: str
    run_ids: list[str]
    revision: int
    analysis_id: str
    strategy_id: str
    strategy_revision: str
    validation_id: str
    expected_revision: int
    changes: ConfigChanges
    base_revision: str
    branch: str
    task_ref: str


class CommandEnvelope(TypedDict):
    schema_version: Literal["1.1"]
    request_id: str
    actor: str
    scope: str
    correlation_id: str
    issued_at: int
    expires_at: int
    action: Action
    category: Literal["spot", "linear", "inverse"] | None
    symbol: str | None
    start_ms: int | None
    end_ms: int | None
    snapshot_id: str | None
    params: Parameters


class Provenance(TypedDict):
    snapshot_id: str | None
    source_ids: list[str]
    coverage: Literal["complete", "partial", "unavailable"]
    code_revision: str | None


class Artifact(TypedDict):
    artifact_id: str
    sha256: str
    media_type: Literal["application/json", "image/png", "text/plain"]
    size_bytes: int


class ResultEnvelope(TypedDict):
    schema_version: Literal["1.0"]
    data: object
    provenance: Provenance
    artifacts: list[Artifact]


class Job(TypedDict):
    job_id: str
    plan_id: str
    state: JobState
    progress: int
    command: CommandEnvelope
    intent_hash: str
    approved_by: str | None
    result: ResultEnvelope | dict | None
