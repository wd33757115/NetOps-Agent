"""Create a Windows .env once. Never rotate credentials on an existing install."""
from pathlib import Path
import argparse
import base64
import re
import secrets


def prepare(root: Path) -> bool:
    destination = root / '.env'
    if destination.exists():
        return False
    text = (root / '.env.example').read_text(encoding='utf-8-sig')
    values = {
        'SECRET_KEY': secrets.token_hex(32),
        'JWT_SECRET_KEY': secrets.token_hex(32),
        'NETOPS_APP_INTERNAL_SECRET': secrets.token_hex(32),
        'ITSM_WEBHOOK_SECRET': secrets.token_hex(32),
        'POSTGRES_PASSWORD': secrets.token_hex(24),
        'MINIO_ACCESS_KEY': 'netops',
        'MINIO_SECRET_KEY': secrets.token_hex(24),
        # A Fernet key: SSH session passwords and temporary device credentials are encrypted with it.
        'DEVICE_ACCESS_CREDENTIAL_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'POSTGRES_HOST': '127.0.0.1',
        'REDIS_HOST': '127.0.0.1',
        'MINIO_ENDPOINT': '127.0.0.1:9000',
        'CELERY_BROKER_URL': 'amqp://guest:guest@127.0.0.1:5672//',
        'CELERY_RESULT_BACKEND': 'redis://127.0.0.1:6379/1',
        'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1',
    }
    for key, value in values.items():
        pattern = rf'^{re.escape(key)}=.*$'
        if re.search(pattern, text, re.MULTILINE):
            text = re.sub(pattern, lambda _: f'{key}={value}', text, flags=re.MULTILINE)
        else:
            text += f'\n{key}={value}\n'
    # Exclusive create protects an environment created concurrently.
    with destination.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print('Created .env; configure DEEPSEEK_API_KEY.' if prepare(args.root) else 'Existing .env preserved.')
