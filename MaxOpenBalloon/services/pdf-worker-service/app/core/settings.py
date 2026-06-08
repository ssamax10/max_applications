from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    service_name: str = 'pdf-worker-service'
    default_dpi: int = 400
    tile_size: int = 1024
    tile_overlap: float = 0.15
    max_pdf_bytes: int = 30 * 1024 * 1024
    vector_word_threshold: int = 8


settings = ServiceSettings()
