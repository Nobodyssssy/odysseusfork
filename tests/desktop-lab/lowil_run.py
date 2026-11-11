"""Run a command as a LOW-integrity Windows process (no admin needed).

A Low-integrity process cannot write to any object labelled Medium or higher (the default for everything you own),
whatever the program inside it does. It is an OS-enforced write boundary: only folders explicitly labelled Low
are writable. It is NOT a read boundary and NOT a network boundary: the process can still read what your account can
read. Use it with synthetic data and no real credentials.

    python lowil_run.py [--cwd DIR] [--env K=V ...] [--log FILE] [--wait] -- program args...
    python lowil_run.py --label DIR        # make DIR (recursively) writable for Low-integrity processes
    python lowil_run.py --check            # print this process's integrity level

The child is placed in a job object that kills it when this launcher exits (unless --detach).
"""
import argparse
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
a32 = ctypes.WinDLL('advapi32', use_last_error=True)

TOKEN_ALL_NEEDED = 0x0001 | 0x0002 | 0x0008 | 0x0020 | 0x0080  # ASSIGN_PRIMARY|DUPLICATE|QUERY|ADJUST_DEFAULT
TokenIntegrityLevel = 25
SE_GROUP_INTEGRITY = 0x20
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x4
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
HANDLE_FLAG_INHERIT = 1
STARTF_USESTDHANDLES = 0x100


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Sid', wintypes.LPVOID), ('Attributes', wintypes.DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [('Label', SID_AND_ATTRIBUTES)]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
                ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD), ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD),
                ('dwXCountChars', wintypes.DWORD), ('dwYCountChars', wintypes.DWORD), ('dwFillAttribute', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                ('lpReserved2', wintypes.LPVOID), ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE), ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('nLength', wintypes.DWORD), ('lpSecurityDescriptor', wintypes.LPVOID), ('bInheritHandle', wintypes.BOOL)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rb', 'wb', 'ob')]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', IO_COUNTERS), ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]



# 64-bit safe signatures: without these ctypes truncates handles to 32 bits.
H = wintypes.HANDLE
k32.GetCurrentProcess.restype = H
k32.GetCurrentProcess.argtypes = []
a32.OpenProcessToken.argtypes = [H, wintypes.DWORD, ctypes.POINTER(H)]
a32.CreateRestrictedToken.argtypes = [H, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(H)]
a32.DuplicateTokenEx.argtypes = [H, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(H)]
a32.SetTokenInformation.argtypes = [H, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
a32.GetTokenInformation.argtypes = [H, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
k32.CreateJobObjectW.restype = H
k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
k32.SetInformationJobObject.argtypes = [H, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
k32.AssignProcessToJobObject.argtypes = [H, H]
k32.ResumeThread.argtypes = [H]
k32.WaitForSingleObject.argtypes = [H, wintypes.DWORD]
k32.GetExitCodeProcess.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
k32.CreateFileW.restype = H
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, H]
a32.CreateProcessAsUserW.argtypes = [H, wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.LPCWSTR, wintypes.LPVOID, wintypes.LPVOID]


def _check(ok, what):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


def low_token():
    token = wintypes.HANDLE()
    _check(a32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_ALL_NEEDED, ctypes.byref(token)), 'OpenProcessToken')
    dup = wintypes.HANDLE()
    _check(a32.DuplicateTokenEx(token, 0xF01FF, None, 2, 1, ctypes.byref(dup)), 'DuplicateTokenEx')   # SecurityImpersonation, TokenPrimary
    sid = wintypes.LPVOID()
    _check(a32.ConvertStringSidToSidW('S-1-16-4096', ctypes.byref(sid)), 'ConvertStringSidToSid')     # Low mandatory level
    label = TOKEN_MANDATORY_LABEL(SID_AND_ATTRIBUTES(sid, SE_GROUP_INTEGRITY))
    _check(a32.SetTokenInformation(dup, TokenIntegrityLevel, ctypes.byref(label), ctypes.sizeof(label) + a32.GetLengthSid(sid)), 'SetTokenInformation')
    return dup


KEEP = []
RESTRICT_SIDS = ['S-1-5-12']   # the RESTRICTED SID: only folders that explicitly grant it are writable (set with --restrict)


def write_restricted_token():
    """Same user, same integrity, but every WRITE must also pass a second access check against RESTRICT_SIDS.
    Normal files grant nothing to those SIDs, so writes fail; reads, named objects and processes still work (MSYS bash runs)."""
    token = wintypes.HANDLE()
    _check(a32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_ALL_NEEDED, ctypes.byref(token)), 'OpenProcessToken')
    sids = []
    for text in RESTRICT_SIDS:
        sid = wintypes.LPVOID()
        if text == 'logon':
            size = wintypes.DWORD()
            a32.GetTokenInformation(token, 28, None, 0, ctypes.byref(size))                              # TokenLogonSid
            raw = ctypes.create_string_buffer(size.value)
            _check(a32.GetTokenInformation(token, 28, raw, size, ctypes.byref(size)), 'GetTokenInformation(LogonSid)')
            count = ctypes.cast(raw, ctypes.POINTER(wintypes.DWORD))[0]
            first = ctypes.cast(ctypes.addressof(raw) + 8, ctypes.POINTER(SID_AND_ATTRIBUTES)).contents          # x64: 4-byte count + 4 pad
            sid = wintypes.LPVOID(first.Sid)
            KEEP.append(raw)
        else:
            _check(a32.ConvertStringSidToSidW(text, ctypes.byref(sid)), 'ConvertStringSidToSid')
        sids.append(SID_AND_ATTRIBUTES(sid, 0))
    array = (SID_AND_ATTRIBUTES * len(sids))(*sids)
    out = wintypes.HANDLE()
    _check(a32.CreateRestrictedToken(token, int(os.environ.get('ODY_TOKEN_FLAGS', '9'), 0), 0, None, 0, None, len(sids), array, ctypes.byref(out)), 'CreateRestrictedToken')   # DISABLE_MAX_PRIVILEGE | WRITE_RESTRICTED
    set_default_dacl(out)
    return out


