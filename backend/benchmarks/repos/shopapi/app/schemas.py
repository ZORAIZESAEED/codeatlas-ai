import re

from pydantic import BaseModel, Field, field_validator

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: str
    password: str = Field(min_length=8)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        if not EMAIL_PATTERN.match(value):
            raise ValueError("invalid email address")
        return value.lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProductCreate(BaseModel):
    """Payload for creating a product; rejects empty names and non-positive prices."""

    name: str = Field(min_length=1, max_length=120)
    price: float
    stock: int = 0

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("price must be positive")
        return round(value, 2)

    @field_validator("stock")
    @classmethod
    def stock_not_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("stock cannot be negative")
        return value
