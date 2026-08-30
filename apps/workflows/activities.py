
import asyncio
from datetime import datetime, timezone
from uuid import UUID

from temporalio import activity

from apps.workflows.dataclasses import (
    IncidentWorkflowInput,
    TriageResult,
    DiagnosisResult,
    RemediationAction,
    RemediationResult,
    VerificationResult,
    PostmortemReport,
)


async def _get_db_session():
    
    from apps.api.database import get_session_factory
    return get_session_factory()()


async def _get_incident(session, incident_id: str):
    """Fetch an incident by ID."""
    from sqlalchemy import select
    from apps.models import Incident
    result = await session.execute(
        select(Incident).where(Incident.id == UUID(incident_id))
    )
    return result.scalar_one_or_none()


async def _get_service(session, service_name: str):
    """Fetch a service by name."""
    from sqlalchemy import select
    from apps.models import Service
    result = await session.execute(
        select(Service).where(Service.name == service_name)
    )
    return result.scalar_one_or_none()


async def _get_active_failures(session, service_id):
    """Fetch active failure injections for a service."""
    from sqlalchemy import select
    from apps.models import FailureInjection
    result = await session.execute(
        select(FailureInjection).where(
            FailureInjection.service_id == service_id,
            FailureInjection.status == "active",
        )
    )
    return result.scalars().all()


async def _log_event(session, incident_id: str, event_type: str,
                     description: str, old_status: str | None = None,
                     new_status: str | None = None, metadata: dict | None = None):
    """Log an IncidentEvent to the audit trail."""
    from apps.models.incident_event import IncidentEvent
    event = IncidentEvent(
        incident_id=UUID(incident_id),
        event_type=event_type,
        description=description,
        old_status=old_status,
        new_status=new_status,
        metadata_=metadata,
        created_by="aegis-workflow",
    )
    session.add(event)
    await session.commit()


async def _update_status(session, incident, new_status: str):
    """Update incident status in the DB."""
    old_status = incident.status
    incident.status = new_status
    await session.commit()
    return old_status


# ─── Failure → Remediation mapping ────────────────────────────────

REMEDIATION_MAP = {
    "service_crash": {
        "action": "restart_service",
        "risk": "low",
        "description": "Restart the crashed service",
    },
    "high_latency": {
        "action": "scale_service",
        "risk": "medium",
        "description": "Scale up the service to handle load",
    },
    "memory_leak": {
        "action": "restart_service",
        "risk": "medium",
        "description": "Restart service to reclaim leaked memory",
    },
    "cpu_saturation": {
        "action": "scale_service",
        "risk": "medium",
        "description": "Scale up service to reduce CPU pressure",
    },
    "dependency_failure": {
        "action": "restart_dependency",
        "risk": "high",
        "description": "Restart the failing dependency",
    },
    "connection_exhaustion": {
        "action": "clear_connection_pool",
        "risk": "high",
        "description": "Clear exhausted connection pool and restart",
    },
}

# Risk levels that require human approval
HIGH_RISK_ACTIONS = {"high"}


# ═══════════════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════════════