class TOKEN_DEFAULT_DACL(ctypes.Structure):
    _fields_ = [('DefaultDacl', wintypes.LPVOID)]


a32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID]
a32.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]


def set_default_dacl(token):
    """A restricted token's default DACL must also grant the restricting SIDs, otherwise the new process cannot open its own
    objects and dies in loader init (STATUS_DLL_INIT_FAILED, 0xC0000142)."""
    user = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True).stdout.strip().split(',')[1].strip('"')
    sddl = f'D:(A;;GA;;;S-1-5-12)(A;;GA;;;SY)(A;;GA;;;{user})'
    sd = wintypes.LPVOID()
    _check(a32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(sd), None), 'ConvertStringSecurityDescriptor')
    present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
    _check(a32.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)), 'GetSecurityDescriptorDacl')
    info = TOKEN_DEFAULT_DACL(dacl)
    _check(a32.SetTokenInformation(token, 6, ctypes.byref(info), ctypes.sizeof(info)), 'SetTokenInformation(DefaultDacl)')
    KEEP.append(sd)


def integrity_level():
    token = wintypes.HANDLE()
    _check(a32.OpenProcessToken(k32.GetCurrentProcess(), 0x8, ctypes.byref(token)), 'OpenProcessToken')
    size = wintypes.DWORD()
    a32.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    _check(a32.GetTokenInformation(token, TokenIntegrityLevel, buffer, size, ctypes.byref(size)), 'GetTokenInformation')
    label = ctypes.cast(buffer, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
    count = a32.GetSidSubAuthorityCount(label.Label.Sid)
    rid = a32.GetSidSubAuthority(label.Label.Sid, ctypes.cast(count, ctypes.POINTER(ctypes.c_ubyte))[0] - 1)
    value = ctypes.cast(rid, ctypes.POINTER(wintypes.DWORD))[0]
    return {0x1000: 'Low', 0x2000: 'Medium', 0x3000: 'High', 0x4000: 'System'}.get(value, hex(value))


a32.GetSidSubAuthorityCount.restype = wintypes.LPVOID
a32.GetSidSubAuthority.restype = wintypes.LPVOID
a32.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
a32.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
a32.GetLengthSid.argtypes = [wintypes.LPVOID]
a32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID)]


