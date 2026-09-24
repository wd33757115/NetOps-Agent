from contextlib import nullcontext
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.rule_governance.backend import api as routes
from src.auth.models import CurrentUser


def test_bulk_endpoint_validates_bounds_and_uses_authenticated_actor(monkeypatch):
    from src.apps.rule_governance.backend import access
    calls = []
    class Service:
        def bulk(self, **kwargs):
            calls.append(kwargs)
            return {'results': [{'id': 'event-v1', 'success': True}], 'succeeded': 1, 'failed': 0}
    monkeypatch.setattr(routes, 'PublishedAssetService', Service)
    monkeypatch.setattr(access, 'execution_lock', lambda: nullcontext())
    app = FastAPI()
    app.include_router(routes.router)
    async def user(request: routes.Request):
        request.state.current_user = CurrentUser(user_id='7', username='app-user', role='viewer')
        return request.state.current_user
    app.dependency_overrides[routes.app_user] = user
    c = TestClient(app)
    for body in ({'kind':'event','action':'delete','ids':[]},
                 {'kind':'event','action':'delete','ids':['x']*101},
                 {'kind':'unknown','action':'delete','ids':['x']}):
        assert c.post('/parser/published-assets/bulk-action',json=body).status_code == 422
    response=c.post('/parser/published-assets/bulk-action',json={'kind':'event','action':'delete','ids':['event-v1']})
    assert response.status_code == 200
    assert calls[0]['actor'] == 'app-user'


def test_bulk_endpoint_requires_app_access():
    app=FastAPI(); app.include_router(routes.router)
    response=TestClient(app).post('/parser/published-assets/bulk-action',json={'kind':'parser','action':'delete','ids':['x']})
    assert response.status_code in (401,403)
