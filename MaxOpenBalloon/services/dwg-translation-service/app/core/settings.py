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
    qcad_wrapper_cmd: str = 'xvfb-run --auto-servernum'
    qcad_dwg2svg_cmd: str = '/usr/bin/dwg2svg'
    qcad_dwg2pdf_cmd: str = '/usr/bin/dwg2pdf'

    # Autodesk Platform Services (APS) for production DWG→PDF conversion
    aps_client_id: str = 'nGVbKvuwlgYD1PDx8BWV6YEGxHRFpnHwShsueYf6FO1ToHpw'
    aps_client_secret: str = 'NlBkaiA9lnSgXLLBrFUnXvP3QtpAuH7qAdkFwojuXsPUky51cgkDwO7IEjBcZndf'
    aps_bucket_key: str = 'aps_shri_dwg_conversion_bucket'


settings = ServiceSettings()
