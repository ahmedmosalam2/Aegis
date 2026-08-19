from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class EventBase(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=50)
    source: str = Field(..., min_length=1, max_length=100)
    severity: str = Field(default="medium", min_length=1, max_length=20)
    message: str = Field(..., min_length=1)


class EventCreate(EventBase):
    pass


class EventUpdate(BaseModel):
    event_type: str | None = Field(default=None, min_length=1, max_length=50)
    source: str | None = Field(default=None, min_length=1, max_length=100)
    severity: str | None = Field(default=None, min_length=1, max_length=20)
    message: str | None = Field(default=None, min_length=1)


class EventResponse(EventBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True