def make_job(kill_on_close):
    job = k32.CreateJobObjectW(None, None)
    _check(job, 'CreateJobObject')
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000 if kill_on_close else 0                             # KILL_ON_JOB_CLOSE
    _check(k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)), 'SetInformationJobObject')
    return job


def run(command, cwd, env, log, wait, detach, mode='low'):
    token = low_token() if mode == 'low' else write_restricted_token()
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    handle = None
    if log:
        handle = k32.CreateFileW(log, 0x00000004 | 0x00100000, 0x3, ctypes.byref(sa), 4, 0x80, None)   # FILE_APPEND_DATA|SYNCHRONIZE, OPEN_ALWAYS
    else:
        handle = k32.CreateFileW('NUL', 0x40000000, 0x3, ctypes.byref(sa), 3, 0x80, None)
    _check(handle not in (None, 0, ctypes.c_void_p(-1).value), 'CreateFile(log)')
    nul_in = k32.CreateFileW('NUL', 0x80000000, 0x3, ctypes.byref(sa), 3, 0x80, None)
    startup = STARTUPINFO()
    startup.cb = ctypes.sizeof(startup)
    startup.dwFlags = STARTF_USESTDHANDLES
    startup.hStdInput, startup.hStdOutput, startup.hStdError = nul_in, handle, handle
    info = PROCESS_INFORMATION()
    block = '\0'.join(f'{k}={v}' for k, v in env.items()) + '\0\0'
    command_line = subprocess.list2cmdline(command)
    ok = a32.CreateProcessAsUserW(token, None, ctypes.create_unicode_buffer(command_line), None, None, True,
                                  CREATE_NO_WINDOW | CREATE_SUSPENDED | 0x400, ctypes.create_unicode_buffer(block), cwd, ctypes.byref(startup), ctypes.byref(info))
    _check(ok, 'CreateProcessAsUser')
    job = make_job(not detach)
    k32.AssignProcessToJobObject(job, info.hProcess)
    k32.ResumeThread(info.hThread)
    print(f'pid={info.dwProcessId} mode={mode} job_kill_on_close={not detach}', flush=True)
    if wait:
        k32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    if not detach:
        k32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cwd')
    parser.add_argument('--env', action='append', default=[])
    parser.add_argument('--log')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--detach', action='store_true', help='leave the child running after this launcher exits')
    parser.add_argument('--mode', choices=('low', 'restricted'), default='low')
    parser.add_argument('--restrict', action='append', help='restricting SID (repeat). "logon" = this logon session SID')
    parser.add_argument('--grant', metavar='DIR', help='let write-restricted processes write under DIR (grants S-1-5-12 modify)')
    parser.add_argument('--label', metavar='DIR')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.check:
        print(integrity_level())
        return 0
    if args.grant:
        target = os.path.abspath(args.grant)
        result = subprocess.run(['icacls', target, '/grant', '*S-1-5-12:(OI)(CI)(M)'], capture_output=True, text=True)
        print((result.stdout + result.stderr).strip()[-160:])
        return result.returncode
    if args.label:
        target = os.path.abspath(args.label)
        if not os.path.isdir(target):
            sys.exit(f'not a folder: {target}')
        user = os.environ.get('USERNAME', '')
        subprocess.run(['icacls', target, '/grant', f'{user}:(OI)(CI)(WO)'], capture_output=True, text=True)   # WRITE_OWNER on the lab folder only: relabeling needs it
        result = subprocess.run(['icacls', target, '/setintegritylevel', '(OI)(CI)L'], capture_output=True, text=True)
        print((result.stdout + result.stderr).strip()[-200:])
        return result.returncode
    if args.restrict:
        RESTRICT_SIDS[:] = args.restrict
    command = [part for part in args.command if part != '--'] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('no command')
    env = dict(os.environ)
    for item in args.env:
        key, _, value = item.partition('=')
        env[key] = value
    return run(command, args.cwd or os.getcwd(), env, args.log, args.wait or not args.detach, args.detach, args.mode)


if __name__ == '__main__':
    sys.exit(main())
