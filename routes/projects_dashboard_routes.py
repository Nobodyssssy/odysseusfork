"""Projects dashboard API: read-only, local browser administrator only (same gate as the provider catalog)."""
from fastapi import APIRouter, HTTPException, Request

from src.application_modes import require_local


def _reader(request):
    from core.middleware import require_admin
    require_local(request)
    if getattr(request.state, 'current_user', None) in ('internal-tool', 'api', 'demo', 'system') \
            or 'authorization' in request.headers or 'x-odysseus-internal-token' in request.headers:
        raise HTTPException(403, 'A direct local browser administrator is required')
    require_admin(request)


def make_projects_dashboard_router(get_dashboard, guard=_reader):
    router = APIRouter(prefix='/api/projects-dashboard', tags=['projects-dashboard'])

    @router.get('')
    def overview(request: Request):
        guard(request)
        return get_dashboard().overview()

    @router.get('/sessions')
    def sessions(request: Request):
        guard(request)
        return {'sessions': get_dashboard().sessions()}

    @router.get('/project/{project_id}')
    def project(project_id: str, request: Request):
        guard(request)
        result = get_dashboard().detail(project_id, port=request.url.port or 7000)
        if result is None:
            raise HTTPException(404, 'Unknown project')
        return result

    return router
