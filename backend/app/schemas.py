from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Input):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class TicketCreate(Input):
    subject: str = Field(min_length=3, max_length=200)
    text: str = Field(min_length=5, max_length=16000)
    language: Literal['en', 'ru', 'tr'] = 'en'
    diagnostic_mode: Literal['normal', 'timeout', 'error'] = 'normal'

    @field_validator('subject', 'text')
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError('Must not be blank')
        return value.strip()


class TicketImport(Input):
    tickets: list[TicketCreate] = Field(min_length=1, max_length=30)


class Clarification(Input):
    text: str = Field(min_length=5, max_length=4000)


class AnalyzeRequest(Input):
    workflow_mode: Literal['standard', 'multi_agent_review'] | None = None


class Review(Input):
    decision: Literal['accepted', 'rejected']
    reason: str = Field(default='', max_length=1000)


class ActionVersion(Input):
    version: int = Field(ge=1)


class ActionDecision(ActionVersion):
    reason: str = Field(default='', max_length=1000)


class ActionEdit(ActionVersion):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=5, max_length=16000)
