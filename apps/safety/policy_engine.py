from __future__ import annotations
from dataclasses import dataclass, field
from apps.safety.action_registry import get_action, is_allowed, ActionDefinition
from apps.safety.risk_scorer import compute_risk, RiskAssessment, RiskLevel
from apps.safety.rate_limiter import get_rate_limiter
from apps.core.logging import get_logger

logger = get_logger("safety.policy_engine")




@dataclass
class PolicyDecision:

    allowed: bool
    requires_approval: bool
    risk_assessment: RiskAssessment | None
    rejection_reason: str = ""
    audit_log: list[str] = field(default_factory=list)

    @property
    def final_risk(self) -> RiskLevel | None:
        return self.risk_assessment.final_risk if self.risk_assessment else None

    @property
    def risk_score(self) -> float | None:
        return self.risk_assessment.score if self.risk_assessment else None

    def summary(self) -> str:
    
        if not self.allowed:
            return f"BLOCKED — {self.rejection_reason}"
        status = "REQUIRES_APPROVAL" if self.requires_approval else "AUTO_EXECUTE"
        return (
            f"{status} — risk={self.final_risk.value if self.final_risk else '?'}, "
            f"score={self.risk_score}"
        )



def evaluate(
    action_name: str,
    target_service: str,
    llm_risk: str,
    incident_id: str,
) -> PolicyDecision:

    audit: list[str] = []
    audit.append(
        f"[PolicyEngine] Evaluating: action='{action_name}' "
        f"service='{target_service}' llm_risk='{llm_risk}' "
        f"incident='{incident_id}'"
    )

    # ── Check 1: Action Allowlist ────────────────────────────────────
    if not is_allowed(action_name):
        reason = (
            f"Action '{action_name}' is not in the approved action registry. "
            f"Allowed actions: {', '.join(_get_allowed_names())}."
        )
        audit.append(f"FAIL [allowlist]: {reason}")
        logger.warning(
            f"[PolicyEngine] BLOCKED (allowlist): {reason}",
            extra={"extra_data": {"incident_id": incident_id}},
        )
        return PolicyDecision(
            allowed=False,
            requires_approval=False,
            risk_assessment=None,
            rejection_reason=reason,
            audit_log=audit,
        )
    audit.append(f"PASS [allowlist]: '{action_name}' is registered")

    # ── Check 2: Rate Limit ──────────────────────────────────────────
    action_def: ActionDefinition = get_action(action_name)  # type: ignore[assignment]
    limiter = get_rate_limiter()

    rate_allowed, rate_reason = limiter.check(
        action_name=action_name,
        service_name=target_service,
        window_seconds=300,                              # 5-minute window
        max_count=action_def.max_executions_per_window,
    )

    if not rate_allowed:
        audit.append(f"FAIL [rate_limit]: {rate_reason}")
        logger.warning(
            f"[PolicyEngine] BLOCKED (rate_limit)",
            extra={"extra_data": {"incident_id": incident_id, "reason": rate_reason}},
        )
        return PolicyDecision(
            allowed=False,
            requires_approval=False,
            risk_assessment=None,
            rejection_reason=rate_reason,
            audit_log=audit,
        )
    audit.append("PASS [rate_limit]: within allowed execution frequency")

    # ── Check 3: Risk Scoring ────────────────────────────────────────
    assessment: RiskAssessment = compute_risk(
        action=action_def,
        target_service=target_service,
        llm_risk=llm_risk,
    )

    audit.append(f"[risk_scorer] score={assessment.score}, final={assessment.final_risk.value}")
    for reason in assessment.reasons:
        audit.append(f"  • {reason}")
    if assessment.llm_risk_overridden:
        audit.append(f"  ⚠ LLM risk estimate overridden by policy engine")

    # ── Final Decision ───────────────────────────────────────────────
    decision = PolicyDecision(
        allowed=True,
        requires_approval=assessment.requires_approval,
        risk_assessment=assessment,
        audit_log=audit,
    )

    logger.info(
        f"[PolicyEngine] {decision.summary()}",
        extra={"extra_data": {
            "incident_id": incident_id,
            "action": action_name,
            "service": target_service,
            "risk_score": assessment.score,
            "final_risk": assessment.final_risk.value,
            "llm_risk": llm_risk,
            "requires_approval": assessment.requires_approval,
            "llm_overridden": assessment.llm_risk_overridden,
        }},
    )

    return decision




def _get_allowed_names() -> list[str]:
    from apps.safety.action_registry import list_allowed_actions
    return list_allowed_actions()
