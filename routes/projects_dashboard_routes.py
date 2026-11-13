"""Projects dashboard API: read-only, local browser administrator only (same gate as the provider catalog)."""
import re

from fastapi import APIRouter, HTTPException, Request, Response

from pydantic import BaseModel, Field

from src.application_modes import require_browser_admin, require_local
from src.projects_grants import GrantError


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


class Preview(BaseModel):
    action: str = Field(max_length=10)
    path: str = Field(max_length=600)
    project_id: str = Field(default='', max_length=40)


class Apply(BaseModel):
    confirmation: str = Field(max_length=200)
    typed_name: str = Field(default='', max_length=300)


def make_projects_dashboard_router(get_dashboard, guard=_reader, get_grants=None, write_guard=require_browser_admin):
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

    if get_grants is not None:
        def user_of(request):
            return str(getattr(request.state, 'current_user', None) or 'admin')

        def refused(error):
            return HTTPException(error.status, str(error))

        @router.post('/grants/preview')
        def grants_preview(body: Preview, request: Request, response: Response):
            write_guard(request)
            _private(response)
            if body.project_id and not PROJECT_ID.fullmatch(body.project_id):
                raise HTTPException(404, 'Unknown project')
            try:
                return get_grants().preview(body.action, body.path, body.project_id, user_of(request))
            except GrantError as error:
                raise refused(error)

        @router.post('/grants/apply')
        def grants_apply(body: Apply, request: Request, response: Response):
            write_guard(request)
            _private(response)
            try:
                return get_grants().apply(body.confirmation, body.typed_name, user_of(request), remote=request.client.host if request.client else None)
            except GrantError as error:
                raise refused(error)

        @router.get('/grants/audit')
        def grants_audit(request: Request, response: Response, limit: int = 20):
            guard(request)
            _private(response)
            return {'audit': get_grants().audit(limit)}

    return router
