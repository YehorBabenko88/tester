"""Typed execution calls on the pinned G HTTP transport; no new network authority."""

from .research_control import Command
from .research_execution import ExecutionRequest
from .research_g_client import ResearchGatewayClient


class ExecutionGatewayClient(ResearchGatewayClient):
    def execution_submit(self, request: ExecutionRequest):
        request = ExecutionRequest.model_validate(request.model_dump())
        result = self._call("POST", "research-executions", request.model_dump(mode="json"))
        self._validate_result(result)
        return result

    def execution_result(self, job_id):
        result = self._call("GET", "research-executions/" + self._id(job_id))
        self._validate_result(result)
        if result["job_id"] != job_id:
            raise ValueError("execution job mismatch")
        return result

    @classmethod
    def _validate_result(cls, result):
        cls._id(result.get("job_id"))
        state = result.get("state")
        if state in {"pending", "running"} and set(result) == {"job_id", "state"}:
            return
        if state == "complete" and set(result) == {
            "job_id",
            "state",
            "chart_command",
            "certification",
        }:
            if result["certification"] != {
                "scope": "SYNTHETIC_ONLY",
                "real_market_coverage": "NOT_PROVEN",
            }:
                raise ValueError("uncertified execution result")
            Command.model_validate(result["chart_command"])
            return
        if (
            state == "failed"
            and set(result) == {"job_id", "state", "reason"}
            and result["reason"] == "ATTEMPT_LIMIT"
        ):
            return
        raise ValueError("invalid execution result")
