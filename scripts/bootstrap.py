"""Create or update local configuration for account-based authentication."""

import base64
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]


def bootstrap(path: Path, template: Path):
    text = path.read_text(encoding='utf-8') if path.exists() else template.read_text(encoding='utf-8')
    lines = [line for line in text.splitlines() if not line.startswith(('REVIEWER_ALEX_TOKEN=', 'REVIEWER_SAM_TOKEN='))]
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if any(line.startswith('AUTH_ENCRYPTION_KEY=') for line in lines):
        lines = [('AUTH_ENCRYPTION_KEY=' + key if line == 'AUTH_ENCRYPTION_KEY=' else line) for line in lines]
    else:
        lines.append('AUTH_ENCRYPTION_KEY=' + key)
    if not any(line.startswith('AUTH_PUBLIC_ORIGIN=') for line in lines):
        lines.append('AUTH_PUBLIC_ORIGIN=http://127.0.0.1:3000')
    updated = '\n'.join(lines) + '\n'
    if not path.exists() or text != updated:
        temporary = path.with_name('.env.new')
        temporary.write_text(updated, encoding='utf-8')
        temporary.chmod(0o600)
        os.replace(temporary, path)


if __name__ == '__main__':
    bootstrap(ROOT / '.env', ROOT / '.env.example')
    print('.env ready for account sign-in. Existing settings were preserved; legacy reviewer tokens were removed.')
    print('Back up AUTH_ENCRYPTION_KEY securely and do not commit .env.')
