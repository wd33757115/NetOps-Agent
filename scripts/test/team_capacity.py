#!/usr/bin/env python3
"""Repeatable Windows capacity probes. Synthetic computation is NOT SSH acceptance.

HTTP mode uses NETOPS_CAPACITY_TOKEN from the environment; it never prints tokens.
Database mode replays persisted observations through comparison/report code without writes.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor
import io
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def peak_working_set_mb():
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (key, ctypes.c_size_t) for key in ['PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage']]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return round(counters.PeakWorkingSetSize / 1024**2, 1)


def fixture(devices=20):
    # One device at a time, as in the production report path.
    return [dict(entity_type='interface', entity_key=f'eth{i}', field=f'field{f}',
                 command='show interface', value=i + f, dimensions={})
            for i in range(48) for f in range(24)]


def compute_job(devices, start_at=0):
    import textfsm
    from src.apps.inspection.backend.patrol_batch_service import PatrolBatchService
    while time.time() < start_at:
        time.sleep(0.02)
    started = time.time()
    sample = '\n'.join(f'eth{i} up {i * 100}' for i in range(48))
    template = 'Value INTERFACE (\\S+)\nValue STATUS (up|down)\nValue PACKETS (\\d+)\n\nStart\n  ^${INTERFACE} ${STATUS} ${PACKETS} -> Record\n'
    observations = fixture()
    count = 0
    for _ in range(devices):
        rows = textfsm.TextFSM(io.StringIO(template)).ParseText(sample)
        assert len(rows) == 48
        result = PatrolBatchService.domain_checks(observations=observations, command_details=[], changes=[], events=[])
        count += sum(r['observation_count'] for r in result)
    return dict(pid=os.getpid(), started=started, finished=time.time(), devices=devices, observations=count, peak_working_set_mb=peak_working_set_mb())


def database_job(devices, start_at=0):
    # Read-only replay of persisted facts through the production comparison code.
    # No long rollback transaction, invented assets or changes to historical jobs.
    from sqlalchemy import text
    from src.infrastructure.db.postgres import engine
    from src.core.state.repository import StateRepository
    from src.core.patrol.change_detector import detect_observation_changes_against_history
    from src.apps.inspection.backend.patrol_batch_service import PatrolBatchService
    with engine.connect() as conn:
        run_id = conn.execute(text("""select run_id from netops_state_observations
            group by run_id having count(distinct device_id) >= :devices
            order by max(observed_at) desc limit 1"""), {'devices': devices}).scalar()
        if not run_id:
            raise ValueError('No persisted run has the required device count')
        device_ids = list(conn.execute(text("""select distinct device_id from netops_state_observations
            where run_id=:run order by device_id limit :devices"""),
            {'run': run_id, 'devices': devices}).scalars())
    while time.time() < start_at:
        time.sleep(0.02)
    started = time.time()
    repository = StateRepository()
    observations = repository.list_run_observations(run_id, device_ids=device_ids)
    comparison = detect_observation_changes_against_history(
        current_run_id=run_id, device_ids=device_ids, persist=False)
    if not comparison.get('success'):
        raise AssertionError('Read-only comparison failed')
    grouped = {}
    for observation in observations:
        grouped.setdefault(observation['device_id'], []).append(dict(
            entity_type=observation['entity_type'], entity_key=observation['entity_key'],
            field=observation['field_name'], command=observation['canonical_command'],
            value=observation['value_json'], dimensions=observation.get('dimensions_json') or {}))
    domains = 0
    for device_observations in grouped.values():
        domains += len(PatrolBatchService.domain_checks(
            observations=device_observations, command_details=[], changes=[], events=[]))
    return dict(pid=os.getpid(), started=started, finished=time.time(), devices=len(grouped),
                observations=len(observations), domains=domains,
                comparison_success=comparison.get('success'), persisted=False,
                run_id=run_id, peak_working_set_mb=peak_working_set_mb())


def summarize_jobs(jobs):
    edges = sorted([(j['started'], 1) for j in jobs] + [(j['finished'], -1) for j in jobs])
    active = peak = 0
    for _, delta in edges:
        active += delta
        peak = max(active, peak)
    return dict(jobs=jobs, peak_overlapping_jobs=peak, distinct_processes=len({j['pid'] for j in jobs}))


async def http_probe(base_url, users, rounds):
    import httpx
    token = os.environ.get('NETOPS_CAPACITY_TOKEN')
    if not token:
        raise ValueError('NETOPS_CAPACITY_TOKEN is required; use a short-lived test-user access token')
    paths = ['/api/apps/inspection/patrol/offline-jobs/?page_size=20',
             '/api/workflows/runs/patrol-batches/?limit=20',
             '/api/devices/?limit=20']
    timings = {p: [] for p in paths}
    errors = []
    async with httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=True,
                                headers={'Authorization': 'Bearer ' + token},
                                limits=httpx.Limits(max_connections=users, max_keepalive_connections=users)) as client:
        async def visitor(index):
            for turn in range(rounds):
                path = paths[(index + turn) % len(paths)]
                start = time.perf_counter()
                try:
                    response = await client.get(path)
                    if response.status_code != 200:
                        errors.append(dict(path=path, status=response.status_code))
                    else:
                        response.json()  # no login HTML accepted as success
                except Exception as exc:
                    errors.append(dict(path=path, error=type(exc).__name__))
                timings[path].append(time.perf_counter() - start)
        await asyncio.gather(*(visitor(i) for i in range(users)))
    return dict(users=users, requests=users*rounds, errors=errors,
                endpoints={p: dict(count=len(v), mean=statistics.fmean(v),
                                   p95=sorted(v)[min(len(v)-1, int(len(v)*.95))], max=max(v))
                           for p, v in timings.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['compute', 'database', 'http'], required=True)
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--devices', type=int, default=20)
    parser.add_argument('--users', type=int, default=50)
    parser.add_argument('--rounds', type=int, default=6)
    parser.add_argument('--base-url', default='http://127.0.0.1:3000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.jobs, args.devices, args.users, args.rounds) < 1:
        parser.error('counts must be positive')
    if args.mode == 'http':
        report = asyncio.run(http_probe(args.base_url, args.users, args.rounds))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            # Allow imports to complete in all Windows spawn processes.
            start_at = time.time() + 15
            call = compute_job if args.mode == 'compute' else database_job
            futures = [pool.submit(call, args.devices, start_at) for _ in range(args.jobs)]
            report = summarize_jobs([future.result() for future in futures])
    report.update(mode=args.mode, logical_processors=os.cpu_count(), timestamp=time.time())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'jobs'}, ensure_ascii=False))
    return int(bool(report.get('errors')) or (args.mode != 'http' and report['peak_overlapping_jobs'] < args.jobs))


if __name__ == '__main__':
    raise SystemExit(main())
