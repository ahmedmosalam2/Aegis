from __future__ import annotations

import json
from dataclasses import dataclass, field

from apps.agents.base import BaseAgent, AgentResult
from apps.agents.llm import get_llm_client
from apps.agents.tools.definitions import create_diagnosis_tools
from apps.core.logging import get_logger

logger = get_logger("agents.diagnosis")


# ═══════════════════════════════════════════════════════════════════
# STRUCTURED OUTPUT
# ═══════════════════════════════════════════════════════════════════


@dataclass
class DiagnosisOutput:
    root_cause: str
    failure_type: str
    confidence: float
    affected_services: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    raw_answer: str = ""


# ═══════════════════════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════════════════════


class DiagnosisAgent(BaseAgent):
    """Diagnoses incidents via multi-step tool-assisted investigation.

    ReAct loop:
        1. Query metrics → understand current state
        2. Get active failures → confirm known issues
        3. Search logs → find error evidence
        4. Get dependencies → assess blast radius
        5. Check system health → isolate vs widespread
        6. Conclude with structured JSON output
    """

    SYSTEM_PROMPT = """You are Aegis Diagnosis Agent — an expert SRE AI that investigates production incidents.

Your goal: identify the ROOT CAUSE of an incident with high confidence.

Investigation protocol:
1. Start with `query_metrics` on the reported service
2. Call `get_active_failures` to see known failure injections
3. Use `search_logs` to find error patterns
4. Call `get_service_dependencies` to understand cascade risk
5. Call `check_system_health` if you suspect a widespread outage
6. When confident, output your diagnosis as JSON

Output format (MUST be valid JSON, no markdown fences):
{
  "root_cause": "A clear, concise description of the root cause",
  "failure_type": "one of: service_crash|high_latency|memory_leak|cpu_saturation|dependency_failure|connection_exhaustion|unknown",
  "confidence": 0.0 to 1.0,
  "affected_services": ["list", "of", "affected", "service", "names"],
  "evidence": ["Evidence point 1", "Evidence point 2"],
  "recommended_actions": ["Action 1", "Action 2"]
}

Rules:
- Always use tools before concluding — never guess without evidence
- If you find multiple failures, focus on the most severe one
- Confidence > 0.8 means you are certain; < 0.5 means you need more evidence
- Output ONLY the JSON object when done — no prose before or after"""

    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    def _parse_output(self, raw: str) -> DiagnosisOutput:
        raw = raw.strip()
        try:
            data = json.loads(raw)
            return DiagnosisOutput(
                root_cause=data.get("root_cause", "Unknown"),
                failure_type=data.get("failure_type", "unknown"),
                confidence=float(data.get("confidence", 0.5)),
                affected_services=data.get("affected_services", []),
                evidence=data.get("evidence", []),
                recommended_actions=data.get("recommended_actions", []),
                raw_answer=raw,
            )
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "DiagnosisAgent: failed to parse JSON output — using raw text",
                extra={"extra_data": {"raw": raw[:500]}},
            )
            return DiagnosisOutput(
                root_cause=raw,
                failure_type="unknown",
                confidence=0.3,
                raw_answer=raw,
            )

    async def diagnose(
        self,
        service_name: str,
        incident_id: str,
        db_session=None,
    ) -> DiagnosisOutput:
        prompt = (
            f"Investigate incident {incident_id} affecting service '{service_name}'. "
            f"Use your tools to identify the root cause and return your diagnosis as JSON."
        )

        logger.info(
            f"[diagnosis] Starting investigation",
            extra={"extra_data": {
                "incident_id": incident_id,
                "service": service_name,
            }},
        )

        result: AgentResult = await self.run(
            user_message=prompt,
            db_session=db_session,
        )

        output = self._parse_output(result.answer)

        logger.info(
            f"[diagnosis] Completed",
            extra={"extra_data": {
                "incident_id": incident_id,
                "failure_type": output.failure_type,
                "confidence": output.confidence,
                "steps": result.steps_taken,
                "tokens": result.usage.total_tokens,
            }},
        )

        return output


# ═══════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════


def create_diagnosis_agent() -> DiagnosisAgent:
    return DiagnosisAgent(
        llm=get_llm_client(task="diagnosis"),
        tools=create_diagnosis_tools(),
        max_steps=10,
        token_budget=8000,
        task="diagnosis",
    )