@activity.defn
async def activity_triage_incident(input: IncidentWorkflowInput) -> TriageResult:
    """Triage an incident: classify severity and decide if investigation is needed.

    Uses the health engine and service dependency graph to assess impact.
    """
    activity.logger.info(f"[Triage] Starting triage for incident {input.incident_id}")

    from apps.core.enums import IncidentSeverity, IncidentStatus
    from apps.core.service_graph import get_dependency_chain
    from apps.core.health_engine import compute_service_health

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        service = await _get_service(session, input.service_name)
        if not service:
            raise RuntimeError(f"Service {input.service_name} not found")

        # Update status: DETECTED → TRIAGING
        old_status = await _update_status(session, incident, IncidentStatus.TRIAGING.value)

        # Get active failures for this service
        failures = await _get_active_failures(session, service.id)
        failure_dicts = [
            {"failure_type": f.failure_type, "config": f.config, "severity": f.severity}
            for f in failures
        ]

        # Compute health
        health = compute_service_health(input.service_name, failure_dicts)

        # Determine blast radius
        affected = list(get_dependency_chain(input.service_name))

        # Classify severity based on health status and blast radius
        severity_map = {
            "down": (IncidentSeverity.CRITICAL.value, 1),
            "unhealthy": (IncidentSeverity.HIGH.value, 2),
            "degraded": (IncidentSeverity.MEDIUM.value, 3),
            "healthy": (IncidentSeverity.LOW.value, 4),
        }
        severity, priority = severity_map.get(
            health.status.value, (IncidentSeverity.MEDIUM.value, 3)
        )

        # Bump severity if many services affected
        if len(affected) >= 3 and priority > 1:
            severity = IncidentSeverity.CRITICAL.value
            priority = 1

        # Update incident severity in DB
        incident.severity = severity
        await session.commit()

        should_investigate = health.status.value != "healthy"

        # Log triage event
        await _log_event(
            session,
            input.incident_id,
            event_type="triage_completed",
            description=(
                f"Triage completed. Severity: {severity}. "
                f"Service status: {health.status.value}. "
                f"Affected services: {len(affected)}. "
                f"{'Proceeding to investigation.' if should_investigate else 'No investigation needed — service is healthy.'}"
            ),
            old_status=old_status,
            new_status=IncidentStatus.TRIAGING.value,
            metadata={
                "severity": severity,
                "priority": priority,
                "service_status": health.status.value,
                "active_failures": len(failure_dicts),
                "affected_services": affected,
                "metrics": health.metrics,
            },
        )

        activity.logger.info(
            f"[Triage] Completed — severity={severity}, "
            f"investigate={should_investigate}, affected={len(affected)}"
        )

        return TriageResult(
            incident_id=input.incident_id,
            severity=severity,
            priority=priority,
            should_investigate=should_investigate,
            affected_services=affected,
            summary=(
                f"Service '{input.service_name}' is {health.status.value}. "
                f"{len(failure_dicts)} active failure(s). "
                f"{len(affected)} dependent service(s) affected."
            ),
        )


@activity.defn
async def activity_diagnose_incident(input: IncidentWorkflowInput) -> DiagnosisResult:
    """Diagnose an incident: identify root cause and gather evidence.

    Queries the health engine for full system health, identifies the
    root failure, and builds an evidence list.
    """
    activity.logger.info(f"[Diagnosis] Starting diagnosis for incident {input.incident_id}")

    from apps.core.enums import IncidentStatus
    from apps.core.health_engine import compute_system_health
    from apps.core.service_graph import (
        get_dependencies,
        get_dependency_chain,
        SERVICE_DEPENDENCIES,
    )

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        service = await _get_service(session, input.service_name)
        if not service:
            raise RuntimeError(f"Service {input.service_name} not found")

        # Update status: TRIAGING → INVESTIGATING
        old_status = await _update_status(session, incident, IncidentStatus.INVESTIGATING.value)

        # Get active failures for ALL services (for system-wide health)
        from sqlalchemy import select
        from apps.models import FailureInjection, Service as ServiceModel

        all_services_result = await session.execute(select(ServiceModel))
        all_services = all_services_result.scalars().all()

        service_failures: dict[str, list[dict]] = {}
        for svc in all_services:
            failures = await _get_active_failures(session, svc.id)
            if failures:
                service_failures[svc.name] = [
                    {"failure_type": f.failure_type, "config": f.config, "severity": f.severity}
                    for f in failures
                ]

        # Compute full system health
        system_health = compute_system_health(service_failures)

        # Get this service's specific failures
        target_failures = service_failures.get(input.service_name, [])
        target_health = system_health.get(input.service_name)

        # Identify root cause — take the most severe active failure
        root_cause = "unknown"
        failure_type = "unknown"
        confidence = 0.5

        if target_failures:
            # Sort by severity priority
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            sorted_failures = sorted(
                target_failures,
                key=lambda f: severity_order.get(f.get("severity", "medium"), 2),
            )
            primary_failure = sorted_failures[0]
            failure_type = primary_failure["failure_type"]
            root_cause = (
                f"Active {failure_type} failure detected on service '{input.service_name}'"
            )
            confidence = 0.9 if len(target_failures) == 1 else 0.75

        # Build evidence
        evidence = []
        if target_health:
            evidence.append({
                "source": "health_engine",
                "description": f"Service health status: {target_health.status.value}",
                "data": target_health.metrics,
            })

        # Check dependencies
        deps = get_dependencies(input.service_name)
        degraded_deps = []
        for dep in deps:
            dep_health = system_health.get(dep)
            if dep_health and dep_health.status.value != "healthy":
                degraded_deps.append(dep)
                evidence.append({
                    "source": "service_graph",
                    "description": f"Dependency '{dep}' is {dep_health.status.value}",
                    "data": dep_health.metrics,
                })

        # Cascading impact
        affected = list(get_dependency_chain(input.service_name))
        if affected:
            evidence.append({
                "source": "service_graph",
                "description": f"Cascading impact to {len(affected)} service(s): {', '.join(affected)}",
                "data": {"affected_services": affected},
            })

        # Recommend remediation actions
        remediation_info = REMEDIATION_MAP.get(failure_type, {})
        recommended_actions = []
        if remediation_info:
            recommended_actions.append(remediation_info.get("description", ""))
        if degraded_deps:
            recommended_actions.append(f"Investigate degraded dependencies: {', '.join(degraded_deps)}")

        # Log diagnosis event
        await _log_event(
            session,
            input.incident_id,
            event_type="diagnosis_completed",
            description=(
                f"Root cause identified: {root_cause}. "
                f"Confidence: {confidence:.0%}. "
                f"Evidence items: {len(evidence)}."
            ),
            old_status=old_status,
            new_status=IncidentStatus.INVESTIGATING.value,
            metadata={
                "root_cause": root_cause,
                "failure_type": failure_type,
                "confidence": confidence,
                "evidence_count": len(evidence),
                "degraded_dependencies": degraded_deps,
            },
        )

        activity.logger.info(
            f"[Diagnosis] Completed — root_cause='{failure_type}', "
            f"confidence={confidence:.0%}, evidence={len(evidence)}"
        )

        return DiagnosisResult(
            incident_id=input.incident_id,
            root_cause=root_cause,
            failure_type=failure_type,
            affected_service=input.service_name,
            confidence=confidence,
            evidence=evidence,
            recommended_actions=recommended_actions,
        )


