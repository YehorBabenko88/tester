import hashlib
import io
import stat
import zipfile

import pytest

from scripts.release_ci_components import download, extract


def test_download_requires_exact_reviewed_bytes(tmp_path):
    data = b"official fixture archive"

    def opener(*args, **kwargs):
        return io.BytesIO(data)

    download(
        "https://supplier.invalid/archive",
        hashlib.sha256(data).hexdigest(),
        tmp_path / "good",
        opener=opener,
    )
    assert (tmp_path / "good").read_bytes() == data
    with pytest.raises(ValueError, match="SHA256"):
        download("https://supplier.invalid/archive", "0" * 64, tmp_path / "bad", opener=opener)
    with pytest.raises(ValueError, match="HTTPS"):
        download("http://supplier.invalid/archive", "0" * 64, tmp_path / "http", opener=opener)


@pytest.mark.parametrize("kind", ["traversal", "link", "collision"])
def test_untrusted_zip_rejected_before_extraction(tmp_path, kind):
    p = tmp_path / "archive.zip"
    with zipfile.ZipFile(p, "w") as z:
        if kind == "traversal":
            z.writestr("../escape", b"x")
        elif kind == "link":
            info = zipfile.ZipInfo("linked")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "outside")
        else:
            z.writestr("File", b"a")
            z.writestr("file", b"b")
    with pytest.raises(ValueError):
        extract(p, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_regular_zip_extracts_offline_runtime_structure(tmp_path):
    p = tmp_path / "archive.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("tools/Lib/", b"")
        z.writestr("tools/Lib/venv.py", b"fixture")
    extract(p, tmp_path / "out")
    assert (tmp_path / "out/tools/Lib/venv.py").read_bytes() == b"fixture"


def test_supplier_verifier_resets_child_powershell_module_path(monkeypatch, tmp_path):
    from scripts import release_ci_components as ci

    monkeypatch.setenv("PSModulePath", "incompatible-pwsh7-builtins")
    monkeypatch.setenv("TESTER_FIXTURE_CONTEXT", "preserved")
    seen = {}

    def checked(args, **kwargs):
        seen.update(kwargs)
        assert "Get-AuthenticodeSignature" in args[-1]
        assert "PINNED_SIGNER" in args[-1]
        return "Expected supplier\n"

    monkeypatch.setattr(ci.subprocess, "check_output", checked)
    assert ci.authenticode(tmp_path / "python.exe", "PINNED_SIGNER") == "Expected supplier"
    assert not any(k.upper() == "PSMODULEPATH" for k in seen["env"])
    assert seen["env"]["TESTER_FIXTURE_CONTEXT"] == "preserved"
