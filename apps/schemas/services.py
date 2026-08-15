from pydantic import BaseModel, Field

class ServiceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = None
    environment: str = Field(
        default="development",
        min_length=1,
        max_length=50,
    )
    health_check_url: str | None = None
    status: str = Field(
        default="unknown",
        min_length=1,
        max_length=20,
    )


class ServiceUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )
    description: str | None = None
    environment: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    health_check_url: str | None = None
    status: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )

class ServiceResponse(BaseModel):
    id: str
    name: str
    description: str | None
    environment: str
    health_check_url: str | None
    status: str