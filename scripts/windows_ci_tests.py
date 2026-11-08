"""Explicit GitHub-Windows-only privilege reduction for real pytest ACL checks.

No UAC, account creation, service operation or installed application path. This is
not a security sandbox for untrusted code. Only the trusted checkout's pytest is
launched. See Microsoft CreateRestrictedToken/CreateProcessAsUser documentation.
"""

import base64
import ctypes as C
import os
import re
import stat
import subprocess
import sys
import tempfile
from ctypes import wintypes as W
from pathlib import Path
from uuid import uuid4


def require_ci_workspace(env, expected_root):
    root = Path(expected_root).absolute()
    if (
        env.get("GITHUB_ACTIONS") != "true"
        or not env.get("GITHUB_WORKSPACE")
        or Path(env["GITHUB_WORKSPACE"]).absolute() != root
        or not root.is_dir()
        or root == Path(root.anchor)
    ):
        raise ValueError("Explicit matching GitHub CI checkout required")
    for path in (root, *root.parents):
        if getattr(path.lstat(), "st_file_attributes", 0) & 0x400 or path.is_symlink():
            raise ValueError("Reparse CI workspace refused")
    return root


def owned_ci_descendant(root, path):
    """Lexical checkout containment plus lstat of every existing path component.

    Recheck at each filesystem/ACL boundary. No recursive cleanup is used. This
    is fail-closed against preexisting redirection; not a concurrent-actor sandbox.
    """
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    if root not in path.parents:
        raise ValueError("Owned checkout descendant required")
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Reparse owned descendant refused")
        if item == root:
            break
    return path


def validate_pytest_args(args):
    if tuple(args[:2]) != ("-m", "pytest") or any(
        not isinstance(x, str) or "\0" in x for x in args
    ):
        raise ValueError("Only trusted Python pytest module arguments permitted")
    return list(args)


class SID_ATTRIBUTES(C.Structure):
    _fields_ = [("Sid", W.LPVOID), ("Attributes", W.DWORD)]


class STARTUPINFO(C.Structure):
    _fields_ = [
        ("cb", W.DWORD),
        ("lpReserved", W.LPWSTR),
        ("lpDesktop", W.LPWSTR),
        ("lpTitle", W.LPWSTR),
        ("dwX", W.DWORD),
        ("dwY", W.DWORD),
        ("dwXSize", W.DWORD),
        ("dwYSize", W.DWORD),
        ("dwXCountChars", W.DWORD),
        ("dwYCountChars", W.DWORD),
        ("dwFillAttribute", W.DWORD),
        ("dwFlags", W.DWORD),
        ("wShowWindow", W.WORD),
        ("cbReserved2", W.WORD),
        ("lpReserved2", W.LPVOID),
        ("hStdInput", W.HANDLE),
        ("hStdOutput", W.HANDLE),
        ("hStdError", W.HANDLE),
    ]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [
        ("hProcess", W.HANDLE),
        ("hThread", W.HANDLE),
        ("dwProcessId", W.DWORD),
        ("dwThreadId", W.DWORD),
    ]


def _api():
    if os.name != "nt":
        raise ValueError("Windows CI only")
    kernel = C.WinDLL("kernel32", use_last_error=True)
    adv = C.WinDLL("advapi32", use_last_error=True)

    def function(dll, name, args, result=W.BOOL):
        value = getattr(dll, name)
        value.argtypes = args
        value.restype = result
        return value

    function(kernel, "GetCurrentProcess", [], W.HANDLE)
    function(kernel, "CloseHandle", [W.HANDLE])
    function(kernel, "LocalFree", [W.LPVOID], W.LPVOID)
    function(kernel, "WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD)
    function(kernel, "GetExitCodeProcess", [W.HANDLE, C.POINTER(W.DWORD)])
    function(adv, "OpenProcessToken", [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)])
    function(adv, "GetTokenInformation", [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)])
    function(adv, "SetTokenInformation", [W.HANDLE, C.c_int, W.LPVOID, W.DWORD])
    function(adv, "ConvertStringSidToSidW", [W.LPCWSTR, C.POINTER(W.LPVOID)])
    function(adv, "ConvertSidToStringSidW", [W.LPVOID, C.POINTER(W.LPWSTR)])
    function(adv, "GetLengthSid", [W.LPVOID], W.DWORD)
    function(
        adv,
        "CreateRestrictedToken",
        [
            W.HANDLE,
            W.DWORD,
            W.DWORD,
            C.POINTER(SID_ATTRIBUTES),
            W.DWORD,
            W.LPVOID,
            W.DWORD,
            W.LPVOID,
            C.POINTER(W.HANDLE),
        ],
    )
    function(
        adv,
        "CreateProcessAsUserW",
        [
            W.HANDLE,
            W.LPCWSTR,
            W.LPWSTR,
            W.LPVOID,
            W.LPVOID,
            W.BOOL,
            W.DWORD,
            W.LPVOID,
            W.LPCWSTR,
            C.POINTER(STARTUPINFO),
            C.POINTER(PROCESS_INFORMATION),
        ],
    )
    return kernel, adv


