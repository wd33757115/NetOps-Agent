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


def release_replay(template_text, current_text, samples, outputs):
    """Replay an imported template before it goes into force, in a process that can be cut off.

    The samples a package carried must parse to what they parsed to when it was
    exported; the outputs this environment already parsed must still yield records
    wherever the template in force yields them.
    """
    from .transfer_format import canonical
    try:
        run = subprocess.run([sys.executable, '-m', __name__],
                             input=canonical({'mode': 'release_replay', 'template_text': template_text,
                                              'current_text': current_text, 'samples': samples, 'outputs': outputs}),
                             capture_output=True, timeout=25, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = json.loads(run.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        return {'passed': False, 'reason': '回放超过 25 秒没有结束，模板里可能有过慢的正则'}
    if run.returncode or result.get('error'):
        return {'passed': False, 'reason': '回放出错：' + str(result.get('error') or '进程失败')[:300]}
    return result['replay']


def _release_replay(data):
    from .transfer_service import parse_sample
    template, current = data['template_text'], data.get('current_text')
    try:
        parse_sample(template, '')
    except Exception as exc:
        return {'passed': False, 'reason': f'模板无法编译：{exc}'[:300], 'samples': 0, 'outputs': 0}
    failures = []
    passed_samples = 0
    for index, sample in enumerate(data['samples']):
        try:
            matched = parse_sample(template, sample['text']) == sample['expected']
        except Exception as exc:
            matched, why = False, str(exc)
        else:
            why = '解析结果与导出时不同'
        if matched:
            passed_samples += 1
        else:
            failures.append(f'包内样本 {index + 1}：{why}'[:200])
    parsed, emptied, differing = 0, 0, 0
    for index, text in enumerate(data['outputs']):
        try:
            after = parse_sample(template, text)
        except Exception as exc:
            failures.append(f'本地输出 {index + 1}：解析出错：{exc}'[:200])
            continue
        parsed += 1
        if current is None:
            continue
        try:
            before = parse_sample(current, text)
        except Exception:
            continue
        had = sum(len(records) for records in before.values())
        has = sum(len(records) for records in after.values())
        if had and not has:
            emptied += 1
            failures.append(f'本地输出 {index + 1}：现用模板解析出 {had} 条记录，导入的模板一条也没有')
        for name, records in before.items():
            theirs = after.get(name, [])
            for old, new in zip(records, theirs):
                if any(str(old.get(key)) != str(new.get(key)) for key in old if key in new):
                    differing += 1
    evidence = len(data['samples']) + len(data['outputs'])
    reason = None
    if not evidence:
        reason = '没有可以用来验证的输出：导出时勾选“携带已脱敏的验证样本”重新导出，或先让匹配的设备在这里巡检一次'
    elif failures:
        reason = failures[0]
    return {'passed': bool(evidence) and not failures, 'reason': reason, 'samples': len(data['samples']),
            'samples_passed': passed_samples, 'outputs': len(data['outputs']), 'outputs_parsed': parsed,
            'outputs_emptied': emptied, 'records_differing': differing, 'failures': failures[:5]}


if __name__ == '__main__':
    from .transfer_service import validate_content, parse_sample
    try:
        data = json.loads(sys.stdin.buffer.read())
        if data.get('mode') == 'release_replay':
            result = {'replay': _release_replay(data)}
        elif data.get('mode') == 'baseline':
            assets = {a['ref']: a for a in data['assets']}
            for sample in data['samples']:
                sample['expected'] = parse_sample(assets[sample['parser']['$ref']]['data']['template_text'], sample['text'])
            result = {'samples': data['samples']}
        else:
            result = {'reports': validate_content(data)}
    except Exception as exc:
        result = {'error': str(exc)[:2000]}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
