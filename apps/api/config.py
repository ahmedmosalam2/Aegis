from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Aegis AI"
    ENVIRONMENT: str = "development"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    class Config:
        env_file = ".env"


settings = Settings()