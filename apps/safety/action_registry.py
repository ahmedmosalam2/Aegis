from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class RiskLevel(str, Enum):
    LOW    = "low"    
    MEDIUM = "medium" 
    HIGH   = "high"    



@dataclass(frozen=True)
class ActionDefinition:

    name: str
    description: str
    base_risk: RiskLevel
    reversible: bool
    requires_healthy_deps: bool
    blast_radius_multiplier: float = 1.0
    max_executions_per_window: int = 3
    
ACTION_REGISTRY: dict[str, ActionDefinition] = {

    "restart_service": ActionDefinition(
        name="restart_service",
        description=(
            "Restart a single service instance. Causes a brief period of "
            "unavailability but is the safest way to recover a crashed or "
            "memory-leaked service."
        ),
        base_risk=RiskLevel.LOW,
        reversible=True,
        requires_healthy_deps=False,
        blast_radius_multiplier=1.0,
        max_executions_per_window=3,
    ),

    "scale_service": ActionDefinition(
        name="scale_service",
        description=(
            "Increase the number of replicas to handle elevated load. "
            "Non-destructive but increases resource consumption."
        ),
        base_risk=RiskLevel.MEDIUM,
        reversible=True,
        requires_healthy_deps=False,
        blast_radius_multiplier=1.0,
        max_executions_per_window=5,
    ),

    "clear_connection_pool": ActionDefinition(
        name="clear_connection_pool",
        description=(
            "Reset the database connection pool. Causes a brief period "
            "where all in-flight requests will fail. Not reversible mid-execution."
        ),
        base_risk=RiskLevel.HIGH,
        reversible=False,
        requires_healthy_deps=True,
        blast_radius_multiplier=1.5,
        max_executions_per_window=2,
    ),

    "restart_dependency": ActionDefinition(
        name="restart_dependency",
        description=(
            "Restart a shared dependency (e.g., a database, message broker). "
            "This affects all services that depend on it — high blast radius."
        ),
        base_risk=RiskLevel.HIGH,
        reversible=True,
        requires_healthy_deps=False,
        blast_radius_multiplier=2.0,
        max_executions_per_window=2,
    ),

    "rollback_deployment": ActionDefinition(
        name="rollback_deployment",
        description=(
            "Roll back the service to its previous deployment version. "
            "Highly effective for bad deploys but requires verification of "
            "the previous version's compatibility."
        ),
        base_risk=RiskLevel.HIGH,
        reversible=True,
        requires_healthy_deps=False,
        blast_radius_multiplier=1.5,
        max_executions_per_window=2,
    ),
}



def get_action(name: str) -> ActionDefinition | None:
 
    return ACTION_REGISTRY.get(name)


def is_allowed(name: str) -> bool:
    return name in ACTION_REGISTRY


def list_allowed_actions() -> list[str]:

    return list(ACTION_REGISTRY.keys())
