"""Provider sharing API. Local administrator in a real browser only: never the Agent, an API token or a proxy."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.application_modes import require_browser_admin, require_local
from src.provider_sync import SyncError


class Target(BaseModel):
    id: str


class Share(BaseModel):
    id: str
    enabled: bool


class CopyKey(BaseModel):
    id: str
    confirm: bool = False


def _reader(request):
    from core.middleware import require_admin
    require_local(request)
    if getattr(request.state, 'current_user', None) in ('internal-tool', 'api', 'demo', 'system') \
            or 'authorization' in request.headers or 'x-odysseus-internal-token' in request.headers:
        raise HTTPException(403, 'A direct local browser administrator is required')
    require_admin(request)


def make_provider_sync_router(get_service):
    router = APIRouter(prefix='/api/provider-sync', tags=['provider-sync'])

    def service():
        current = get_service()
        if current is None:
            raise HTTPException(404, 'Provider sharing needs Hermes mode to be configured.')
        return current

    def guarded(action):
        try:
            return action()
        except SyncError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('')
    def catalog(request: Request):
        _reader(request)
        return service().snapshot()

    @router.post('/refresh')
    def refresh(request: Request):
        require_browser_admin(request)
        return service().snapshot(refresh=True)

    @router.post('/share')
    def share(body: Share, request: Request):
        require_browser_admin(request)
        return guarded(lambda: service().set_share(body.id, body.enabled))

    @router.post('/apply')
    def apply(body: Target, request: Request):
        require_browser_admin(request)
        return guarded(lambda: service().apply(body.id))

    @router.post('/apply-all')
    def apply_all(request: Request):
        require_browser_admin(request)
        return {'results': service().apply_all()}

    @router.post('/copy-key')
    def copy_key(body: CopyKey, request: Request):
        require_browser_admin(request)
        if body.confirm is not True:
            raise HTTPException(400, 'Confirm that you want to copy this key.')
        return guarded(lambda: service().copy_key(body.id))

    @router.post('/test')
    def test(body: Target, request: Request):
        require_browser_admin(request)
        return guarded(lambda: service().test(body.id))

    return router
