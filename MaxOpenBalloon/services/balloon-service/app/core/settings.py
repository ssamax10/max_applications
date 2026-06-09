from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    service_name: str = 'balloon-service'
    tenant_header: str = 'X-Tenant-ID'

    auth_required: bool = False
    oidc_jwks_url: str = ''
    oidc_issuer: str = ''
    oidc_audience: str = ''
    tenant_claim: str = Field(default='tenant_id')
    otel_exporter_otlp_endpoint: str = ''

    database_url: str = 'postgresql://max:draw@postgres:5432/maxopenballoon'


settings = ServiceSettings()
