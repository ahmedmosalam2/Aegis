"""
Incident Resolution Workflow — Temporal Durable Workflow.

This is the main orchestrator that coordinates the full incident
lifecycle through activities. It uses:
  - Signals for human-in-the-loop approval
  - Queries to inspect workflow state
  - Retry policies on all activities
  - Loop-back on verification failure (max 3 attempts)

┌─────────┐    ┌───────────┐    ┌──────┐    ┌─────────┐    ┌────────┐    ┌──────────┐
│ Triage  │───▸│ Diagnosis │───▸│ Plan │───▸│ Execute │───▸│ Verify │───▸│Postmortem│
└─────────┘    └───────────┘    └──────┘    └─────────┘    └────────┘    └──────────┘
                    ▲               │              ▲            │
                    │         requires_approval?   │       failed?
                    │               │              │            │
                    │          ┌────▼────┐         │       ┌────▼────┐
                    │          │  Wait   │         │       │ Retry   │
                    │          │ Signal  │─rejected─┘       │ (max 3) │
                    │          └─────────┘                  └─────────┘
                    └──────────────────────────────────────────┘
"""
from asyncio import TimeoutError as AsyncTimeoutError
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from apps.workflows.dataclasses import (
        IncidentWorkflowInput,
        TriageResult,
        DiagnosisResult,
        RemediationAction,
        RemediationResult,
        VerificationResult,
        PostmortemReport,
        WorkflowResult,
    )
    from apps.workflows.activities import (
        activity_triage_incident,
        activity_diagnose_incident,
        activity_plan_remediation,
        activity_execute_remediation,
        activity_verify_remediation,
        activity_generate_postmortem,
        activity_update_incident_status,
    )


# ─── Retry / Timeout Policies ─────────────────────────────────────

ACTIVITY_RETRY = workflow.RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

ACTIVITY_TIMEOUT = timedelta(seconds=60)

# Maximum times we re-diagnose after verification failure
MAX_REINVESTIGATION_ATTEMPTS = 3

# How long to wait for human approval before timing out
APPROVAL_TIMEOUT = timedelta(minutes=30)


