from enum import Enum


class IncidentStatus(str, Enum):


    DETECTED = "detected"
    TRIAGING = "triaging"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    REMEDIATING = "remediating"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentSeverity(str, Enum):


    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


# Valid state transitions — defines which status can move to which
VALID_STATUS_TRANSITIONS: dict[IncidentStatus, list[IncidentStatus]] = {
    IncidentStatus.DETECTED: [
        IncidentStatus.TRIAGING,
        IncidentStatus.CLOSED,          # false alarm
    ],
    IncidentStatus.TRIAGING: [
        IncidentStatus.INVESTIGATING,
        IncidentStatus.CLOSED,          # not actionable
    ],
    IncidentStatus.INVESTIGATING: [
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.REMEDIATING,     # auto-safe action
        IncidentStatus.CLOSED,          # resolved itself
    ],
    IncidentStatus.AWAITING_APPROVAL: [
        IncidentStatus.REMEDIATING,     # approved
        IncidentStatus.INVESTIGATING,   # rejected → re-investigate
    ],
    IncidentStatus.REMEDIATING: [
        IncidentStatus.VERIFYING,
        IncidentStatus.INVESTIGATING,   # remediation failed
    ],
    IncidentStatus.VERIFYING: [
        IncidentStatus.RESOLVED,
        IncidentStatus.INVESTIGATING,   # verification failed
    ],
    IncidentStatus.RESOLVED: [
        IncidentStatus.CLOSED,
        IncidentStatus.INVESTIGATING,   # re-opened
    ],
    IncidentStatus.CLOSED: [],          # terminal state
}


def validate_status_transition(
    current: IncidentStatus,
    target: IncidentStatus,
) -> bool:
    """Check if a status transition is valid."""
    return target in VALID_STATUS_TRANSITIONS.get(current, [])
