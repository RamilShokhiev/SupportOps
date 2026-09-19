"""Serve the built UI and synthetic API with disposable local E2E state."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from backend.app.config import Settings
from backend.app.main import create_app
from retailbridge.main import app as retailbridge_app


def main():
    root = Path(__file__).resolve().parents[1]
    if not (root / 'frontend' / 'dist' / 'index.html').exists():
        raise SystemExit('Build the frontend before running E2E: cd frontend && npm run build')
    port = int(os.environ.get('SUPPORTOPS_E2E_PORT', '18765'))
    origin = f'http://127.0.0.1:{port}'
    with TemporaryDirectory(prefix='supportops-e2e-') as temporary:
        state = Path(temporary)
        config = Settings(
            _env_file=None,
            database_url=f'sqlite:///{(state / "app.db").as_posix()}',
            checkpoint_sqlite_path=str(state / 'checkpoints.db'),
            data_dir=root / 'data',
            frontend_dist=root / 'frontend' / 'dist',
            mode='demo', llm_provider='demo', embedding_provider='demo', issue_provider='demo',
            embedding_dimensions=256, demo_issue_mode='normal',
            seed_demo=True, demo_password='demo-supportops', cookie_secure=False,
            openai_api_key='', github_token='', langfuse_enabled=False,
            retailbridge_url=f'{origin}/retailbridge', api_timeout_seconds=0.25,
            allowed_origins=[origin],
        )
        application = create_app(config, initialize=True)
        # Real HTTP diagnostics share the test port; production processes are untouched.
        application.mount('/retailbridge', retailbridge_app)
        uvicorn.run(application, host='127.0.0.1', port=port, log_level='warning')


if __name__ == '__main__':
    main()
