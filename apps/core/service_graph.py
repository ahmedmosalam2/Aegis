
SERVICE_DEPENDENCIES: dict[str, list[str]] = {
    "api-gateway": [
        "order-service",
        "payment-service",
        "inventory-service",
    ],
    "order-service": [
        "payment-service",
        "inventory-service",
        "notification-service",
    ],
    "payment-service": [
        "postgres",
    ],
    "inventory-service": [
        "postgres",
    ],
    "notification-service": [],
}


# ── Default Services ────────────────────────────────────────────────
# Seeded into the database on first startup.

DEFAULT_SERVICES = [
    {
        "name": "api-gateway",
        "description": "Main entry point — routes client requests to backend services",
        "environment": "production",
        "health_check_url": "http://api-gateway:8080/health",
    },
    {
        "name": "order-service",
        "description": "Handles order creation, updates, and lifecycle management",
        "environment": "production",
        "health_check_url": "http://order-service:8081/health",
    },
    {
        "name": "payment-service",
        "description": "Processes payments, refunds, and billing operations",
        "environment": "production",
        "health_check_url": "http://payment-service:8082/health",
    },
    {
        "name": "inventory-service",
        "description": "Manages product stock levels and reservations",
        "environment": "production",
        "health_check_url": "http://inventory-service:8083/health",
    },
    {
        "name": "notification-service",
        "description": "Sends email, SMS, and push notifications",
        "environment": "production",
        "health_check_url": "http://notification-service:8084/health",
    },
]


# ── Graph Utilities ─────────────────────────────────────────────────


def get_dependencies(service_name: str) -> list[str]:
    """Get direct dependencies of a service.

    Example: get_dependencies("order-service")
             → ["payment-service", "inventory-service", "notification-service"]
    """
    return SERVICE_DEPENDENCIES.get(service_name, [])


def get_dependents(service_name: str) -> list[str]:
    """Get services that directly depend on this service.

    Example: get_dependents("payment-service")
             → ["api-gateway", "order-service"]
    """
    return [
        svc
        for svc, deps in SERVICE_DEPENDENCIES.items()
        if service_name in deps
    ]


def get_dependency_chain(service_name: str) -> set[str]:
    """Get the full transitive set of services affected if this one fails.

    Walks the dependency graph upward — finds every service that
    directly or indirectly depends on the given service.

    Example: get_dependency_chain("postgres")
             → {"payment-service", "inventory-service",
                "order-service", "api-gateway"}
    """
    affected: set[str] = set()
    queue = [service_name]

    while queue:
        current = queue.pop(0)
        for dependent in get_dependents(current):
            if dependent not in affected:
                affected.add(dependent)
                queue.append(dependent)

    return affected


def would_cascade(service_name: str) -> bool:
    """Check if a failure in this service would cascade to others."""
    return len(get_dependents(service_name)) > 0