def _ok(value):
    if not value:
        raise C.WinError(C.get_last_error())


def _user(adv, token):
    size = W.DWORD()
    adv.GetTokenInformation(token, 1, None, 0, C.byref(size))
    buffer = C.create_string_buffer(size.value)
    _ok(adv.GetTokenInformation(token, 1, buffer, size, C.byref(size)))
    return buffer, C.cast(buffer, C.POINTER(SID_ATTRIBUTES)).contents.Sid


def _sid_text(kernel, adv, sid):
    text = W.LPWSTR()
    _ok(adv.ConvertSidToStringSidW(sid, C.byref(text)))
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


def spawn_no_inherit(adv, token, command, block, root):
    """No inherited parent handles, including unrelated privileged handles."""
    startup = STARTUPINFO()
    startup.cb = C.sizeof(startup)
    # NULL inherits the elevated parent's station/desktop. Empty instead requests
    # the documented process-connection rules, without modifying any desktop ACL.
    # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessasuserw
    startup.lpDesktop = ""
    process = PROCESS_INFORMATION()
    _ok(
        adv.CreateProcessAsUserW(
            token,
            sys.executable,
            command,
            None,
            None,
            False,
            0x400 | 0x08000000,
            block,
            str(root),
            C.byref(startup),
            C.byref(process),
        )
    )
    return process


def run_pytest(args, *, cwd, env):
    """Current identity only; disabled admin group/privileges, user default owner."""
    args = validate_pytest_args(args)
    root = require_ci_workspace(env, cwd)
    if root != Path(__file__).resolve().parents[1]:
        raise ValueError("Launcher must execute from its own checkout")
    kernel, adv = _api()
    token, restricted = W.HANDLE(), W.HANDLE()
    admin, medium = W.LPVOID(), W.LPVOID()
    process = PROCESS_INFORMATION()
    scratch = owned_ci_descendant(root, root / ".scratch")
    owned_ci_descendant(root, scratch).mkdir(exist_ok=True)
    log_name = "standard-user-ci-" + uuid4().hex + ".log"
    log_path = owned_ci_descendant(root, scratch / log_name)
    if log_path.exists():
        raise ValueError("Fresh child log required")
    try:
        # Rights requested only on a duplicate of the current process token.
        _ok(adv.OpenProcessToken(kernel.GetCurrentProcess(), 0xF01FF, C.byref(token)))
        user_buffer, user_sid = _user(adv, token)
        user_text = _sid_text(kernel, adv, user_sid)
        _ok(adv.ConvertStringSidToSidW("S-1-5-32-544", C.byref(admin)))
        disabled = SID_ATTRIBUTES(admin, 0)
        _ok(
            adv.CreateRestrictedToken(
                token, 1, 1, C.byref(disabled), 0, None, 0, None, C.byref(restricted)
            )
        )
        owner = W.LPVOID(user_sid)
        _ok(adv.SetTokenInformation(restricted, 4, C.byref(owner), C.sizeof(owner)))
        _ok(adv.ConvertStringSidToSidW("S-1-16-8192", C.byref(medium)))
        label = SID_ATTRIBUTES(medium, 0x20)
        _ok(
            adv.SetTokenInformation(
                restricted, 25, C.byref(label), C.sizeof(label) + adv.GetLengthSid(medium)
            )
        )
        # Only this explicit CI checkout receives an additional individual-user
        # Modify ACE; existing ACEs/owners are preserved, no recursive /T change.
        require_ci_workspace(env, root)
        subprocess.run(
            ["icacls.exe", str(root), "/grant", "*" + user_text + ":(OI)(CI)M"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        child_env = {k: v for k, v in env.items() if k.upper() != "PSMODULEPATH"}
        block = C.create_unicode_buffer(
            "\0".join(
                k + "=" + v for k, v in sorted(child_env.items(), key=lambda item: item[0].upper())
            )
            + "\0\0"
        )
        command = C.create_unicode_buffer(
            subprocess.list2cmdline(
                [sys.executable, str(Path(__file__).resolve()), "--child", log_name, *args[2:]]
            )
        )
        process = spawn_no_inherit(adv, restricted, command, block, root)
        if kernel.WaitForSingleObject(process.hProcess, 0xFFFFFFFF) != 0:
            raise C.WinError(C.get_last_error())
        code = W.DWORD()
        _ok(kernel.GetExitCodeProcess(process.hProcess, C.byref(code)))
        if not owned_ci_descendant(root, log_path).is_file():
            raise missing_child_log_error(code.value)
        print(owned_ci_descendant(root, log_path).read_text(encoding="utf-8"), end="", flush=True)
        if code.value:
            raise subprocess.CalledProcessError(code.value, [sys.executable, *args])
    finally:
        for handle in [process.hThread, process.hProcess, restricted, token]:
            if handle:
                kernel.CloseHandle(handle)
        for sid in (admin, medium):
            if sid:
                kernel.LocalFree(sid)


def _powershell(script, *, root, paths):
    for path in paths:
        owned_ci_descendant(root, path)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)


def missing_child_log_error(exit_code):
    return RuntimeError(
        "Restricted CI child did not produce its guarded log; "
        f"native exit code {exit_code} (0x{exit_code:08X})"
    )


def initialize_child_descriptors(log):
    """Called only after the guarded log exists, inside logged exception handling."""
    null_fd = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(null_fd, descriptor)
    if null_fd > 2:
        os.close(null_fd)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)