@activity.defn
async def activity_plan_remediation(
    input: IncidentWorkflowInput,
    diagnosis: DiagnosisResult,
) -> RemediationAction:
    """Plan a remediation action based on diagnosis results.

    Maps failure types to concrete actions and classifies risk level.
    High-risk actions will require human approval.
    """
    activity.logger.info(
        f"[Remediation Plan] Planning for incident {input.incident_id}, "
        f"failure_type={diagnosis.failure_type}"
    )

    from apps.core.enums import IncidentStatus

    remediation_info = REMEDIATION_MAP.get(diagnosis.failure_type, {
        "action": "restart_service",
        "risk": "medium",
        "description": f"Generic restart for unknown failure type: {diagnosis.failure_type}",
    })

    risk_level = remediation_info["risk"]
    requires_approval = risk_level in HIGH_RISK_ACTIONS

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        if requires_approval:
            # Move to AWAITING_APPROVAL
            old_status = await _update_status(
                session, incident, IncidentStatus.AWAITING_APPROVAL.value
            )
            await _log_event(
                session,
                input.incident_id,
                event_type="approval_requested",
                description=(
                    f"High-risk action requires human approval: "
                    f"{remediation_info['action']} on {input.service_name}. "
                    f"Risk level: {risk_level}."
                ),
                old_status=old_status,
                new_status=IncidentStatus.AWAITING_APPROVAL.value,
                metadata={
                    "action": remediation_info["action"],
                    "risk_level": risk_level,
                    "target_service": input.service_name,
                },
            )
        else:
            await _log_event(
                session,
                input.incident_id,
                event_type="remediation_planned",
                description=(
                    f"Auto-approved action: {remediation_info['action']} "
                    f"on {input.service_name}. Risk level: {risk_level}."
                ),
                metadata={
                    "action": remediation_info["action"],
                    "risk_level": risk_level,
                    "auto_approved": True,
                },
            )

    activity.logger.info(
        f"[Remediation Plan] Action: {remediation_info['action']}, "
        f"risk={risk_level}, approval_needed={requires_approval}"
    )

    return RemediationAction(
        action_type=remediation_info["action"],
        target_service=input.service_name,
        risk_level=risk_level,
        requires_approval=requires_approval,
        parameters={"failure_type": diagnosis.failure_type},
        reason=remediation_info["description"],
    )


