#!/usr/bin/env python3
"""Launch a fresh CLI under an inherited, OS-enforced filesystem allowlist.

The caller stages only the train pack, clean request and (optionally) auth.json
inside --sandbox-dir. This script never loads an old Codex home or config.
No model call occurs with --check (it executes only `codex --help`).

Security boundary: filesystem reads/writes + blocked host IPC/process inspection.
TCP connects are limited to port 443 by Landlock. DNS UDP remains available;
this is not a network-destination sandbox. Outer sandbox restrictions still apply.
"""
import argparse
import ctypes
import errno
import os
from pathlib import Path
import platform
import resource
import sys

CLI = '/usr/lib/chatgpt/resources/codex'
FS_ALL = (1 << 16) - 1
FS_READ = (1 << 0) | (1 << 2) | (1 << 3)
FS_FILE_READ = 1 << 2
FS_FILE_WRITE = 1 << 1

class Ruleset(ctypes.Structure):
    _fields_ = [('fs', ctypes.c_uint64), ('net', ctypes.c_uint64), ('scoped', ctypes.c_uint64)]

class PathRule(ctypes.Structure):
    _layout_ = 'ms'
    _pack_ = 1
    _fields_ = [('access', ctypes.c_uint64), ('fd', ctypes.c_int32)]

class NetRule(ctypes.Structure):
    _fields_ = [('access', ctypes.c_uint64), ('port', ctypes.c_uint64)]

class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]

class Program(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ushort), ('filters', ctypes.POINTER(Filter))]

libc = ctypes.CDLL(None, use_errno=True)

def syscall(number, *args):
    value = libc.syscall(number, *args)
    if value < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return value

def landlock(box):
    if platform.machine() != 'x86_64':
        raise RuntimeError('This reviewed launcher is x86_64-only')
    abi = syscall(444, 0, 0, 1)
    if abi < 6:
        raise RuntimeError(f'Landlock ABI >=6 required; got {abi}')
    # All known filesystem actions; TCP bind/connect denied unless allowed;
    # isolate abstract UNIX sockets and signals from unconfined host processes.
    attrs = Ruleset(FS_ALL, 3, 3)
    ruleset = syscall(444, ctypes.byref(attrs), ctypes.sizeof(attrs), 0)
    entries = [(str(box), FS_ALL)]
    entries += [(p, FS_READ) for p in ['/usr', '/bin', '/lib', '/lib64'] if Path(p).exists()]
    entries += [(p, FS_FILE_READ) for p in [
        '/etc/hosts', '/etc/resolv.conf', '/etc/nsswitch.conf', '/etc/gai.conf',
        '/etc/ssl/cert.pem', '/etc/ld.so.cache', '/dev/urandom', '/dev/random'
    ] if Path(p).exists()]
    if Path('/etc/ssl/certs').exists():
        entries.append(('/etc/ssl/certs', FS_READ))
    if Path('/dev/null').exists():
        entries.append(('/dev/null', FS_FILE_READ | FS_FILE_WRITE))
    for path, rights in entries:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = PathRule(rights, fd)
            syscall(445, ruleset, 1, ctypes.byref(rule), 0)
        finally:
            os.close(fd)
    https = NetRule(2, 443)  # LANDLOCK_ACCESS_NET_CONNECT_TCP
    syscall(445, ruleset, 2, ctypes.byref(https), 0)
    if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    syscall(446, ruleset, 0)
    os.close(ruleset)

