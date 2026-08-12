from pydantic import BaseModel, Field
from typing import Optional


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    severity: str = Field(..., min_length=1, max_length=20)
  

class IncidentResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    severity: str
    status: str
    created_at: Optional[str]
    updated_at: Optional[str]

class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = None
    severity: str | None = Field(default=None, min_length=1, max_length=20)
    status: str | None = Field(default=None, min_length=1, max_length=20)