@workflow.defn
class IncidentResolutionWorkflow:
    """Durable workflow that orchestrates the full incident resolution lifecycle.

    Phases:
        1. Triage — classify severity and decide if investigation is needed
        2. Diagnosis — identify root cause and gather evidence
        3. Plan — select remediation action and assess risk
        4. (Optional) Await human approval for high-risk actions
        5. Execute — run the remediation
        6. Verify — confirm the system is healthy
        7. Postmortem — generate a structured incident report

    The workflow is durable — it survives process crashes, restarts,
    and infrastructure failures without losing state.
    """

    def __init__(self):
        self._current_phase: str = "initialized"
        self._approval_status: str | None = None   # "approved" | "rejected" | None
        self._approval_reason: str = ""

    # ── Signals ─────────────────────────────────────────────────────

    @workflow.signal
    async def approve_remediation(self, reason: str = "Approved by operator"):
        """Signal sent when a human approves a high-risk action."""
        workflow.logger.info(f"[Signal] Remediation APPROVED: {reason}")
        self._approval_status = "approved"
        self._approval_reason = reason

    @workflow.signal
    async def reject_remediation(self, reason: str = "Rejected by operator"):
        """Signal sent when a human rejects a high-risk action."""
        workflow.logger.info(f"[Signal] Remediation REJECTED: {reason}")
        self._approval_status = "rejected"
        self._approval_reason = reason

    # ── Queries ─────────────────────────────────────────────────────

    @workflow.query
    def get_current_phase(self) -> str:
        """Query the current phase of the workflow."""
        return self._current_phase

    @workflow.query
    def get_approval_status(self) -> str | None:
        """Query the approval status (None = pending)."""
        return self._approval_status

    # ── Main Workflow ───────────────────────────────────────────────

    @workflow.run
    async def run(self, input: IncidentWorkflowInput) -> WorkflowResult:
        """Execute the full incident resolution lifecycle."""
        workflow.logger.info(
            f"[Workflow] Starting incident resolution: "
            f"incident={input.incident_id}, service={input.service_name}"
        )

        result = WorkflowResult(
            incident_id=input.incident_id,
            status="in_progress",
        )

        try:
            # ── Phase 1: Triage ────────────────────────────────────
            self._current_phase = "triage"
            triage: TriageResult = await workflow.execute_activity(
                activity_triage_incident,
                input,
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY,
            )
            result.triage = triage

            if not triage.should_investigate:
                # No investigation needed — close the incident
                workflow.logger.info("[Workflow] No investigation needed — closing incident")
                await workflow.execute_activity(
                    activity_update_incident_status,
                    args=[input.incident_id, "closed", "Triage determined no investigation needed"],
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY,
                )
                result.status = "closed"
                return result

            # Investigation loop — allows re-diagnosis after failed verification
            for attempt in range(1, MAX_REINVESTIGATION_ATTEMPTS + 1):
                workflow.logger.info(
                    f"[Workflow] Investigation attempt {attempt}/{MAX_REINVESTIGATION_ATTEMPTS}"
                )

                # ── Phase 2: Diagnosis ─────────────────────────────
                self._current_phase = "diagnosis"
                diagnosis: DiagnosisResult = await workflow.execute_activity(
                    activity_diagnose_incident,
                    input,
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY,
                )
                result.diagnosis = diagnosis

                # ── Phase 3: Plan Remediation ──────────────────────
                self._current_phase = "planning"
                action: RemediationAction = await workflow.execute_activity(
                    activity_plan_remediation,
                    args=[input, diagnosis],
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY,
                )

                # ── Phase 3b: Human Approval (if required) ────────
                if action.requires_approval:
                    self._current_phase = "awaiting_approval"
                    workflow.logger.info(
                        f"[Workflow] Awaiting human approval for: "
                        f"{action.action_type} on {action.target_service} "
                        f"(risk: {action.risk_level})"
                    )

                    # Reset approval state
                    self._approval_status = None

                    # Wait for signal or timeout
                    try:
                        await workflow.wait_condition(
                            lambda: self._approval_status is not None,
                            timeout=APPROVAL_TIMEOUT,
                        )
                    except AsyncTimeoutError:
                        # Approval timed out — treat as rejection
                        workflow.logger.warning(
                            "[Workflow] Approval timed out — treating as rejection"
                        )
                        self._approval_status = "rejected"
                        self._approval_reason = "Approval timed out"

                    if self._approval_status == "rejected":
                        workflow.logger.info(
                            f"[Workflow] Action rejected: {self._approval_reason}. "
                            f"Re-investigating..."
                        )
                        # Log rejection and loop back to diagnosis
                        await workflow.execute_activity(
                            activity_update_incident_status,
                            args=[
                                input.incident_id,
                                "investigating",
                                f"Remediation rejected: {self._approval_reason}. Re-investigating.",
                            ],
                            start_to_close_timeout=ACTIVITY_TIMEOUT,
                            retry_policy=ACTIVITY_RETRY,
                        )
                        continue  # Back to diagnosis

                    workflow.logger.info(
                        f"[Workflow] Action approved: {self._approval_reason}"
                    )

                # ── Phase 4: Execute Remediation ───────────────────
                self._current_phase = "remediating"
                remediation: RemediationResult = await workflow.execute_activity(
                    activity_execute_remediation,
                    args=[input, action],
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY,
                )
                result.remediation = remediation

                # ── Phase 5: Verify ────────────────────────────────
                self._current_phase = "verifying"
                verification: VerificationResult = await workflow.execute_activity(
                    activity_verify_remediation,
                    input,
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY,
                )
                result.verification = verification

                if verification.is_healthy:
                    break  # Success — proceed to postmortem
                else:
                    workflow.logger.warning(
                        f"[Workflow] Verification failed (attempt {attempt}). "
                        f"{'Re-investigating...' if attempt < MAX_REINVESTIGATION_ATTEMPTS else 'Max attempts reached.'}"
                    )
                    if attempt < MAX_REINVESTIGATION_ATTEMPTS:
                        await workflow.execute_activity(
                            activity_update_incident_status,
                            args=[
                                input.incident_id,
                                "investigating",
                                f"Verification failed (attempt {attempt}). Re-investigating.",
                            ],
                            start_to_close_timeout=ACTIVITY_TIMEOUT,
                            retry_policy=ACTIVITY_RETRY,
                        )

            # ── Phase 6: Postmortem ────────────────────────────────
            self._current_phase = "postmortem"
            postmortem: PostmortemReport = await workflow.execute_activity(
                activity_generate_postmortem,
                args=[input, triage, diagnosis, remediation, verification],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY,
            )
            result.postmortem = postmortem
            result.status = "resolved"

            self._current_phase = "completed"
            workflow.logger.info(
                f"[Workflow] Incident {input.incident_id} resolved successfully"
            )

        except Exception as e:
            self._current_phase = "failed"
            result.status = "failed"
            result.error = str(e)
            workflow.logger.error(f"[Workflow] Failed: {e}")
            raise

        return result
