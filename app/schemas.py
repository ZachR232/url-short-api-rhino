from pydantic import BaseModel, HttpUrl


class ShortenRequest(BaseModel):
    url: HttpUrl


class ShortenResponse(BaseModel):
    short_url: str
    short_code: str


class HealthResponse(BaseModel):
    status: str          # "healthy" | "degraded"
    postgres: bool
    redis: bool
