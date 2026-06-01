from pydantic import BaseModel


class ServiceSettings(BaseModel):
    service_name: str = "balloon-service"
    tenant_header: str = "X-Tenant-ID"


settings = ServiceSettings()
