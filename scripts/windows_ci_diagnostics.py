"""Bounded read-only Windows CI station/desktop/token diagnostics, no mutators.

Matching 0xC0000142 alone does not identify the failing DLL or prove a desktop ACL
cause. Read actual context before considering fresh private CI-only objects.
"""

import ctypes as C
from ctypes import wintypes as W


class SID_ATTRIBUTES(C.Structure):
    _fields_ = [("Sid", W.LPVOID), ("Attributes", W.DWORD)]


class GROUPS(C.Structure):
    _fields_ = [("Count", W.DWORD), ("Groups", SID_ATTRIBUTES * 1)]


def record(operation):
    try:
        return operation()
    except (OSError, ValueError) as exc:
        # Native API messages may expose unrelated paths; retain numeric evidence.
        return dict(
            error_type=type(exc).__name__,
            winerror=getattr(exc, "winerror", None),
            errno=getattr(exc, "errno", None),
        )


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())


def token_info(adv, token, kind):
    size = W.DWORD()
    adv.GetTokenInformation(token, kind, None, 0, C.byref(size))
    if not 0 < size.value <= 65536:
        raise ValueError("bounded token information required")
    buffer = C.create_string_buffer(size.value)
    checked(adv.GetTokenInformation(token, kind, buffer, size, C.byref(size)))
    return buffer


def sid_text(kernel, adv, sid):
    text = W.LPWSTR()
    checked(adv.ConvertSidToStringSidW(sid, C.byref(text)))
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


def token_profile(kernel, adv, token):
    session = token_info(adv, token, 12)
    user = token_info(adv, token, 1)
    owner = token_info(adv, token, 4)
    integrity = token_info(adv, token, 25)
    groups = token_info(adv, token, 2)
    count = W.DWORD.from_buffer(groups).value
    if GROUPS.Groups.offset + count * C.sizeof(SID_ATTRIBUTES) > C.sizeof(groups):
        raise ValueError("invalid token group bounds")
    membership = []
    for item in (SID_ATTRIBUTES * count).from_buffer(groups, GROUPS.Groups.offset):
        name = sid_text(kernel, adv, item.Sid)
        if name == "S-1-5-32-544" or item.Attributes & 0xC0000000 == 0xC0000000:
            membership.append(dict(sid=name, attributes=item.Attributes))
    return dict(
        session=W.DWORD.from_buffer(session).value,
        user=sid_text(kernel, adv, C.cast(user, C.POINTER(SID_ATTRIBUTES)).contents.Sid),
        default_owner=sid_text(kernel, adv, C.cast(owner, C.POINTER(W.LPVOID)).contents.value),
        integrity=sid_text(kernel, adv, C.cast(integrity, C.POINTER(SID_ATTRIBUTES)).contents.Sid),
        admin_and_logon_groups=membership,
    )


def object_name(user, handle):
    size = W.DWORD()
    user.GetUserObjectInformationW(handle, 2, None, 0, C.byref(size))
    if not 0 < size.value <= 65536:
        raise ValueError("bounded object name required")
    buffer = C.create_unicode_buffer(size.value // C.sizeof(C.c_wchar) + 1)
    checked(user.GetUserObjectInformationW(handle, 2, buffer, C.sizeof(buffer), C.byref(size)))
    return buffer.value


def object_sddl(kernel, adv, user, handle, flags):
    information = W.DWORD(flags)
    size = W.DWORD()
    user.GetUserObjectSecurity(handle, C.byref(information), None, 0, C.byref(size))
    if not size.value:
        raise C.WinError(C.get_last_error())
    if size.value > 65536:
        raise ValueError("bounded object security required")
    buffer = C.create_string_buffer(size.value)
    checked(user.GetUserObjectSecurity(handle, C.byref(information), buffer, size, C.byref(size)))
    text = W.LPWSTR()
    checked(
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            buffer, 1, flags, C.byref(text), None
        )
    )
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


def diagnose(kernel, adv, current, restricted, *, env, root):
    from scripts.windows_ci_tests import require_ci_workspace

    require_ci_workspace(env, root)  # No native loading/calls before CI scope proof.
    user = C.WinDLL("user32", use_last_error=True)

    def function(dll, name, args, result):
        value = getattr(dll, name)
        value.argtypes = args
        value.restype = result

    function(kernel, "GetCurrentThreadId", [], W.DWORD)
    function(user, "GetProcessWindowStation", [], W.HANDLE)
    function(user, "GetThreadDesktop", [W.DWORD], W.HANDLE)
    function(
        user,
        "GetUserObjectInformationW",
        [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)],
        W.BOOL,
    )
    function(
        user,
        "GetUserObjectSecurity",
        [W.HANDLE, C.POINTER(W.DWORD), W.LPVOID, W.DWORD, C.POINTER(W.DWORD)],
        W.BOOL,
    )
    function(
        adv,
        "ConvertSecurityDescriptorToStringSecurityDescriptorW",
        [W.LPVOID, W.DWORD, W.DWORD, C.POINTER(W.LPWSTR), C.POINTER(W.DWORD)],
        W.BOOL,
    )
    context = {}
    # Borrowed handles: GetProcessWindowStation/GetThreadDesktop are never closed.
    for label, handle in [
        ("station", user.GetProcessWindowStation()),
        ("desktop", user.GetThreadDesktop(kernel.GetCurrentThreadId())),
    ]:
        context[label] = dict(
            name=record(lambda: object_name(user, handle)),
            owner_dacl=record(lambda: object_sddl(kernel, adv, user, handle, 0x5)),
            mandatory_label=record(lambda: object_sddl(kernel, adv, user, handle, 0x10)),
        )
    return dict(
        schema_version="tester.windows-ci-native-context/1",
        parent_objects=context,
        current_token=record(lambda: token_profile(kernel, adv, current)),
        restricted_token=record(lambda: token_profile(kernel, adv, restricted)),
    )
