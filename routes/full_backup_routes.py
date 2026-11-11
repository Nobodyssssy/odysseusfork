"""Full backup (Odysseus + Hermes) from the admin page: create, list and verify. Restore is offline, from a terminal."""
import asyncio
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src import hermes_backup as backup
from src.application_modes import require_browser_admin, require_local

_job = {'running': False, 'started': None, 'finished': None, 'error': None, 'result': None}
_lock = threading.Lock()


def _roots():
    roots = backup.default_roots()
    if roots['hermes'] is not None and not roots['hermes'].is_dir():
        roots['hermes'] = None
    return roots


def _run():
    try:
        destination, report = backup.create_full_backup()
        outcome = {'name': destination.name, 'files': report['files'], 'bytes': report['bytes'], 'databases': report['sqlite_backups']}
        error = None
    except Exception as exc:                     # noqa: BLE001 - reported to the page, never raised into the loop
        outcome, error = None, str(exc)[:300] or exc.__class__.__name__
    with _lock:
        _job.update(running=False, finished=time.time(), error=error, result=outcome)


def setup_full_backup_routes() -> APIRouter:
    router = APIRouter(prefix='/api/backup/full', tags=['backup'])

    @router.get('')
    def status(request: Request):
        require_local(request)
        require_admin(request)
        roots = _roots()
        with _lock:
            job = dict(_job)
        return {'backup_dir': str(roots['backup_dir']), 'includes_hermes': roots['hermes'] is not None,
                'backups': backup.list_backups(roots['backup_dir'], limit=10), 'job': job,
                'restore_help': 'python scripts\\odysseus-backup.py restore <snapshot name>   (dry run; add --apply with everything stopped)'}

    @router.post('')
    def create(request: Request):
        require_browser_admin(request)
        with _lock:
            if _job['running']:
                raise HTTPException(409, 'A backup is already running.')
            _job.update(running=True, started=time.time(), finished=None, error=None, result=None)
        threading.Thread(target=_run, name='full-backup', daemon=True).start()
        return {'state': 'running'}

    @router.post('/verify')
    async def verify(request: Request):
        require_browser_admin(request)
        try:
            name = (await request.json()).get('name')
        except Exception as exc:                  # noqa: BLE001
            raise HTTPException(400, 'Expected {"name": "<snapshot>"}') from exc
        try:
            path = backup.snapshot_path(_roots()['backup_dir'], name)
            return await asyncio.to_thread(backup.verify_backup, path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
