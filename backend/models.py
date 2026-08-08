# models.py — Pydantic data models for the API.
pass

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from alerts_engine import RULE_TYPES

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    ticker: str | None = None

class PeerQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tickers: list[str] = Field(min_length=1, max_length=6)

    @field_validator("tickers")
    @classmethod
    def normalise(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip().upper() for t in v if t and t.strip()]
        if not cleaned:
            raise ValueError("At least one ticker is required")
        seen, out = set(), []
        for t in cleaned:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

class CompareFilingsRequest(BaseModel):
    ticker: str
    question: str = Field(min_length=1, max_length=2000)
    earlier_filing_id: int | None = None
    later_filing_id: int | None = None

class QuerySource(BaseModel):
    marker: int
    ticker: str
    filing_type: str
    filing_date: str
    section: str
    source_url: str
    chunk_id: int
    excerpt: str = ""
    excerpt_truncated: bool = False
    distance: float | None = None

class QueryResponse(BaseModel):
    answer: str
    sources: list[QuerySource]
    all_sources: list[QuerySource] = []
    generated: bool = True

class PredictionResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    ticker: str
    prediction_date: date
    target_date: date
    predicted_direction: str
    confidence: float
    prob_up: float | None = None
    actual_direction: str | None = None
    model_name: str | None = None

class BacktestSummary(BaseModel):
    ticker: str
    total_predictions: int
    resolved_predictions: int
    correct_predictions: int
    accuracy: float | None = None

class WatchlistAdd(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)

class WatchlistItem(BaseModel):
    ticker: str
    added_at: datetime

class AlertCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    rule_type: str
    threshold: float | None = None

    @field_validator("rule_type")
    @classmethod
    def known_rule(cls, v: str) -> str:
        if v not in RULE_TYPES:
            raise ValueError(f"rule_type must be one of: {', '.join(RULE_TYPES)}")
        return v

class NaturalLanguageAlertCreate(BaseModel):
    text: str = Field(min_length=3, max_length=500)

class AlertResponse(BaseModel):
    id: int
    ticker: str
    rule_type: str
    threshold: float | None
    natural_language: str | None
    is_active: bool
    last_fired_at: datetime | None
    created_at: datetime
    description: str = ""

class AlertEventResponse(BaseModel):
    id: int
    alert_id: int
    ticker: str
    message: str
    is_read: bool
    emailed: bool
    created_at: datetime

class SandboxRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    ticker: str = Field(min_length=1, max_length=10)
    start_date: date | None = None
    end_date: date | None = None
    starting_cash: float = Field(default=10000.0, gt=0)
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    model_version_id: int | None = None