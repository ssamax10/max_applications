from pydantic import BaseModel


class mcp_service_event(BaseModel):
    tenant_id: str
    aggregate_id: str
    event_type: str
