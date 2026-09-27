
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
    from sqlalchemy import select
    from apps.models import Incident
    result = await session.execute(
        select(Incident).where(Incident.id == UUID(incident_id))
    )
    return result.scalar_one_or_none()


async def _get_service(session, service_name: str):
    from sqlalchemy import select
    from apps.models import Service
    result = await session.execute(
        select(Service).where(Service.name == service_name)
    )
    return result.scalar_one_or_none()


async def _get_active_failures(session, service_id):
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
    old_status = incident.status
    incident.status = new_status
    await session.commit()
    return old_status


# ─── Failure → Remediation mapping (fallback) ─────────────────────

REMEDIATION_MAP = {
    "service_crash":        {"action": "restart_service",       "risk": "low"},
    "high_latency":         {"action": "scale_service",          "risk": "medium"},
    "memory_leak":          {"action": "restart_service",        "risk": "medium"},
    "cpu_saturation":       {"action": "scale_service",          "risk": "medium"},
    "dependency_failure":   {"action": "restart_dependency",     "risk": "high"},
    "connection_exhaustion":{"action": "clear_connection_pool",  "risk": "high"},
}

HIGH_RISK_ACTIONS = {"high"}


# ═══════════════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════════════


@activity.defn
async def activity_triage_incident(input: IncidentWorkflowInput) -> TriageResult:
    """Triage an incident using the AI Triage Agent."""
    activity.logger.info(f"[Triage] Starting for incident {input.incident_id}")

    from apps.core.enums import IncidentStatus
    from apps.agents import create_triage_agent

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        old_status = await _update_status(session, incident, IncidentStatus.TRIAGING.value)

        agent = create_triage_agent()
        output = await agent.triage(
            service_name=input.service_name,
            incident_id=input.incident_id,
            db_session=session,
        )

        incident.severity = output.severity
        await session.commit()

        await _log_event(
            session,
            input.incident_id,
            event_type="triage_completed",
            description=(
                f"AI Triage: severity={output.severity}, "
                f"investigate={output.should_investigate}. {output.summary}"
            ),
            old_status=old_status,
            new_status=IncidentStatus.TRIAGING.value,
            metadata={
                "severity": output.severity,
                "priority": output.priority,
                "affected_services": output.affected_services,
                "agent_steps": "ai_triage",
            },
        )

    activity.logger.info(
        f"[Triage] Done — severity={output.severity}, investigate={output.should_investigate}"
    )

    return TriageResult(
        incident_id=input.incident_id,
        severity=output.severity,
        priority=output.priority,
        should_investigate=output.should_investigate,
        affected_services=output.affected_services,
        summary=output.summary,
    )


@activity.defn
async def activity_diagnose_incident(input: IncidentWorkflowInput) -> DiagnosisResult:
    """Diagnose an incident using the AI Diagnosis Agent (ReAct loop)."""
    activity.logger.info(f"[Diagnosis] Starting for incident {input.incident_id}")

    from apps.core.enums import IncidentStatus
    from apps.agents import create_diagnosis_agent

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        old_status = await _update_status(session, incident, IncidentStatus.INVESTIGATING.value)

        agent = create_diagnosis_agent()
        output = await agent.diagnose(
            service_name=input.service_name,
            incident_id=input.incident_id,
            db_session=session,
        )

        await _log_event(
            session,
            input.incident_id,
            event_type="diagnosis_completed",
            description=(
                f"AI Diagnosis: {output.root_cause}. "
                f"Confidence: {output.confidence:.0%}. "
                f"Failure type: {output.failure_type}."
            ),
            old_status=old_status,
            new_status=IncidentStatus.INVESTIGATING.value,
            metadata={
                "root_cause": output.root_cause,
                "failure_type": output.failure_type,
                "confidence": output.confidence,
                "affected_services": output.affected_services,
            },
        )

    activity.logger.info(
        f"[Diagnosis] Done — failure={output.failure_type}, confidence={output.confidence:.0%}"
    )

    return DiagnosisResult(
        incident_id=input.incident_id,
        root_cause=output.root_cause,
        failure_type=output.failure_type,
        affected_service=input.service_name,
        confidence=output.confidence,
        evidence=[{"source": "ai_agent", "description": e} for e in output.evidence],
        recommended_actions=output.recommended_actions,
    )


