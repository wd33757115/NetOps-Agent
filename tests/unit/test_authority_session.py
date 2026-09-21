import asyncio
import threading

import httpx
import pytest
from fastapi import HTTPException

from src.platform.authorization import client as authority


@pytest.fixture
def transport(monkeypatch):
    pools, requests = [], []
    real_client = httpx.Client
    revoked = set()

    def handle(request):
        actor = request.headers["authorization"].removeprefix("Bearer ").split(":")[0]
        requests.append(actor)
        if actor in revoked:
            return httpx.Response(403)
        return httpx.Response(
            200,
            json={
                "data": {
                    "user": {
                        "user_id": actor,
                        "username": actor,
                        "role": "viewer",
                    }
                }
            },
        )

    def factory(**kwargs):
        client = real_client(transport=httpx.MockTransport(handle), **kwargs)
        pools.append(client)
        return client

    monkeypatch.setattr(authority.httpx, "Client", factory)
    monkeypatch.setattr(authority, "service_token", lambda actor, app: f"{actor}:{app}")
    monkeypatch.setattr(authority, "authority_url", lambda: "http://authority")
    monkeypatch.setattr("src.platform.kernel.runtime.token", lambda: "service")
    return pools, requests, revoked


def test_pool_reuses_connection_but_rechecks_every_decision(transport):
    pools, requests, revoked = transport

    async def run():
        async with authority.authority_session():
            first = await asyncio.to_thread(authority.authorize_worker, "alice", "assistant")
            assert first.user_id == "alice"
            revoked.add("alice")
            with pytest.raises(HTTPException) as exc:
                await asyncio.to_thread(authority.authorize_worker, "alice", "assistant")
            assert exc.value.status_code == 403
        assert authority._connection.get() is None

    asyncio.run(run())
    assert requests == ["alice", "alice"]
    assert len(pools) == 1 and pools[0].is_closed


def test_concurrent_runs_have_separate_pools_and_request_identity(transport):
    pools, requests, _ = transport

    async def run(actor):
        async with authority.authority_session():
            pool = authority._connection.get()
            await asyncio.sleep(0)
            for _ in range(2):
                user = await asyncio.to_thread(authority.authorize_worker, actor, "assistant")
                assert user.user_id == actor
                assert authority._connection.get() is pool

    async def both():
        await asyncio.gather(run("alice"), run("bob"))

    asyncio.run(both())
    assert sorted(requests) == ["alice", "alice", "bob", "bob"]
    assert len(pools) == 2 and all(p.is_closed for p in pools)


def test_cancel_during_pool_construction_closes_created_client(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_client = httpx.Client
    pool = real_client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    def delayed(**kwargs):
        entered.set()
        assert release.wait(3)
        return pool

    monkeypatch.setattr(authority.httpx, "Client", delayed)

    async def scoped():
        async with authority.authority_session():
            pytest.fail("Cancelled construction must not enter the scope")

    async def run():
        task = asyncio.create_task(scoped())
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert authority._connection.get() is None

    asyncio.run(run())
    assert pool.is_closed
