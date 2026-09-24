"""Short-lived, timeout-bound compiler and sample replay worker."""
import json
import subprocess
import sys


def baseline_samples(assets, samples):
    from .transfer_format import canonical
    try:
        run = subprocess.run([sys.executable, '-m', __name__], input=canonical({'mode': 'baseline', 'assets': assets, 'samples': samples}),
                             capture_output=True, timeout=25, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = json.loads(run.stdout)
    except (subprocess.TimeoutExpired, ValueError) as exc:
        raise ValueError('样本基线生成失败或超时') from exc
    if run.returncode or result.get('error'):
        raise ValueError(result.get('error', '样本基线生成失败'))
    return result['samples']


if __name__ == '__main__':
    from .transfer_service import validate_content, parse_sample
    try:
        data = json.loads(sys.stdin.buffer.read())
        if data.get('mode') == 'baseline':
            assets = {a['ref']: a for a in data['assets']}
            for sample in data['samples']:
                sample['expected'] = parse_sample(assets[sample['parser']['$ref']]['data']['template_text'], sample['text'])
            result = {'samples': data['samples']}
        else:
            result = {'reports': validate_content(data)}
    except Exception as exc:
        result = {'error': str(exc)[:2000]}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
