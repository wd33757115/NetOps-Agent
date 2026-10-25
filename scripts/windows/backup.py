"""Binary-safe PostgreSQL dumps and MinIO volume archive; no credentials in argv."""
import argparse
import json
from pathlib import Path
import subprocess


def run(*args: str, **kwargs):
    return subprocess.run(['docker', *args], check=True, timeout=600, **kwargs)


def backup(output: Path, langfuse: bool):
    # Refuse an absent/wrong volume instead of allowing `docker run` to create
    # an empty volume and report a successful object-storage backup.
    info = run('inspect', 'netops-minio', capture_output=True)
    mounts = json.loads(info.stdout)[0]['Mounts']
    if not any(mount.get('Name') == 'deployment_minio-data' and mount['Destination'] == '/data'
               for mount in mounts):
        raise RuntimeError('Unexpected MinIO volume; backup stopped.')
    run('volume', 'inspect', 'deployment_minio-data', stdout=subprocess.DEVNULL)
    output.mkdir(parents=True, exist_ok=False)
    databases = [('netops-postgres', 'postgres.dump')]
    if langfuse:
        databases.append(('netops-langfuse-db', 'langfuse.dump'))
    for container, filename in databases:
        # Binary stdout must bypass Windows PowerShell's text pipeline.
        with (output / filename).open('wb') as target:
            run('exec', container, 'sh', '-c',
                'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"', stdout=target)
    run('run', '--rm', '--network', 'none',
        '--mount', 'type=volume,source=deployment_minio-data,target=/source,readonly',
        '--mount', f'type=bind,source={output.resolve()},target=/backup',
        'alpine:3.21', 'tar', '-czf', '/backup/minio.tar.gz', '-C', '/source', '.')
    (output / 'manifest.json').write_text(json.dumps({
        'databases': [file for _, file in databases],
        'object_storage': 'minio.tar.gz',
        'note': 'Application writers must be stopped for a consistent cross-store backup.',
    }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--langfuse', type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    backup(args.output, bool(args.langfuse))
