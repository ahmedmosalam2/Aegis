from __future__ import annotations
import time
from dataclasses import dataclass
from threading import Lock

from apps.core.logging import get_logger

logger = get_logger("safety.rate_limiter")



@dataclass
class ExecutionRecord:
    action_name: str
    service_name: str
    executed_at: float  
    incident_id: str = ""




class InMemoryRateLimiter:

    CLEANUP_WINDOW_SECONDS = 3600

    def __init__(self):
        self._lock = Lock()
        self._history: list[ExecutionRecord] = []



    def check(
        self,
        action_name: str,
        service_name: str,
        window_seconds: int = 300,
        max_count: int = 3,
    ) -> tuple[bool, str]:

        now = time.time()
        cutoff = now - window_seconds

        with self._lock:
            recent_executions = [
                record for record in self._history
                if record.action_name == action_name
                and record.service_name == service_name
                and record.executed_at >= cutoff
            ]

        count = len(recent_executions)

        if count >= max_count:
            window_minutes = window_seconds // 60
            reason = (
                f"Rate limit exceeded: '{action_name}' on '{service_name}' "
                f"has been executed {count} time(s) in the last {window_minutes} "
                f"minute(s) (max allowed: {max_count}). "
                f"Repeated execution without success indicates escalation is needed."
            )
            logger.warning(f"[RateLimiter] {reason}")
            return False, reason

        return True, ""

    def record(
        self,
        action_name: str,
        service_name: str,
        incident_id: str = "",
    ) -> None:

        with self._lock:
            self._history.append(ExecutionRecord(
                action_name=action_name,
                service_name=service_name,
                executed_at=time.time(),
                incident_id=incident_id,
            ))
            self._cleanup()

        logger.debug(
            f"[RateLimiter] Recorded: {action_name} on {service_name}",
            extra={"extra_data": {"incident_id": incident_id}},
        )

    def get_execution_count(
        self,
        action_name: str,
        service_name: str,
        window_seconds: int = 300,
    ) -> int:
      
        now = time.time()
        cutoff = now - window_seconds

        with self._lock:
            return sum(
                1 for record in self._history
                if record.action_name == action_name
                and record.service_name == service_name
                and record.executed_at >= cutoff
            )

    def _cleanup(self) -> None:
        """Remove records older than CLEANUP_WINDOW_SECONDS to prevent unbounded memory growth."""
        cutoff = time.time() - self.CLEANUP_WINDOW_SECONDS
        before = len(self._history)
        self._history = [r for r in self._history if r.executed_at >= cutoff]
        after = len(self._history)
        if before != after:
            logger.debug(f"[RateLimiter] Cleaned up {before - after} old records")


_rate_limiter = InMemoryRateLimiter()


def get_rate_limiter() -> InMemoryRateLimiter:
    
    return _rate_limiter