@activity.defn
async def activity_execute_remediation(
    input: IncidentWorkflowInput,
    action: RemediationAction,
) -> RemediationResult:
    """Execute a remediation action.

    In this phase, we simulate remediation by resolving the active
    FailureInjection records in the database.
    """
    activity.logger.info(
        f"[Remediation Execute] Executing {action.action_type} "
        f"on {action.target_service} for incident {input.incident_id}"
    )

    from apps.core.enums import IncidentStatus, FailureStatus
    from datetime import datetime, timezone

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        # Update status → REMEDIATING
        old_status = await _update_status(
            session, incident, IncidentStatus.REMEDIATING.value
        )

        service = await _get_service(session, action.target_service)
        if not service:
            raise RuntimeError(f"Service {action.target_service} not found")

        # Resolve active failures (simulate remediation)
        failures = await _get_active_failures(session, service.id)
        resolved_count = 0
        for failure in failures:
            failure.status = FailureStatus.RESOLVED.value
            failure.resolved_at = datetime.now(timezone.utc)
            resolved_count += 1

        # Update service status back to healthy
        service.status = "healthy"
        await session.commit()

        # Log remediation event
        await _log_event(
            session,
            input.incident_id,
            event_type="remediation_executed",
            description=(
                f"Executed {action.action_type} on '{action.target_service}'. "
                f"Resolved {resolved_count} active failure(s)."
            ),
            old_status=old_status,
            new_status=IncidentStatus.REMEDIATING.value,
            metadata={
                "action_type": action.action_type,
                "target_service": action.target_service,
                "resolved_failures": resolved_count,
            },
        )

    activity.logger.info(
        f"[Remediation Execute] Completed — resolved {resolved_count} failure(s)"
    )

    return RemediationResult(
        incident_id=input.incident_id,
        action_type=action.action_type,
        target_service=action.target_service,
        success=True,
        details=f"Resolved {resolved_count} active failure(s) via {action.action_type}",
    )


@activity.defn
async def activity_verify_remediation(input: IncidentWorkflowInput) -> VerificationResult:
    """Verify that remediation was successful.

    Re-runs the health engine and checks if the service returned
    to healthy status.
    """
    activity.logger.info(f"[Verification] Verifying incident {input.incident_id}")

    from apps.core.enums import IncidentStatus
    from apps.core.health_engine import compute_service_health

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        service = await _get_service(session, input.service_name)
        if not service:
            raise RuntimeError(f"Service {input.service_name} not found")

        # Update status → VERIFYING
        old_status = await _update_status(
            session, incident, IncidentStatus.VERIFYING.value
        )

        # Check for any remaining active failures
        failures = await _get_active_failures(session, service.id)
        failure_dicts = [
            {"failure_type": f.failure_type, "config": f.config, "severity": f.severity}
            for f in failures
        ]

        # Compute health
        health = compute_service_health(input.service_name, failure_dicts)

        is_healthy = health.status.value == "healthy"
        needs_reinvestigation = not is_healthy

        # Log verification event
        await _log_event(
            session,
            input.incident_id,
            event_type="verification_completed",
            description=(
                f"Verification {'passed' if is_healthy else 'failed'}. "
                f"Service status: {health.status.value}. "
                f"{'System is healthy.' if is_healthy else 'Reinvestigation needed.'}"
            ),
            old_status=old_status,
            new_status=IncidentStatus.VERIFYING.value,
            metadata={
                "is_healthy": is_healthy,
                "service_status": health.status.value,
                "remaining_failures": len(failure_dicts),
                "metrics": health.metrics,
            },
        )

    activity.logger.info(
        f"[Verification] Completed — healthy={is_healthy}, "
        f"remaining_failures={len(failure_dicts)}"
    )

    return VerificationResult(
        incident_id=input.incident_id,
        is_healthy=is_healthy,
        needs_reinvestigation=needs_reinvestigation,
        service_status=health.status.value,
        metrics_snapshot=health.metrics,
        details=(
            "Service returned to healthy state."
            if is_healthy
            else f"Service still {health.status.value} with {len(failure_dicts)} active failure(s)."
        ),
    )


