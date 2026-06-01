from pydantic import BaseModel


class ServiceSettings(BaseModel):
    service_name: str = "revision-service"
    tenant_header: str = "X-Tenant-ID"


settings = ServiceSettings()
