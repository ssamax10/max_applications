from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    service_name: str = 'drawing-service'
    tenant_header: str = 'X-Tenant-ID'
    database_url: str = 'postgresql://max:draw@postgres:5432/maxopenballoon'
    minio_endpoint: str = 'minio:9000'
    minio_access_key: str = 'minio'
    minio_secret_key: str = 'minio123'
    minio_secure: bool = False
    drawings_bucket: str = 'drawings'

    auth_required: bool = False
    oidc_jwks_url: str = ''
    oidc_issuer: str = ''
    oidc_audience: str = ''
    tenant_claim: str = Field(default='tenant_id')
    otel_exporter_otlp_endpoint: str = ''


settings = ServiceSettings()
