from pydantic import BaseModel


class dwg_translation_service_event(BaseModel):
    tenant_id: str
    aggregate_id: str
    event_type: str
