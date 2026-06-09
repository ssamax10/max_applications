from pydantic import BaseModel


class balloon_service_event(BaseModel):
    tenant_id: str
    aggregate_id: str
    event_type: str
