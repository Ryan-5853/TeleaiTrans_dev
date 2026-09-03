from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GROUND_", env_file=".env", extra="ignore"
    )

    db_path: str = "data/ground.db"
    artifact_dir: str = "data/artifacts"
    aircraft_config: str = "config/aircraft.yaml"
    public_base: str = "http://127.0.0.1:8000"

    plane_heartbeat_interval_s: float = 10.0
    plane_timeout_s: float = 30.0
    ui_telemetry_max_hz: float = 5.0
    param_wait_timeout_s: float = 3.0
    claim_ttl_h: float = 24.0
    claim_code_length: int = 12
    aircraft_camera_port: int = 8080
    aircraft_camera_connect_timeout_s: float = 3.0
    aircraft_camera_timeout_s: float = 10.0

    max_artifact_bytes: int = 1_048_576
    plane_max_message_bytes: int = 262_144


@lru_cache
def get_settings() -> Settings:
    return Settings()
