from __future__ import annotations

import json
from dataclasses import dataclass, field

from apps.agents.base import BaseAgent, AgentResult
from apps.agents.llm import get_llm_client
from apps.agents.tools.definitions import create_verification_tools
from apps.core.logging import get_logger

logger = get_logger("agents.verification")


@dataclass
class VerificationOutput:
    is_healthy: bool
    service_status: str
    needs_reinvestigation: bool
    remaining_failures: int = 0
    metrics_snapshot: dict = field(default_factory=dict)
    details: str = ""
    raw_answer: str = ""


class VerificationAgent(BaseAgent):
    """Verifies that remediation was successful by re-checking service health.

    Uses a lightweight model — this is a pass/fail health check,
    not complex reasoning.
    """

    SYSTEM_PROMPT = """You are Aegis Verification Agent — an SRE AI that confirms whether remediation was successful.

Your goal: determine if the service is NOW HEALTHY after remediation.

Verification protocol:
1. Call `query_metrics` on the remediated service
2. Call `get_active_failures` to check for remaining failures
3. Call `check_system_health` to confirm system-wide recovery
4. Output your verification result as JSON

Output format (valid JSON, no markdown):
{
  "is_healthy": true or false,
  "service_status": "healthy|degraded|unhealthy|down",
  "needs_reinvestigation": true or false,
  "remaining_failures": number of active failures still present,
  "metrics_snapshot": {"error_rate": X, "latency_ms": X, "cpu_percent": X},
  "details": "One sentence explanation of the verification result"
}

Output ONLY the JSON object when done."""

    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    def _parse_output(self, raw: str) -> VerificationOutput:
        raw = raw.strip()
        try:
            data = json.loads(raw)
            return VerificationOutput(
                is_healthy=bool(data.get("is_healthy", False)),
                service_status=data.get("service_status", "unknown"),
                needs_reinvestigation=bool(data.get("needs_reinvestigation", True)),
                remaining_failures=int(data.get("remaining_failures", 0)),
                metrics_snapshot=data.get("metrics_snapshot", {}),
                details=data.get("details", ""),
                raw_answer=raw,
            )
        except (json.JSONDecodeError, ValueError):
            logger.warning("VerificationAgent: failed to parse JSON output")
            return VerificationOutput(
                is_healthy=False,
                service_status="unknown",
                needs_reinvestigation=True,
                raw_answer=raw,
            )

    async def verify(
        self,
        service_name: str,
        incident_id: str,
        db_session=None,
    ) -> VerificationOutput:
        prompt = (
            f"Verify that remediation for incident {incident_id} on service '{service_name}' "
            f"was successful. Check current health and confirm the service has recovered. "
            f"Return your verification result as JSON."
        )

        logger.info(
            f"[verification] Starting check",
            extra={"extra_data": {"incident_id": incident_id, "service": service_name}},
        )

        result: AgentResult = await self.run(
            user_message=prompt,
            db_session=db_session,
        )

        output = self._parse_output(result.answer)

        logger.info(
            f"[verification] Completed",
            extra={"extra_data": {
                "incident_id": incident_id,
                "is_healthy": output.is_healthy,
                "service_status": output.service_status,
                "steps": result.steps_taken,
                "tokens": result.usage.total_tokens,
            }},
        )

        return output


def create_verification_agent() -> VerificationAgent:
    return VerificationAgent(
        llm=get_llm_client(task="verification"),
        tools=create_verification_tools(),
        max_steps=5,
        token_budget=4000,
        task="verification",
    )
