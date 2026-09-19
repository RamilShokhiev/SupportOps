from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / '.env'), extra='ignore')
    mode: Literal['demo', 'live'] = 'demo'
    database_url: str = 'postgresql+psycopg://supportops:supportops-local@127.0.0.1:5432/supportops'
    postgres_host: str | None = None
    postgres_port: int = 5432
    postgres_user: str = 'supportops'
    postgres_password: str = 'supportops-local'
    postgres_db: str = 'supportops'
    checkpoint_sqlite_path: str = str(PROJECT_ROOT / 'checkpoints.db')
    llm_provider: Literal['demo', 'openai'] = 'demo'
    embedding_provider: Literal['demo', 'openai'] = 'demo'
    issue_provider: Literal['demo', 'github'] = 'demo'
    openai_api_key: str = ''
    openai_model: str = 'gpt-4.1-mini'
    embedding_model: str = 'text-embedding-3-small'
    embedding_dimensions: int = 256
    max_output_tokens: int = 1200
    api_timeout_seconds: float = 2.0
    llm_timeout_seconds: float = 30.0
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    embedding_cost_per_million: float | None = None
    retailbridge_url: str = 'http://127.0.0.1:8001'
    github_token: str = ''
    github_repositories: dict[str, str] = {}
    github_api_url: str = 'https://api.github.com'
    github_api_version: str = '2026-03-10'
    demo_issue_mode: Literal['normal', 'timeout_before', 'timeout_after', 'error'] = 'normal'
    seed_demo: bool = True
    demo_password: str = 'demo-supportops'
    cookie_secure: bool = False
    session_hours: int = 12
    allowed_origins: list[str] = ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:8000', 'http://127.0.0.1:8000']
    langfuse_public_key: str = ''
    langfuse_enabled: bool = False
    langfuse_secret_key: str = ''
    langfuse_base_url: str = 'https://cloud.langfuse.com'
    data_dir: Path = PROJECT_ROOT / 'data'
    frontend_dist: Path = PROJECT_ROOT / 'frontend' / 'dist'
    worker_poll_seconds: float = 1.0

    @model_validator(mode='after')
    def container_database_url(self):
        # Compose passes separate components so reserved password characters stay valid.
        if self.postgres_host:
            self.database_url = URL.create(
                'postgresql+psycopg', username=self.postgres_user,
                password=self.postgres_password, host=self.postgres_host,
                port=self.postgres_port, database=self.postgres_db,
            ).render_as_string(hide_password=False)
        return self


settings = Settings()
