"""
hello_workflow.py - Temporal hello world workflow

ده مثال تعليمي عشان تفهم:
1. الـ Activity: خطوة صغيرة بتعمل شغل حقيقي
2. الـ Workflow: مدير بيرتب الـ activities
3. الـ Worker: اللي بينفذ الـ workflows والـ activities
"""
import asyncio
from datetime import timedelta
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker


# 1. ACTIVITIES - الخطوات الصغيرة

@activity.defn
async def triage_incident(incident_id: str) -> str:
    """بيعمل triage على الـ incident ويرجع severity."""
    print(f"[Activity] Triaging incident: {incident_id}")
    await asyncio.sleep(1)
    return "CRITICAL"


@activity.defn
async def notify_on_call(incident_id: str, severity: str) -> str:
    """بيبعت notification للـ on-call engineer."""
    print(f"[Activity] Notifying on-call: {incident_id} is {severity}")
    await asyncio.sleep(1)
    return f"Engineer notified for {incident_id}"


# 2. WORKFLOW - الـ orchestrator

@workflow.defn
class IncidentWorkflow:
    """
    Workflow كامل لـ incident.
    Temporal بيحفظ كل خطوة - لو السيرفر وقع وقام تاني
    بيكمل من نفس المكان.
    """

    @workflow.run
    async def run(self, incident_id: str) -> dict:
        print(f"\n[Workflow] Incident started: {incident_id}")

        # الخطوة 1: Triage
        severity = await workflow.execute_activity(
            triage_incident,
            incident_id,
            start_to_close_timeout=timedelta(seconds=30),
        )
        print(f"[Workflow] Triage done - severity: {severity}")

        # الخطوة 2: Notify
        notification = await workflow.execute_activity(
            notify_on_call,
            args=[incident_id, severity],
            start_to_close_timeout=timedelta(seconds=30),
        )
        print(f"[Workflow] {notification}")

        return {
            "incident_id": incident_id,
            "severity": severity,
            "status": "TRIAGED",
        }


# 3. MAIN - شغّل الـ worker والـ workflow

async def main():
    client = await Client.connect("localhost:7233")

    async with Worker(
        client,
        task_queue="incidents",
        workflows=[IncidentWorkflow],
        activities=[triage_incident, notify_on_call],
    ):
        print("\n[Worker] Running on queue: 'incidents'")
        print("[Worker] Triggering IncidentWorkflow...\n")

        result = await client.execute_workflow(
            IncidentWorkflow.run,
            "INC-1042",
            id="incident-INC-1042",
            task_queue="incidents",
        )

        print(f"\nWorkflow completed!")
        print(f"Result: {result}")
        print(f"\nOpen http://localhost:8088 to see the workflow recorded!")


if __name__ == "__main__":
    asyncio.run(main())