def child_main(args, log_name):
    """Open our own guarded log; no parent handles crossed the token boundary."""
    root = require_ci_workspace(os.environ, Path(__file__).resolve().parents[1])
    if not re.fullmatch(r"standard-user-ci-[0-9a-f]{32}\.log", log_name):
        raise ValueError("Fresh bounded child log name required")
    scratch = owned_ci_descendant(root, root / ".scratch")
    owned_ci_descendant(root, scratch).mkdir(exist_ok=True)
    log_path = owned_ci_descendant(root, scratch / log_name)
    # Open before any descriptor setup so bootstrap failures are observable.
    with owned_ci_descendant(root, log_path).open("x", encoding="utf-8", buffering=1) as original:
        original.write("CI child bootstrap: guarded log opened\n")
        original.flush()
        exit_code = 1
        log = original
        reserved = []
        try:
            # The first file descriptor may itself be 0/1/2 with no inherited
            # handles. Keep the diagnostic stream above those slots before dup2.
            descriptor = os.dup(original.fileno())
            reserved.append(descriptor)
            while descriptor < 3:
                descriptor = os.dup(original.fileno())
                reserved.append(descriptor)
            log = os.fdopen(reserved.pop(), "w", encoding="utf-8", buffering=1)
            sys.stdout = log
            sys.stderr = log
            initialize_child_descriptors(log)
            exit_code = _child_proof_and_pytest(args, root, scratch)
        except BaseException:
            import traceback

            traceback.print_exc(file=log)
        finally:
            log.flush()
            if log is not original:
                log.close()
            for descriptor in reserved:
                os.close(descriptor)
            # Initial Python streams may be None in a CREATE_NO_WINDOW child.
            sys.stdout = None
            sys.stderr = None
    return exit_code


def _child_proof_and_pytest(args, root, scratch):
    """Refuse tests until actual OS membership, owner and RX denial pass."""
    owned_ci_descendant(root, scratch)
    directory = Path(tempfile.mkdtemp(prefix="standard-user-proof-", dir=scratch))
    owned_ci_descendant(root, directory)
    probe = owned_ci_descendant(root, directory / "probe.txt")
    owned_ci_descendant(root, probe).write_text("unchanged", encoding="utf-8")
    literal = str(probe).replace("'", "''")
    folder = str(directory).replace("'", "''")
    prefix = (
        "$ErrorActionPreference='Stop';$Me=[Security.Principal.WindowsIdentity]::GetCurrent();"
        "$Principal=[Security.Principal.WindowsPrincipal]::new($Me);"
        "if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'CI child is still administrator'};"
        f"$File='{literal}';$Directory='{folder}';"
        "foreach ($Path in @($File,$Directory)) {"
        "$Owner=(Get-Acl -LiteralPath $Path).GetOwner([Security.Principal.SecurityIdentifier]).Value;"
        "if ($Owner -ne $Me.User.Value) {throw 'CI child default owner is not individual user'}};"
        "$Acl=Get-Acl -LiteralPath $File;$Acl.SetAccessRuleProtection($true,$false);"
        "foreach ($Rule in @($Acl.Access)) {$null=$Acl.RemoveAccessRuleSpecific($Rule)};"
    )
    try:
        _powershell(
            prefix
            + "$Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($Me.User,'ReadAndExecute','Allow'));Set-Acl -LiteralPath $File -AclObject $Acl",
            root=root,
            paths=(directory, probe),
        )
        try:
            owned_ci_descendant(root, probe).write_text("must-not-write", encoding="utf-8")
        except PermissionError:
            pass
        else:
            raise RuntimeError("CI child bypassed actual read-only DACL")
    finally:
        _powershell(
            prefix
            + "$Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($Me.User,'Modify','Allow'));Set-Acl -LiteralPath $File -AclObject $Acl",
            root=root,
            paths=(directory, probe),
        )
        owned_ci_descendant(root, probe).unlink()
        owned_ci_descendant(root, directory).rmdir()
    print(
        "PASS: actual standard-user CI token, individual file/directory owner and RX write denial",
        flush=True,
    )
    sys.path.insert(0, str(root))
    import pytest

    return int(pytest.main(args))


if __name__ == "__main__":
    if sys.argv[1:2] != ["--child"] or len(sys.argv) < 3:
        raise SystemExit("Use canonical acceptance CI launcher")
    raise SystemExit(child_main(sys.argv[3:], sys.argv[2]))
