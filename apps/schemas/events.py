from IPython import display
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class EventBase(BaseModel):
    event_type: str
    source: str
    severity: str
    message: str

class EventCreate(EventBase):
    pass
    

class EventUpdate(EventBase):
    pass

class EventResponse(EventBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True

