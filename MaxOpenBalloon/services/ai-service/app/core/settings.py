from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    service_name: str = 'ai-service'
    tenant_header: str = 'X-Tenant-ID'
    database_url: str = 'postgresql://max:draw@postgres:5432/maxopenballoon'

    auth_required: bool = False
    oidc_jwks_url: str = ''
    oidc_issuer: str = ''
    oidc_audience: str = ''
    tenant_claim: str = Field(default='tenant_id')
    otel_exporter_otlp_endpoint: str = ''

    detector_order: str = 'pdf_worker,paddleocr_opencv,florence2,hybrid,heuristic'
    florence2_endpoint: str = ''
    florence2_timeout_seconds: int = 20
    detector_timeout_seconds: int = 20
    pdf_worker_internal_url: str = 'http://pdf-worker-service:8000'
    pdf_worker_timeout_seconds: int = 180

    minio_endpoint: str = 'minio:9000'
    minio_access_key: str = 'minio'
    minio_secret_key: str = 'minio123'
    minio_secure: bool = False
    dwg_translation_internal_url: str = 'http://dwg-translation-service:8000'


settings = ServiceSettings()

