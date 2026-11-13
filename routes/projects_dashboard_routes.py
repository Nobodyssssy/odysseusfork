"""Projects dashboard API: read-only, local browser administrator only (same gate as the provider catalog)."""
import re

from fastapi import APIRouter, HTTPException, Request, Response

from src.application_modes import require_local


def _reader(request):
    from core.middleware import require_admin
    require_local(request)
    if getattr(request.state, 'current_user', None) in ('internal-tool', 'api', 'demo', 'system') \
            or 'authorization' in request.headers or 'x-odysseus-internal-token' in request.headers:
        raise HTTPException(403, 'A direct local browser administrator is required')
    require_admin(request)


PROJECT_ID = re.compile(r'[0-9a-f]{10}')


def _private(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'


def make_projects_dashboard_router(get_dashboard, guard=_reader):
    router = APIRouter(prefix='/api/projects-dashboard', tags=['projects-dashboard'])

    @router.get('')
    def overview(request: Request, response: Response):
        guard(request)
        _private(response)
        return get_dashboard().overview()

    @router.get('/sessions')
    def sessions(request: Request, response: Response):
        guard(request)
        _private(response)
        return {'sessions': get_dashboard().sessions()}

    @router.get('/project/{project_id}')
    def project(project_id: str, request: Request, response: Response):
        guard(request)
        _private(response)
        if not PROJECT_ID.fullmatch(project_id):
            raise HTTPException(404, 'Unknown project')
        result = get_dashboard().detail(project_id, port=request.url.port or 7000)
        if result is None:
            raise HTTPException(404, 'Unknown project')
        return result

    return router
