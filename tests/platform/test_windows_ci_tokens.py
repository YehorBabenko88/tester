"""Restricted-copy default DACL policy; no actual native token APIs."""

from copy import deepcopy

import pytest

from scripts.windows_ci_tokens import replacement_default_dacl


def before():
    return dict(
        null=False,
        sddl="D:(A;;GA;;;BA)(A;;GA;;;SY)(A;;GXGR;;;S-1-5-5-0-42)",
        entries=[
            dict(type=0, flags=0, mask=0x10000000, sid="S-1-5-32-544"),
            dict(type=0, flags=0, mask=0x10000000, sid="S-1-5-18"),
            dict(type=0, flags=0, mask=0xA0000000, sid="S-1-5-5-0-42"),
        ],
    )


def test_only_measured_identical_ba_system_defaults_admit_copy_repair():
    user = "S-1-5-21-1-2-3-500"
    assert (
        replacement_default_dacl(before(), before(), user, "S-1-5-5-0-42")
        == f"D:(A;;GA;;;{user})(A;;GA;;;SY)(A;;GXGR;;;S-1-5-5-0-42)"
    )


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
        replacement_default_dacl(original, restricted, "S-1-5-21-1-2-3-500", "S-1-5-5-0-42")


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


def test_logon_sid_is_measured_from_both_enabled_token_groups():
    from scripts.windows_ci_tokens import measured_logon_sid

    profile = dict(admin_and_logon_groups=[dict(sid="S-1-5-5-0-42", attributes=0xC0000007)])
    assert measured_logon_sid(profile, deepcopy(profile)) == "S-1-5-5-0-42"
    for attrs in [0xC0000003, 0xC0000017, 7]:
        wrong = deepcopy(profile)
        wrong["admin_and_logon_groups"][0]["attributes"] = attrs
        with pytest.raises(ValueError):
            measured_logon_sid(profile, wrong)
    other = deepcopy(profile)
    other["admin_and_logon_groups"][0]["sid"] = "S-1-5-5-0-43"
    with pytest.raises(ValueError):
        measured_logon_sid(profile, other)


@pytest.mark.parametrize("change", ["missing", "extra", "write", "different", "deny", "inherit"])
def test_logon_ace_must_be_preserved_without_widening(change):
    original = before()
    wrong = deepcopy(original)
    if change == "missing":
        wrong["entries"].pop()
    elif change == "extra":
        wrong["entries"].append(dict(type=0, flags=0, mask=0xA0000000, sid="S-1-1-0"))
    elif change == "write":
        wrong["entries"][2]["mask"] = 0x10000000
    elif change == "different":
        wrong["entries"][2]["sid"] = "S-1-5-5-0-43"
    elif change == "deny":
        wrong["entries"][2]["type"] = 1
    else:
        wrong["entries"][2]["flags"] = 2
    with pytest.raises(ValueError):
        replacement_default_dacl(wrong, deepcopy(wrong), "S-1-5-21-1-2-3-500", "S-1-5-5-0-42")


def test_after_policy_keeps_only_user_system_and_exact_logon_rx():
    from scripts.windows_ci_tokens import verify_replacement

    after = before()
    user = "S-1-5-21-1-2-3-500"
    after["entries"][0]["sid"] = user
    verify_replacement(after, user, "S-1-5-5-0-42")
    for mutate in ("widen_logon", "missing_logon", "admin_again"):
        wrong = deepcopy(after)
        if mutate == "widen_logon":
            wrong["entries"][2]["mask"] = 0x10000000
        elif mutate == "missing_logon":
            wrong["entries"].pop()
        else:
            wrong["entries"][0]["sid"] = "S-1-5-32-544"
        with pytest.raises(ValueError):
            verify_replacement(wrong, user, "S-1-5-5-0-42")
