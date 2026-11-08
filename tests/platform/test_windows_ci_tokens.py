"""Restricted-copy default DACL policy; no actual native token APIs."""

from copy import deepcopy

import pytest

from scripts.windows_ci_tokens import replacement_default_dacl


def before():
    return dict(
        null=False,
        sddl="D:(A;;GA;;;BA)(A;;GA;;;SY)",
        entries=[
            dict(type=0, flags=0, mask=0x10000000, sid="S-1-5-32-544"),
            dict(type=0, flags=0, mask=0x10000000, sid="S-1-5-18"),
        ],
    )


def test_only_measured_identical_ba_system_defaults_admit_copy_repair():
    user = "S-1-5-21-1-2-3-500"
    assert replacement_default_dacl(before(), before(), user) == f"D:(A;;GA;;;{user})(A;;GA;;;SY)"


@pytest.mark.parametrize(
    "change", ["null", "deny", "everyone", "mask", "flags", "already_user", "mismatch"]
)
def test_unexpected_default_dacl_refuses_without_guessing(change):
    original = before()
    restricted = deepcopy(original)
    if change == "null":
        restricted["null"] = True
    elif change == "mismatch":
        restricted["sddl"] = "different"
    elif change == "deny":
        restricted["entries"][0]["type"] = 1
    elif change == "everyone":
        restricted["entries"][0]["sid"] = "S-1-1-0"
    elif change == "already_user":
        restricted["entries"][0]["sid"] = "S-1-5-21-1-2-3-500"
    elif change == "mask":
        restricted["entries"][0]["mask"] = 0x00120089
    else:
        restricted["entries"][0]["flags"] = 2
    with pytest.raises(ValueError):
        replacement_default_dacl(original, restricted, "S-1-5-21-1-2-3-500")


def test_copy_setter_keeps_acl_storage_alive_and_targets_only_copy():
    import ctypes as C
    from ctypes import wintypes as W

    from scripts.windows_ci_tokens import install_copy_default_dacl

    calls = []
    storage = C.create_string_buffer(64)
    base = C.addressof(storage)
    copy = object()

    class Adv:
        def ConvertStringSecurityDescriptorToSecurityDescriptorW(self, sddl, revision, out, size):
            C.cast(out, C.POINTER(W.LPVOID)).contents.value = base
            calls.append("allocated")
            return 1

        def GetSecurityDescriptorDacl(self, descriptor, present, acl, defaulted):
            assert descriptor.value == base
            C.cast(present, C.POINTER(W.BOOL)).contents.value = True
            C.cast(acl, C.POINTER(W.LPVOID)).contents.value = base + 16
            return 1

        def SetTokenInformation(self, token, kind, value, size):
            assert token is copy
            assert kind == 6 and size == C.sizeof(W.LPVOID)
            assert C.cast(value, C.POINTER(W.LPVOID)).contents.value == base + 16
            assert calls == ["allocated"]
            calls.append("copied")
            return 1

    class Kernel:
        def LocalFree(self, descriptor):
            assert descriptor.value == base
            calls.append("freed")

    install_copy_default_dacl(Kernel(), Adv(), copy, "D:(A;;GA;;;SY)")
    assert calls == ["allocated", "copied", "freed"]
