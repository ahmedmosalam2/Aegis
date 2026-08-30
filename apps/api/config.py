from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Aegis AI"
    ENVIRONMENT: str = "development"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str = (
        "postgresql+asyncpg://aegis:aegis_dev_password@localhost:5432/aegis"
    )

    # Temporal
    TEMPORAL_ADDRESS: str = "localhost:7233"

    # Observability
    LOG_LEVEL: str = "INFO"
    OTEL_ENABLED: bool = True
    OTEL_EXPORTER_ENDPOINT: str = "http://localhost:4317"
    PROMETHEUS_ENABLED: bool = True

    class Config:
        env_file = ".env"


settings = Settings()