@activity.defn
async def activity_plan_remediation(
    input: IncidentWorkflowInput,
    diagnosis: DiagnosisResult,
) -> RemediationAction:
    """Plan remediation using the AI Remediation Agent + Policy Engine.

    Flow:
        1. AI Remediation Agent proposes an action
        2. Policy Engine validates: allowlist → rate limit → risk scoring
        3. Policy Engine's decision overrides the LLM's self-assessment
    """
    activity.logger.info(f"[Remediation Plan] Starting for incident {input.incident_id}")

    from apps.core.enums import IncidentStatus
    from apps.agents import create_remediation_agent
    from apps.safety.policy_engine import evaluate

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        # ── Step 1: AI Agent proposes an action ────────────────────
        agent = create_remediation_agent()
        output = await agent.plan(
            service_name=input.service_name,
            incident_id=input.incident_id,
            failure_type=diagnosis.failure_type,
            root_cause=diagnosis.root_cause,
            db_session=session,
        )

        # ── Step 2: Policy Engine validates the proposed action ────
        # الـ policy engine هو اللي بيقرر — مش الـ LLM
        decision = evaluate(
            action_name=output.action_type,
            target_service=output.target_service or input.service_name,
            llm_risk=output.risk_level,
            incident_id=input.incident_id,
        )

        if not decision.allowed:
            # الـ action ممنوعة — نـ raise exception تتمسك في الـ workflow
            # الـ workflow عنده retry policy — هيحاول تاني
            activity.logger.error(
                f"[Remediation Plan] BLOCKED by policy engine: {decision.rejection_reason}"
            )
            await _log_event(
                session,
                input.incident_id,
                event_type="remediation_blocked",
                description=(
                    f"Policy engine blocked action '{output.action_type}': "
                    f"{decision.rejection_reason}"
                ),
                metadata={
                    "action": output.action_type,
                    "rejection_reason": decision.rejection_reason,
                    "audit_log": decision.audit_log,
                },
            )
            raise RuntimeError(
                f"Policy engine blocked remediation: {decision.rejection_reason}"
            )

        # ── Step 3: Use policy engine's risk decision (not LLM's) ──
        final_risk = decision.final_risk.value  # type: ignore[union-attr]
        final_approval = decision.requires_approval

        if final_approval:
            old_status = await _update_status(
                session, incident, IncidentStatus.AWAITING_APPROVAL.value
            )
            await _log_event(
                session,
                input.incident_id,
                event_type="approval_requested",
                description=(
                    f"Policy engine classified '{output.action_type}' on "
                    f"'{output.target_service}' as HIGH risk (score={decision.risk_score}). "
                    f"Awaiting human approval. {output.reason}"
                ),
                old_status=old_status,
                new_status=IncidentStatus.AWAITING_APPROVAL.value,
                metadata={
                    "action": output.action_type,
                    "risk_level": final_risk,
                    "risk_score": decision.risk_score,
                    "llm_risk": output.risk_level,
                    "llm_overridden": (
                        decision.risk_assessment.llm_risk_overridden
                        if decision.risk_assessment else False
                    ),
                    "audit_log": decision.audit_log,
                },
            )
        else:
            await _log_event(
                session,
                input.incident_id,
                event_type="remediation_planned",
                description=(
                    f"Policy engine approved auto-execution: '{output.action_type}' "
                    f"on '{output.target_service}'. Risk: {final_risk} "
                    f"(score={decision.risk_score})."
                ),
                metadata={
                    "action": output.action_type,
                    "risk_level": final_risk,
                    "risk_score": decision.risk_score,
                    "audit_log": decision.audit_log,
                },
            )

    activity.logger.info(
        f"[Remediation Plan] action={output.action_type}, "
        f"policy_risk={final_risk}, requires_approval={final_approval}"
    )

    return RemediationAction(
        action_type=output.action_type,
        target_service=output.target_service or input.service_name,
        risk_level=final_risk,           # ← من الـ policy engine
        requires_approval=final_approval, # ← من الـ policy engine
        parameters=output.parameters,
        reason=output.reason,
    )


