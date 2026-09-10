import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.core.logging import request_id_ctx, get_logger
from apps.core.telemetry import REQUEST_DURATION

logger = get_logger("middleware")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Use client-provided X-Request-ID or generate one
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        token = request_id_ctx.set(req_id)

        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                f"{request.method} {request.url.path} → 500",
                extra={"extra_data": {"request_id": req_id}},
            )
            REQUEST_DURATION.labels(
                method=request.method,
                path=request.url.path,
                status_code="500",
            ).observe(time.perf_counter() - start)
            raise
        finally:
            duration_s = time.perf_counter() - start
            duration_ms = round(duration_s * 1000, 2)
            
            logger.info(
                f"{request.method} {request.url.path} → {response.status_code} ({duration_ms}ms)",
                extra={"extra_data": {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                }},
            )
            
            REQUEST_DURATION.labels(
                method=request.method,
                path=request.url.path,
                status_code=str(response.status_code),
            ).observe(duration_s)
            
            request_id_ctx.reset(token)

        response.headers["X-Request-ID"] = req_id
        return response