@activity.defn
async def activity_generate_postmortem(
    input: IncidentWorkflowInput,
    triage: TriageResult,
    diagnosis: DiagnosisResult,
    remediation: RemediationResult,
    verification: VerificationResult,
) -> PostmortemReport:
    """Generate a structured postmortem report.

    Compiles all phase results into a comprehensive incident report
    with timeline, metrics, and recommendations.
    """
    activity.logger.info(f"[Postmortem] Generating report for incident {input.incident_id}")

    from apps.core.enums import IncidentStatus

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        # Update status → RESOLVED
        old_status = await _update_status(
            session, incident, IncidentStatus.RESOLVED.value
        )

        # Fetch full timeline from DB
        from sqlalchemy import select
        from apps.models.incident_event import IncidentEvent

        events_result = await session.execute(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident.id)
            .order_by(IncidentEvent.created_at.asc())
        )
        events = events_result.scalars().all()

        # Build timeline
        timeline = [
            {
                "timestamp": event.created_at.isoformat() if event.created_at else "",
                "event_type": event.event_type,
                "description": event.description,
            }
            for event in events
        ]

        # Calculate metrics
        if events and len(events) >= 2:
            first_event_time = events[0].created_at
            last_event_time = events[-1].created_at
            total_duration = (last_event_time - first_event_time).total_seconds()
        else:
            total_duration = 0.0

        metrics = {
            "total_duration_seconds": total_duration,
            "phases_completed": 5,
            "triage_severity": triage.severity,
            "diagnosis_confidence": diagnosis.confidence,
            "remediation_success": remediation.success,
            "verification_healthy": verification.is_healthy,
        }

        # Build recommendations
        recommendations = [
            f"Monitor '{input.service_name}' closely for recurrence of {diagnosis.failure_type}",
        ]
        if triage.affected_services:
            recommendations.append(
                f"Review cascading impact on: {', '.join(triage.affected_services)}"
            )
        if diagnosis.confidence < 0.8:
            recommendations.append(
                "Root cause confidence was below 80% — consider manual review"
            )
        recommendations.append(
            "Update runbook with remediation steps for this failure type"
        )

        # Log postmortem event
        await _log_event(
            session,
            input.incident_id,
            event_type="postmortem_generated",
            description=(
                f"Incident resolved. Total duration: {total_duration:.0f}s. "
                f"Root cause: {diagnosis.root_cause}."
            ),
            old_status=old_status,
            new_status=IncidentStatus.RESOLVED.value,
            metadata=metrics,
        )

    activity.logger.info(f"[Postmortem] Report generated — duration={total_duration:.0f}s")

    return PostmortemReport(
        incident_id=input.incident_id,
        title=f"Postmortem: {diagnosis.failure_type} on {input.service_name}",
        summary=(
            f"Incident affecting '{input.service_name}' was detected, "
            f"diagnosed as {diagnosis.failure_type}, remediated via "
            f"{remediation.action_type}, and verified "
            f"{'successfully' if verification.is_healthy else 'with issues'}."
        ),
        root_cause=diagnosis.root_cause,
        timeline=timeline,
        actions_taken=[{
            "action": remediation.action_type,
            "target": remediation.target_service,
            "success": remediation.success,
            "details": remediation.details,
        }],
        impact={
            "severity": triage.severity,
            "affected_services": triage.affected_services,
        },
        metrics=metrics,
        recommendations=recommendations,
    )


@activity.defn
async def activity_update_incident_status(
    incident_id: str,
    new_status: str,
    reason: str,
) -> str:
    """Generic helper activity to update incident status and log an event.

    Used for intermediate transitions (e.g., closing a non-actionable incident).
    Returns the new status value.
    """
    activity.logger.info(
        f"[Status Update] Incident {incident_id} → {new_status}: {reason}"
    )

    async with await _get_db_session() as session:
        incident = await _get_incident(session, incident_id)
        if not incident:
            raise RuntimeError(f"Incident {incident_id} not found")

        old_status = await _update_status(session, incident, new_status)

        await _log_event(
            session,
            incident_id,
            event_type="status_changed",
            description=reason,
            old_status=old_status,
            new_status=new_status,
        )

    return new_status
