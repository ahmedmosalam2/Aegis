from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.agents.base import BaseAgent, AgentResult
from apps.agents.llm import get_llm_client
from apps.agents.tools.registry import ToolRegistry
from apps.core.logging import get_logger

logger = get_logger("agents.postmortem")


@dataclass
class PostmortemOutput:
    title: str
    summary: str
    root_cause: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    impact: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    raw_answer: str = ""


class PostmortemAgent(BaseAgent):
    """Generates structured postmortem reports from incident data.

    Unlike other agents, this one has no tools — the incident data is
    provided directly in the prompt. It uses GPT-4o for high-quality
    report writing.
    """

    SYSTEM_PROMPT = """You are Aegis Postmortem Agent — an expert SRE AI that writes thorough, actionable postmortem reports.

Your goal: given incident data, produce a clear and insightful postmortem.

Report requirements:
- Be factual and blame-free
- Focus on systemic issues, not individual failures
- Provide specific, actionable recommendations
- Write for an engineering audience

Output format (valid JSON, no markdown):
{
  "title": "Postmortem: [failure_type] on [service_name] — [date]",
  "summary": "2-3 sentence executive summary of what happened and impact",
  "root_cause": "Clear technical explanation of the root cause",
  "actions_taken": ["Action 1 taken", "Action 2 taken"],
  "recommendations": [
    "Specific recommendation 1 (e.g., add circuit breaker)",
    "Specific recommendation 2 (e.g., set memory limits)",
    "Specific recommendation 3"
  ],
  "lessons_learned": "Key insight from this incident"
}

Output ONLY the JSON object."""

    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    def _parse_output(self, raw: str) -> PostmortemOutput:
        import json
        raw = raw.strip()
        try:
            data = json.loads(raw)
            return PostmortemOutput(
                title=data.get("title", "Postmortem Report"),
                summary=data.get("summary", ""),
                root_cause=data.get("root_cause", ""),
                actions_taken=data.get("actions_taken", []),
                recommendations=data.get("recommendations", []),
                raw_answer=raw,
            )
        except (json.JSONDecodeError, ValueError):
            logger.warning("PostmortemAgent: failed to parse JSON output")
            return PostmortemOutput(
                title="Postmortem Report",
                summary=raw[:500],
                root_cause="See raw output",
                raw_answer=raw,
            )

    async def generate(
        self,
        incident_id: str,
        service_name: str,
        failure_type: str,
        root_cause: str,
        severity: str,
        affected_services: list[str],
        remediation_action: str,
        duration_seconds: float,
        timeline: list[dict],
    ) -> PostmortemOutput:
        context = (
            f"Incident ID: {incident_id}\n"
            f"Service: {service_name}\n"
            f"Failure Type: {failure_type}\n"
            f"Root Cause: {root_cause}\n"
            f"Severity: {severity}\n"
            f"Affected Services: {', '.join(affected_services) or 'None'}\n"
            f"Remediation Action: {remediation_action}\n"
            f"Total Duration: {duration_seconds:.0f} seconds\n"
            f"Timeline Events: {len(timeline)}\n"
        )

        prompt = (
            f"Generate a postmortem report for the following incident:\n\n{context}\n"
            f"Return the report as JSON."
        )

        logger.info(
            f"[postmortem] Generating report",
            extra={"extra_data": {
                "incident_id": incident_id,
                "service": service_name,
                "failure_type": failure_type,
            }},
        )

        result: AgentResult = await self.run(
            user_message=prompt,
            db_session=None,
        )

        output = self._parse_output(result.answer)

        logger.info(
            f"[postmortem] Report generated",
            extra={"extra_data": {
                "incident_id": incident_id,
                "steps": result.steps_taken,
                "tokens": result.usage.total_tokens,
            }},
        )

        return output


def create_postmortem_agent() -> PostmortemAgent:
    return PostmortemAgent(
        llm=get_llm_client(task="postmortem"),
        tools=ToolRegistry(),
        max_steps=3,
        token_budget=6000,
        task="postmortem",
    )