def seccomp():
    # No pathname UNIX sockets to Docker/desktop/session brokers; no io_uring
    # socket bypass, process-memory inspection, or System V IPC. Pipes and
    # socketpair stay available for communication among confined descendants.
    deny = [101, 310, 311, 438, 425, 426, 427, 321, 298, 304,
            29, 30, 31, 64, 65, 66, 67, 68, 69, 70, 71, 220]
    ins = [(0x20, 0, 0, 4),                    # arch
           (0x15, 1, 0, 0xc000003e),           # AUDIT_ARCH_X86_64
           (0x06, 0, 0, 0x80000000),           # KILL_PROCESS
           (0x20, 0, 0, 0),                    # syscall nr
           (0x35, 0, 1, 0x40000000),           # reject x32 ABI
           (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    for number in deny:
        ins += [(0x15, 0, 1, number), (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    ins += [(0x15, 0, 6, 41),                  # socket(): allow IPv4/IPv6 only
            (0x20, 0, 0, 16),                 # args[0] domain
            (0x15, 2, 0, 2),                  # AF_INET
            (0x15, 1, 0, 10),                 # AF_INET6
            (0x06, 0, 0, 0x00050000 | errno.EPERM),
            (0x06, 0, 0, 0x7fff0000),
            (0x06, 0, 0, 0x7fff0000),
            (0x06, 0, 0, 0x7fff0000)]
    filters = (Filter * len(ins))(*(Filter(*row) for row in ins))
    program = Program(len(ins), filters)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0):  # PR_SET_SECCOMP, FILTER
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sandbox-dir', required=True)
    parser.add_argument('--stdin-file', help='Clean task file INSIDE sandbox-dir')
    parser.add_argument('--check', action='store_true', help='Run codex --help only; no model call')
    parser.add_argument('--probe', action='append', metavar='FORBIDDEN_FILE', help='Check open() denied for a path without reading contents; repeatable; no model call')
    args = parser.parse_args()
    box = Path(args.sandbox_dir).resolve(strict=True)
    if not box.is_dir() or str(box) in ['/', '/tmp', '/home', '/usr']:
        parser.error('Use a dedicated, freshly staged sandbox directory')
    for name in ['home', 'codex', 'work', 'out', 'tmp']:
        (box / name).mkdir(mode=0o700, exist_ok=True)
    task = None
    if args.stdin_file:
        task = Path(args.stdin_file).resolve(strict=True)
        if not task.is_relative_to(box):
            parser.error('--stdin-file must be inside sandbox-dir')
    if not (args.check or args.probe or task):
        parser.error('Supply a clean --stdin-file, or --check/--probe')
    if not (args.check or args.probe) and not (box / 'work' / 'output_schema.json').is_file():
        parser.error('Missing sandbox work/output_schema.json')
    env = {
        'HOME': str(box / 'home'), 'CODEX_HOME': str(box / 'codex'),
        'TMPDIR': str(box / 'tmp'), 'PATH': '/usr/bin:/bin',
        'PWD': str(box / 'work'), 'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8',
        'TZ': 'UTC', 'NO_COLOR': '1', 'TERM': 'dumb',
    }
    # Drop all inherited environment values and non-stdio descriptors before
    # establishing the Landlock domain. No file contents/credential values log.
    os.environ.clear()
    os.environ.update(env)
    if task is not None:
        fd = os.open(task, os.O_RDONLY | os.O_CLOEXEC)
        os.dup2(fd, 0)
        if fd != 0:
            os.close(fd)
    else:
        fd = os.open('/dev/null', os.O_RDONLY)
        os.dup2(fd, 0)
        if fd != 0:
            os.close(fd)
    fd_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    os.closerange(3, 1048576 if fd_limit == resource.RLIM_INFINITY else fd_limit)
    os.chdir(box / 'work')
    landlock(box)
    seccomp()
    if args.probe:
        program = '''import os,pathlib,socket,sys
allowed=pathlib.Path(sys.argv[1])
assert allowed.read_text() == "TRAIN_CANARY"
for p in [*map(pathlib.Path,sys.argv[2:]),pathlib.Path("/etc/passwd"),pathlib.Path("/home/xaeliudzyh/.codex/config.toml")]:
 try: fd=os.open(p,os.O_RDONLY)
 except PermissionError: print("DENIED",p)
 else:
  os.close(fd)
  raise AssertionError("unexpected readable path: "+str(p))
try: socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
except PermissionError: print("HOST_UNIX_SOCKET_DENIED")
else: raise AssertionError("UNIX socket unexpectedly allowed")
print("OS_ALLOWLIST_OK")
'''
        command = ['/usr/bin/python3', '-c', program, str(box / 'work' / 'train-canary.txt'), *args.probe]
    elif args.check:
        command = [CLI, '--help']
    else:
        command = [CLI, '--no-daemon', '--ask-for-approval', 'never',
                   '--sandbox', 'read-only', '--disable', 'apps', '--disable', 'plugins',
                   '--disable', 'shell_tool', '--disable', 'multi_agent', '--disable', 'memories',
                   '--disable', 'skill_search', '--disable', 'hooks', '-c', 'web_search="disabled"',
                   'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                   '--skip-git-repo-check', '--cd', str(box / 'work'), '--json',
                   '--output-schema', str(box / 'work' / 'output_schema.json'),
                   '--output-last-message', str(box / 'out' / 'author_result.json'), '-']
    os.execve(command[0], command, env)

if __name__ == '__main__':
    main()
