from pydantic import BaseModel


class drawing_service_event(BaseModel):
    tenant_id: str
    aggregate_id: str
    event_type: str
