import json
import subprocess
import sys


def test_scm_plan_only_and_least_privilege(tmp_path):
    from src.api.scm_plan import service_plan

    for action in ["install", "repair", "uninstall"]:
        plan = service_plan(action, tmp_path)
        assert plan["physical_changes"] is False
        assert plan["identity"] == r"NT SERVICE\TesterMarketLab"
        assert plan["host_kind"] == "native_pywin32_ServiceFramework"
        assert plan["restart_policy"]["max_restarts"] == 3
        assert plan["delayed_auto_start"] is True
        assert all("password" not in str(step).lower() for step in plan["operations"])
    result = subprocess.run(
        [sys.executable, "-m", "src.api.scm_plan", "install", "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["physical_changes"] is False
    assert list(tmp_path.iterdir()) == []
