"""Measured default-DACL repair on a newly restricted CI token copy only.

TOKEN_DEFAULT_DACL supplies protection for newly created objects. This module
never changes an existing file/process/thread ACL or the original token.
https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-token_default_dacl
"""

import ctypes as C
import re
from ctypes import wintypes as W

from scripts.windows_ci_diagnostics import checked, token_info


class SECURITY_DESCRIPTOR(C.Structure):
    _fields_ = [
        ("Revision", W.BYTE),
        ("Sbz1", W.BYTE),
        ("Control", W.WORD),
        ("Owner", W.LPVOID),
        ("Group", W.LPVOID),
        ("Sacl", W.LPVOID),
        ("Dacl", W.LPVOID),
    ]


class ACL_SIZE_INFORMATION(C.Structure):
    _fields_ = [("AceCount", W.DWORD), ("AclBytesInUse", W.DWORD), ("AclBytesFree", W.DWORD)]


class ACE_HEADER(C.Structure):
    _fields_ = [("Type", W.BYTE), ("Flags", W.BYTE), ("Size", W.WORD)]


def default_dacl(kernel, adv, token):
    from scripts.windows_ci_diagnostics import sid_text

    buffer = token_info(adv, token, 6)
    acl = C.cast(buffer, C.POINTER(W.LPVOID)).contents.value
    entries = []
    if acl:
        start = C.addressof(buffer)
        end = start + C.sizeof(buffer)
        if not start <= acl <= end - 8:
            raise ValueError("Token default ACL pointer out of bounds")
        info = ACL_SIZE_INFORMATION()
        checked(adv.GetAclInformation(acl, C.byref(info), C.sizeof(info), 2))
        if info.AceCount > 1024 or not 8 <= info.AclBytesInUse <= end - acl:
            raise ValueError("Token default ACL size out of bounds")
        for index in range(info.AceCount):
            pointer = W.LPVOID()
            checked(adv.GetAce(acl, index, C.byref(pointer)))
            address = pointer.value
            if address is None or not acl + 8 <= address <= acl + info.AclBytesInUse - 4:
                raise ValueError("Token default ACE pointer out of bounds")
            header = ACE_HEADER.from_address(address)
            if header.Size < 4 or address + header.Size > acl + info.AclBytesInUse:
                raise ValueError("Token default ACE size out of bounds")
            entry = dict(type=header.Type, flags=header.Flags)
            if header.Type == 0:
                if header.Size < 16:
                    raise ValueError("Token default allowed ACE truncated")
                entry.update(
                    mask=W.DWORD.from_address(address + 4).value,
                    sid=sid_text(kernel, adv, address + 8),
                )
            entries.append(entry)
    # Absolute descriptor in local Python memory only, not a host ACL mutation.
    descriptor = SECURITY_DESCRIPTOR(1, 0, 4, None, None, None, acl)
    text = W.LPWSTR()
    checked(
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            C.byref(descriptor), 1, 4, C.byref(text), None
        )
    )
    try:
        return dict(null=not bool(acl), sddl=text.value, entries=entries)
    finally:
        kernel.LocalFree(text)


def measured_logon_sid(original_profile, restricted_profile):
    """Resolve exactly one matching enabled, non-deny logon SID from both tokens."""
    resolved = []
    for profile in (original_profile, restricted_profile):
        groups = profile.get("admin_and_logon_groups")
        if not isinstance(groups, list):
            raise ValueError("Measured token logon groups unavailable")
        found = []
        for group in groups:
            attributes = group.get("attributes")
            sid = group.get("sid")
            if type(attributes) is not int or not isinstance(sid, str):
                raise ValueError("Invalid measured token group")
            if attributes & 0xC0000000 == 0xC0000000:
                if (
                    not attributes & 4
                    or attributes & 0x10
                    or not re.fullmatch(r"S-1-5-5-[0-9]{1,10}-[0-9]{1,10}", sid)
                ):
                    raise ValueError("Token logon SID must be enabled and non-deny")
                found.append((sid, attributes))
        if len(found) != 1:
            raise ValueError("Exactly one measured token logon SID required")
        resolved.append(found[0])
    if resolved[0] != resolved[1]:
        raise ValueError("Original and restricted token logon SID/attributes differ")
    return resolved[0][0]


def _exact_aces(summary, grants):
    expected = [dict(type=0, flags=0, mask=mask, sid=sid) for sid, mask in grants.items()]
    return (
        summary.get("null") is False
        and len(summary.get("entries", [])) == len(expected)
        and sorted(summary["entries"], key=lambda item: item.get("sid", ""))
        == sorted(expected, key=lambda item: item["sid"])
    )


def replacement_default_dacl(original, restricted, user_sid, logon_sid):
    if not re.fullmatch(r"S-1-5-21-(?:[0-9]{1,10}-){3}[0-9]{1,10}", user_sid):
        raise ValueError("Individual user SID required")
    if not re.fullmatch(r"S-1-5-5-[0-9]{1,10}-[0-9]{1,10}", logon_sid):
        raise ValueError("Measured token logon SID required")
    # Exact published CI evidence: BA GA + SYSTEM GA + that token's logon GXGR.
    # The logon SID is measured per job, never a literal runner/session SID.
    grants = {"S-1-5-32-544": 0x10000000, "S-1-5-18": 0x10000000, logon_sid: 0xA0000000}
    if original != restricted or not _exact_aces(original, grants):
        raise ValueError("Unproven default DACL hypothesis; repair refused")
    return f"D:(A;;GA;;;{user_sid})(A;;GA;;;SY)(A;;GXGR;;;{logon_sid})"


def install_copy_default_dacl(kernel, adv, restricted, sddl):
    descriptor = W.LPVOID()
    checked(
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None)
    )
    try:
        present = W.BOOL()
        defaulted = W.BOOL()
        acl = W.LPVOID()
        checked(
            adv.GetSecurityDescriptorDacl(
                descriptor, C.byref(present), C.byref(acl), C.byref(defaulted)
            )
        )
        if not present.value or not acl.value:
            raise ValueError("Explicit non-null replacement DACL required")
        # TOKEN_DEFAULT_DACL is one pointer. The backing descriptor/ACL remains
        # allocated until SetTokenInformation has synchronously copied it.
        value = W.LPVOID(acl.value)
        checked(adv.SetTokenInformation(restricted, 6, C.byref(value), C.sizeof(value)))
    finally:
        kernel.LocalFree(descriptor)


def verify_replacement(summary, user_sid, logon_sid):
    grants = {user_sid: 0x10000000, "S-1-5-18": 0x10000000, logon_sid: 0xA0000000}
    if not _exact_aces(summary, grants):
        raise ValueError(
            "Restricted default DACL must match exact user/SYSTEM and unchanged logon GXGR"
        )
