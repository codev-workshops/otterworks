from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "billing-service"
    schema_name: str = "billing_svc"
    database_url: str
    cors_origins: list[str] = ["http://localhost:3000"]
    allow_internal_reset: bool = False
    log_format: str = Field(
        default="json", validation_alias=AliasChoices("BILLING_SVC_LOG_FORMAT", "LOG_FORMAT")
    )
    tracing_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("BILLING_SVC_TRACING_ENABLED", "TRACING_ENABLED"),
    )

    model_config = {"env_prefix": "BILLING_SVC_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
