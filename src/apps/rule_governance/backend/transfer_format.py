"""Bounded data-only rule exchange format. Never extract an uploaded archive."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from pathlib import PurePosixPath

FORMAT = 'netops.rules/v1'
MAX_ZIP = 12 * 1024 * 1024  # Base64 JSON stays below the platform's 20 MB request limit.
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_ASSETS = 5000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'JSON 键重复：{key}')
        result[key] = value
    return result


def read_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError('不允许非有限数值')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('规则包 JSON 无效') from exc


def pack(assets, roots, recipes=None, samples=None):
    files = {}
    items = []
    for index, asset in enumerate(assets):
        item = {**asset, 'data': dict(asset['data'])}
        if item['kind'] == 'parser':
            path = f'templates/{index}.textfsm'
            files[path] = item['data'].pop('template_text').encode('utf-8')
            item['data']['template_file'] = path
        path = f'assets/{index}.json'
        files[path] = canonical(item)
        items.append(path)
    files['recipes.json'] = canonical(recipes or [])
    files['samples.json'] = canonical(samples or [])
    manifest = {'format': FORMAT, 'roots': roots, 'assets': items,
                'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    files['manifest.json'] = canonical(manifest)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    result = out.getvalue()
    unpack(result)  # Our own output must satisfy exactly the same limits.
    return result


def unpack(raw):
    if len(raw) > MAX_ZIP:
        raise ValueError('规则包不能超过 12 MB')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ASSETS * 2 + 3:
                raise ValueError('规则包文件数量过多')
            files, total = {}, 0
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                if (name in files or path.is_absolute() or '..' in path.parts or '\\' in name
                        or ':' in name or str(path) != name or entry.is_dir()
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise ValueError('规则包包含重复、加密或不安全的文件路径')
                total += entry.file_size
                if entry.file_size > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError('规则包解压内容超过限制')
                with archive.open(entry) as source:
                    content = source.read(MAX_FILE + 1)
                if len(content) != entry.file_size or len(content) > MAX_FILE:
                    raise ValueError('规则包文件大小无效')
                files[name] = content
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise ValueError('规则包损坏或压缩格式不支持') from exc
    manifest = read_json(files.get('manifest.json', b'{}'))
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT:
        raise ValueError('不支持的规则包格式版本')
    hashes = manifest.get('files')
    if not isinstance(hashes, dict) or set(hashes) != set(files) - {'manifest.json'}:
        raise ValueError('规则包清单与文件不一致')
    for name, checksum in hashes.items():
        if hashlib.sha256(files[name]).hexdigest() != checksum:
            raise ValueError(f'文件校验失败：{name}')
    paths = manifest.get('assets')
    if not isinstance(paths, list) or not 0 < len(paths) <= MAX_ASSETS or len(set(paths)) != len(paths):
        raise ValueError('资产清单为空、重复或超过限制')
    assets, refs = [], set()
    used = {'manifest.json', 'recipes.json', 'samples.json', *paths}
    for path in paths:
        if not isinstance(path, str) or path not in files:
            raise ValueError('资产文件缺失')
        asset = read_json(files[path])
        if not isinstance(asset, dict) or set(asset) != {'ref', 'kind', 'data', 'source_status'}:
            raise ValueError('资产结构不支持')
        ref = asset['ref']
        if not isinstance(ref, str) or not ref or len(ref) > 256 or ref in refs:
            raise ValueError('资产引用为空或重复')
        refs.add(ref)
        if not isinstance(asset['data'], dict):
            raise ValueError('资产内容必须为对象')
        if asset['kind'] == 'parser':
            filename = asset['data'].pop('template_file', None)
            if filename not in files or not filename.startswith('templates/'):
                raise ValueError('TextFSM 原文缺失')
            used.add(filename)
            asset['data']['template_text'] = files[filename].decode('utf-8')
        assets.append(asset)
    if used != set(files):
        raise ValueError('规则包含有未声明用途的文件')
    roots = manifest.get('roots')
    if not isinstance(roots, list) or not roots or any(r not in refs for r in roots):
        raise ValueError('所选根资产不存在')
    return {'assets': assets, 'roots': roots, 'recipes': read_json(files['recipes.json']),
            'samples': read_json(files['samples.json'])}
