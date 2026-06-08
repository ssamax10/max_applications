from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    service_name: str = 'dwg-translation-service'
    tenant_header: str = 'X-Tenant-ID'

    auth_required: bool = False
    oidc_jwks_url: str = ''
    oidc_issuer: str = ''
    oidc_audience: str = ''
    tenant_claim: str = Field(default='tenant_id')
    otel_exporter_otlp_endpoint: str = ''

    database_url: str = 'postgresql://max:draw@postgres:5432/maxopenballoon'
    public_base_url: str = 'http://localhost:18007'
    minio_endpoint: str = 'minio:9000'
    minio_access_key: str = 'minio'
    minio_secret_key: str = 'minio123'
    minio_secure: bool = False
    conversion_timeout_seconds: int = 600
    dwg_conversion_engine: str = 'qcad'
    qcad_cmd: str = '/usr/bin/qcad'
    qcad_script_path: str = '/app/app/scripts/qcad_dwg_to_pdf.js'


settings = ServiceSettings()
