from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from apps.agents.base import BaseAgent, AgentResult
from apps.agents.llm import get_llm_client
from apps.agents.tools.registry import ToolRegistry
from apps.agents.tools.definitions import (
    create_diagnosis_tools,
    QUERY_METRICS_SCHEMA,
    CHECK_SYSTEM_HEALTH_SCHEMA,
    query_metrics,
    check_system_health,
)
from apps.agents.tools.registry import ToolDefinition
from apps.core.logging import get_logger

logger = get_logger("agents.remediation")


@dataclass
class RemediationPlanOutput:
    action_type: str
    target_service: str
    risk_level: str
    requires_approval: bool
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    raw_answer: str = ""


class RemediationAgent(BaseAgent):
    """Plans the optimal remediation action for a diagnosed incident.

    Given a diagnosis, the agent selects the safest and most effective
    remediation strategy, classifies its risk, and decides if human
    approval is required.
    """

    SYSTEM_PROMPT = """You are Aegis Remediation Agent — an SRE AI that plans safe remediation actions for production incidents.

Your goal: given a diagnosis, determine the BEST remediation action and assess its risk.

Available remediation actions:
- restart_service: Restart a crashed or memory-leaked service (low-medium risk)
- scale_service: Scale up replicas to handle load (medium risk)
- restart_dependency: Restart a failing dependency (high risk — affects downstream)
- clear_connection_pool: Reset exhausted connection pools (high risk — brief downtime)
- rollback_deployment: Roll back to previous version (high risk — requires verification)

Risk classification:
- low: Automated, reversible, minimal blast radius
- medium: Automated but may cause brief disruption
- high: Requires human approval before execution

Output format (valid JSON, no markdown):
{
  "action_type": "restart_service|scale_service|restart_dependency|clear_connection_pool|rollback_deployment",
  "target_service": "name of the service to act on",
  "risk_level": "low|medium|high",
  "requires_approval": true or false (true if risk is high),
  "parameters": {"key": "value"},
  "reason": "Why this action was chosen and why this risk level"
}

Output ONLY the JSON object when done."""

    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    def _parse_output(self, raw: str) -> RemediationPlanOutput:
        raw = raw.strip()
        try:
            data = json.loads(raw)
            risk = data.get("risk_level", "medium")
            return RemediationPlanOutput(
                action_type=data.get("action_type", "restart_service"),
                target_service=data.get("target_service", ""),
                risk_level=risk,
                requires_approval=bool(data.get("requires_approval", risk == "high")),
                parameters=data.get("parameters", {}),
                reason=data.get("reason", ""),
                raw_answer=raw,
            )
        except (json.JSONDecodeError, ValueError):
            logger.warning("RemediationAgent: failed to parse JSON output")
            return RemediationPlanOutput(
                action_type="restart_service",
                target_service="",
                risk_level="medium",
                requires_approval=False,
                raw_answer=raw,
            )

    async def plan(
        self,
        service_name: str,
        incident_id: str,
        failure_type: str,
        root_cause: str,
        db_session=None,
    ) -> RemediationPlanOutput:
        prompt = (
            f"Plan remediation for incident {incident_id} on service '{service_name}'. "
            f"Diagnosis: failure_type='{failure_type}', root_cause='{root_cause}'. "
            f"Use query_metrics to verify current state, then select the best remediation action. "
            f"Return your plan as JSON."
        )

        logger.info(
            f"[remediation] Planning action",
            extra={"extra_data": {
                "incident_id": incident_id,
                "service": service_name,
                "failure_type": failure_type,
            }},
        )

        result: AgentResult = await self.run(
            user_message=prompt,
            db_session=db_session,
        )

        output = self._parse_output(result.answer)

        logger.info(
            f"[remediation] Plan ready",
            extra={"extra_data": {
                "incident_id": incident_id,
                "action": output.action_type,
                "risk": output.risk_level,
                "requires_approval": output.requires_approval,
                "steps": result.steps_taken,
                "tokens": result.usage.total_tokens,
            }},
        )

        return output


def _create_remediation_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="query_metrics",
        description="Query current health metrics for a specific service.",
        parameters=QUERY_METRICS_SCHEMA,
        handler=query_metrics,
    ))
    registry.register(ToolDefinition(
        name="check_system_health",
        description="Get overall system health across all services.",
        parameters=CHECK_SYSTEM_HEALTH_SCHEMA,
        handler=check_system_health,
    ))
    return registry


def create_remediation_agent() -> RemediationAgent:
    return RemediationAgent(
        llm=get_llm_client(task="remediation"),
        tools=_create_remediation_tools(),
        max_steps=5,
        token_budget=4000,
        task="remediation",
    )
