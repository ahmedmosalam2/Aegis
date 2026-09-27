from __future__ import annotations
from dataclasses import dataclass, field
from apps.safety.action_registry import ActionDefinition, RiskLevel
from apps.core.service_graph import would_cascade, get_dependents
from apps.core.logging import get_logger

logger = get_logger("safety.risk_scorer")

@dataclass
class RiskAssessment:

    final_risk: RiskLevel
    requires_approval: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    llm_risk_overridden: bool = False



_BASE_SCORES: dict[RiskLevel, float] = {
    RiskLevel.LOW:    0.2,
    RiskLevel.MEDIUM: 0.5,
    RiskLevel.HIGH:   0.8,
}

_SCORE_THRESHOLDS = {
    "high":   0.65,  
    "medium": 0.40,  
   
}



def compute_risk(
    action: ActionDefinition,
    target_service: str,
    llm_risk: str,
) -> RiskAssessment:

    reasons: list[str] = []
    score: float = 0.0
    llm_risk_overridden = False

   
    base_score = _BASE_SCORES[action.base_risk]
    score += base_score
    reasons.append(
        f"Base action risk ({action.name}): {action.base_risk.value} "
        f"→ +{base_score:.2f}"
    )

  
    try:
        dependents = get_dependents(target_service)
    except Exception:
        dependents = []

    if dependents:
   
        blast_bonus = min(len(dependents) * 0.05, 0.30)
        score += blast_bonus
        reasons.append(
            f"Service '{target_service}' has {len(dependents)} downstream "
            f"dependent(s) {dependents} → +{blast_bonus:.2f}"
        )
    else:
        reasons.append(f"Service '{target_service}' has no downstream dependents → +0.00")

    in_cascade_zone = would_cascade(target_service)

    if in_cascade_zone:
        cascade_bonus = 0.15 * action.blast_radius_multiplier
        score += cascade_bonus
        reasons.append(
            f"Service is in cascade risk zone "
            f"(multiplier={action.blast_radius_multiplier}x) → +{cascade_bonus:.2f}"
        )

   
    if not action.reversible:
        score += 0.10
        reasons.append("Action is NOT reversible → +0.10")
    else:
        reasons.append("Action is reversible → +0.00")

    
    parsed_llm_risk = RiskLevel(llm_risk)

    if action.base_risk == RiskLevel.HIGH and parsed_llm_risk == RiskLevel.LOW:
        score += 0.10
        llm_risk_overridden = True
        reasons.append(
            f"LLM underestimated risk (LLM said '{llm_risk}', "
            f"registry says '{action.base_risk.value}') → +0.10 (override)"
        )

   
    score = min(round(score, 3), 1.0)

    if score >= _SCORE_THRESHOLDS["high"]:
        final_risk = RiskLevel.HIGH
    elif score >= _SCORE_THRESHOLDS["medium"]:
        final_risk = RiskLevel.MEDIUM
    else:
        final_risk = RiskLevel.LOW

    requires_approval = (final_risk == RiskLevel.HIGH)

    logger.debug(
        f"[RiskScorer] {action.name} on '{target_service}': "
        f"score={score}, final={final_risk.value}",
        extra={"extra_data": {"reasons": reasons}},
    )

    return RiskAssessment(
        final_risk=final_risk,
        requires_approval=requires_approval,
        score=score,
        reasons=reasons,
        llm_risk_overridden=llm_risk_overridden,
    )
