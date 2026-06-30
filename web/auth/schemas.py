from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    username: str


class UserResponse(BaseModel):
    id: str
    username: str


class LLMSettingsRequest(BaseModel):
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None


class LLMSettingsResponse(BaseModel):
    llm_model: str | None = None
    llm_base_url: str | None = None
    has_api_key: bool = False
