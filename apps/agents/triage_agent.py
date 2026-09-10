from __future__ import annotations

import json
from dataclasses import dataclass, field

from apps.agents.base import BaseAgent, AgentResult
from apps.agents.llm import get_llm_client
from apps.agents.tools.definitions import create_triage_tools
from apps.core.logging import get_logger

logger = get_logger("agents.triage")


@dataclass
class TriageOutput:
    severity: str
    priority: int
    should_investigate: bool
    affected_services: list[str] = field(default_factory=list)
    summary: str = ""
    raw_answer: str = ""


class TriageAgent(BaseAgent):
    """Classifies incident severity and decides whether investigation is needed.

    Uses a lightweight model (gpt-4o-mini) — this is a quick assessment,
    not deep diagnosis.
    """

    SYSTEM_PROMPT = """You are Aegis Triage Agent — an SRE AI that quickly assesses production incidents.

Your goal: determine the SEVERITY and whether the incident needs full investigation.

Triage protocol:
1. Call `query_metrics` on the reported service
2. Call `get_service_dependencies` to assess blast radius
3. Call `check_system_health` to understand scope
4. Output your triage assessment as JSON

Severity levels:
- critical: Service is completely down or widespread outage
- high: Service is unhealthy with significant user impact
- medium: Service is degraded but partially functional
- low: Minor issue with minimal user impact

Output format (valid JSON, no markdown):
{
  "severity": "critical|high|medium|low",
  "priority": 1 to 4 (1=critical, 4=low),
  "should_investigate": true or false,
  "affected_services": ["service1", "service2"],
  "summary": "One sentence summary of the situation"
}

Output ONLY the JSON object when done."""

    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    def _parse_output(self, raw: str) -> TriageOutput:
        raw = raw.strip()
        try:
            data = json.loads(raw)
            return TriageOutput(
                severity=data.get("severity", "medium"),
                priority=int(data.get("priority", 3)),
                should_investigate=bool(data.get("should_investigate", True)),
                affected_services=data.get("affected_services", []),
                summary=data.get("summary", ""),
                raw_answer=raw,
            )
        except (json.JSONDecodeError, ValueError):
            logger.warning("TriageAgent: failed to parse JSON output")
            return TriageOutput(
                severity="medium",
                priority=3,
                should_investigate=True,
                raw_answer=raw,
            )

    async def triage(
        self,
        service_name: str,
        incident_id: str,
        db_session=None,
    ) -> TriageOutput:
        prompt = (
            f"Triage incident {incident_id} affecting service '{service_name}'. "
            f"Assess severity, blast radius, and whether full investigation is needed. "
            f"Return your assessment as JSON."
        )

        logger.info(
            f"[triage] Starting assessment",
            extra={"extra_data": {"incident_id": incident_id, "service": service_name}},
        )

        result: AgentResult = await self.run(
            user_message=prompt,
            db_session=db_session,
        )

        output = self._parse_output(result.answer)

        logger.info(
            f"[triage] Completed",
            extra={"extra_data": {
                "incident_id": incident_id,
                "severity": output.severity,
                "should_investigate": output.should_investigate,
                "steps": result.steps_taken,
                "tokens": result.usage.total_tokens,
            }},
        )

        return output


def create_triage_agent() -> TriageAgent:
    return TriageAgent(
        llm=get_llm_client(task="triage"),
        tools=create_triage_tools(),
        max_steps=5,
        token_budget=4000,
        task="triage",
    )
