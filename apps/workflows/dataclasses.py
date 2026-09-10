from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class IncidentWorkflowInput:
    """Input to start an incident resolution workflow."""
    incident_id: str
    service_name: str


@dataclass
class TriageResult:
    """Output of the triage phase."""
    incident_id: str
    severity: str               # critical, high, medium, low, info
    priority: int               # 1 = highest
    should_investigate: bool
    affected_services: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class EvidenceItem:
    """A single piece of diagnostic evidence."""
    source: str         # e.g. "health_engine", "metrics", "service_graph"
    description: str
    data: dict = field(default_factory=dict)


@dataclass
class DiagnosisResult:
    """Output of the diagnosis phase."""
    incident_id: str
    root_cause: str
    failure_type: str
    affected_service: str
    confidence: float           # 0.0 – 1.0
    evidence: list[dict] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)


@dataclass
class RemediationAction:
    """A planned remediation action."""
    action_type: str            # restart_service, clear_connection_pool, scale_service, rollback_deployment
    target_service: str
    risk_level: str             # low, medium, high
    requires_approval: bool
    parameters: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class RemediationResult:
    """Output of executing a remediation."""
    incident_id: str
    action_type: str
    target_service: str
    success: bool
    details: str = ""


@dataclass
class VerificationResult:
    """Output of the verification phase."""
    incident_id: str
    is_healthy: bool
    needs_reinvestigation: bool
    service_status: str = ""
    metrics_snapshot: dict = field(default_factory=dict)
    details: str = ""


@dataclass
class PostmortemReport:
    """Structured postmortem generated after incident resolution."""
    incident_id: str
    title: str
    summary: str
    root_cause: str
    timeline: list[dict] = field(default_factory=list)
    actions_taken: list[dict] = field(default_factory=list)
    impact: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)       # TTD, TTR, etc.
    recommendations: list[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    """Final output of the complete incident resolution workflow."""
    incident_id: str
    status: str                 # resolved, closed, failed
    triage: TriageResult | None = None
    diagnosis: DiagnosisResult | None = None
    remediation: RemediationResult | None = None
    verification: VerificationResult | None = None
    postmortem: PostmortemReport | None = None
    error: str | None = None