@activity.defn
async def activity_execute_remediation(
    input: IncidentWorkflowInput,
    action: RemediationAction,
) -> RemediationResult:
    """Execute remediation — resolves active failures in the DB.

    بعد التنفيذ الناجح، بنسجّل في الـ rate limiter عشان نمنع
    التنفيذ المتكرر لو الـ verification فشل ورجعنا للـ planning.
    """
    activity.logger.info(
        f"[Remediation Execute] {action.action_type} on {action.target_service}"
    )

    from apps.core.enums import IncidentStatus, FailureStatus
    from apps.safety.rate_limiter import get_rate_limiter

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        old_status = await _update_status(session, incident, IncidentStatus.REMEDIATING.value)

        service = await _get_service(session, action.target_service)
        if not service:
            raise RuntimeError(f"Service {action.target_service} not found")

        failures = await _get_active_failures(session, service.id)
        resolved_count = 0
        for failure in failures:
            failure.status = FailureStatus.RESOLVED.value
            failure.resolved_at = datetime.now(timezone.utc)
            resolved_count += 1

        service.status = "healthy"
        await session.commit()

        # ── Record execution in rate limiter ───────────────────────
        # يتعمل بعد الـ commit عشان لو فشل الـ commit ما نحسبهاش
        get_rate_limiter().record(
            action_name=action.action_type,
            service_name=action.target_service,
            incident_id=input.incident_id,
        )

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
                "resolved_failures": resolved_count,
            },
        )

    activity.logger.info(
        f"[Remediation Execute] Resolved {resolved_count} failure(s)"
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
    """Verify remediation success using the AI Verification Agent."""
    activity.logger.info(f"[Verification] Starting for incident {input.incident_id}")

    from apps.core.enums import IncidentStatus
    from apps.agents import create_verification_agent

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        old_status = await _update_status(session, incident, IncidentStatus.VERIFYING.value)

        agent = create_verification_agent()
        output = await agent.verify(
            service_name=input.service_name,
            incident_id=input.incident_id,
            db_session=session,
        )

        await _log_event(
            session,
            input.incident_id,
            event_type="verification_completed",
            description=(
                f"AI Verification: {'PASSED' if output.is_healthy else 'FAILED'}. "
                f"Status: {output.service_status}. {output.details}"
            ),
            old_status=old_status,
            new_status=IncidentStatus.VERIFYING.value,
            metadata={
                "is_healthy": output.is_healthy,
                "service_status": output.service_status,
                "remaining_failures": output.remaining_failures,
            },
        )

    activity.logger.info(
        f"[Verification] Done — healthy={output.is_healthy}, status={output.service_status}"
    )

    return VerificationResult(
        incident_id=input.incident_id,
        is_healthy=output.is_healthy,
        needs_reinvestigation=output.needs_reinvestigation,
        service_status=output.service_status,
        metrics_snapshot=output.metrics_snapshot,
        details=output.details,
    )


@activity.defn
async def activity_generate_postmortem(
    input: IncidentWorkflowInput,
    triage: TriageResult,
    diagnosis: DiagnosisResult,
    remediation: RemediationResult,
    verification: VerificationResult,
) -> PostmortemReport:
    """Generate postmortem report using the AI Postmortem Agent."""
    activity.logger.info(f"[Postmortem] Generating for incident {input.incident_id}")

    from apps.core.enums import IncidentStatus
    from apps.agents import create_postmortem_agent

    async with await _get_db_session() as session:
        incident = await _get_incident(session, input.incident_id)
        if not incident:
            raise RuntimeError(f"Incident {input.incident_id} not found")

        old_status = await _update_status(session, incident, IncidentStatus.RESOLVED.value)

        from sqlalchemy import select
        from apps.models.incident_event import IncidentEvent

        events_result = await session.execute(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident.id)
            .order_by(IncidentEvent.created_at.asc())
        )
        events = events_result.scalars().all()

        timeline = [
            {
                "timestamp": event.created_at.isoformat() if event.created_at else "",
                "event_type": event.event_type,
                "description": event.description,
            }
            for event in events
        ]

        duration = 0.0
        if len(events) >= 2:
            duration = (events[-1].created_at - events[0].created_at).total_seconds()

        agent = create_postmortem_agent()
        output = await agent.generate(
            incident_id=input.incident_id,
            service_name=input.service_name,
            failure_type=diagnosis.failure_type,
            root_cause=diagnosis.root_cause,
            severity=triage.severity,
            affected_services=triage.affected_services,
            remediation_action=remediation.action_type,
            duration_seconds=duration,
            timeline=timeline,
        )

        await _log_event(
            session,
            input.incident_id,
            event_type="postmortem_generated",
            description=f"Incident resolved. Duration: {duration:.0f}s. {output.summary}",
            old_status=old_status,
            new_status=IncidentStatus.RESOLVED.value,
            metadata={"duration_seconds": duration},
        )

    activity.logger.info(f"[Postmortem] Done — duration={duration:.0f}s")

    return PostmortemReport(
        incident_id=input.incident_id,
        title=output.title,
        summary=output.summary,
        root_cause=output.root_cause,
        timeline=timeline,
        actions_taken=[{"action": a} for a in output.actions_taken],
        impact={"severity": triage.severity, "affected_services": triage.affected_services},
        metrics={"duration_seconds": duration, "diagnosis_confidence": diagnosis.confidence},
        recommendations=output.recommendations,
    )


@activity.defn
async def activity_update_incident_status(
    incident_id: str,
    new_status: str,
    reason: str,
) -> str:
    activity.logger.info(f"[Status Update] Incident {incident_id} → {new_status}")

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
