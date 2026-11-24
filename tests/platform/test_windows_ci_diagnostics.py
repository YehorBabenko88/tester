"""Read-only diagnostic policy; no actual Windows native API calls."""

import pytest

from scripts.windows_ci_diagnostics import diagnose, record


def test_native_diagnostics_refuse_non_ci_scope(tmp_path):
    with pytest.raises(ValueError, match="GitHub"):
        diagnose(None, None, None, None, env={}, root=tmp_path)


def test_diagnostic_error_does_not_echo_exception_secrets():
    def failed():
        raise OSError(5, "secret-capability-must-not-be-printed")

    result = record(failed)
    assert result["error_type"] == "OSError"
    assert "secret-capability" not in str(result)
    assert result["errno"] == 5
