"""
Temporal Worker for the Aegis Incident Resolution Workflow.

This is a standalone process that registers the workflow and all
activities with Temporal, then listens for tasks on the
'aegis-incidents' task queue.

Usage:
    python -m apps.workflows.worker

The worker must be running for workflows to execute. It is separate
from the FastAPI server by design — this follows the standard
Temporal deployment pattern.
"""
import asyncio
import signal
import sys

from temporalio.client import Client
from temporalio.worker import Worker


async def run_worker():
    """Connect to Temporal and start the worker."""
    from apps.api.config import settings
    from apps.workflows.incident_workflow import IncidentResolutionWorkflow
    from apps.workflows.activities import (
        activity_triage_incident,
        activity_diagnose_incident,
        activity_plan_remediation,
        activity_execute_remediation,
        activity_verify_remediation,
        activity_generate_postmortem,
        activity_update_incident_status,
    )

    print(f"[Aegis Worker] Connecting to Temporal at {settings.TEMPORAL_ADDRESS}...")

    client = await Client.connect(settings.TEMPORAL_ADDRESS)

    print("[Aegis Worker] Connected to Temporal")
    print("[Aegis Worker] Task queue: aegis-incidents")
    print("[Aegis Worker] Registered workflow: IncidentResolutionWorkflow")
    print("[Aegis Worker] Registered activities:")
    print("  - activity_triage_incident")
    print("  - activity_diagnose_incident")
    print("  - activity_plan_remediation")
    print("  - activity_execute_remediation")
    print("  - activity_verify_remediation")
    print("  - activity_generate_postmortem")
    print("  - activity_update_incident_status")
    print()
    print("[Aegis Worker] Listening for tasks... (Ctrl+C to stop)")

    # Create and run the worker
    worker = Worker(
        client,
        task_queue="aegis-incidents",
        workflows=[IncidentResolutionWorkflow],
        activities=[
            activity_triage_incident,
            activity_diagnose_incident,
            activity_plan_remediation,
            activity_execute_remediation,
            activity_verify_remediation,
            activity_generate_postmortem,
            activity_update_incident_status,
        ],
    )

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        print("\n[Aegis Worker] Shutting down gracefully...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM
            pass

    # Run worker until shutdown
    async with worker:
        await shutdown_event.wait()

    print("[Aegis Worker] Worker stopped.")


def main():
    """Entry point for `python -m apps.workflows.worker`."""
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        print("\n[Aegis Worker] Interrupted — exiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
