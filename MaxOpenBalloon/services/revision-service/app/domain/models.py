from pydantic import BaseModel


class TenantScopedEntity(BaseModel):
    id: str
    tenant_id: str
