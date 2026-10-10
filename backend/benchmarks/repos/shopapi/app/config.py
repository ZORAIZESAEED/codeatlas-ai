"""Application settings loaded from environment variables."""
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://shop:shop@localhost:5432/shop"
    db_pool_size: int = 10
    jwt_secret: str = "change-me"
    token_expiry_minutes: int = 30
    upload_dir: str = "./uploads"
    allowed_image_types: tuple[str, ...] = ("image/png", "image/jpeg")
    smtp_host: str = "localhost"


@lru_cache
def get_settings() -> Settings:
    """Read configuration once from SHOP_* environment variables."""
    return Settings(
        database_url=os.environ.get("SHOP_DATABASE_URL", Settings.database_url),
        db_pool_size=int(os.environ.get("SHOP_DB_POOL_SIZE", Settings.db_pool_size)),
        jwt_secret=os.environ.get("SHOP_JWT_SECRET", Settings.jwt_secret),
        token_expiry_minutes=int(
            os.environ.get("SHOP_TOKEN_EXPIRY_MINUTES", Settings.token_expiry_minutes)
        ),
        upload_dir=os.environ.get("SHOP_UPLOAD_DIR", Settings.upload_dir),
        smtp_host=os.environ.get("SHOP_SMTP_HOST", Settings.smtp_host),
    )
