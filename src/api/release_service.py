"""Release lifecycle on the existing native SCM backend and supervisor host."""

import subprocess
from pathlib import Path

from .lab_scm import ACCOUNT, NativeSCM, owned
from .lab_service import LabService
from .release_install import lifecycle_lock, load_marker


def plans(marker):
    root = Path(marker["root"])
    common = dict(
        root=str(root),
        installation_id=marker["installation_id"],
        source_sha=marker["source_sha"],
        account=ACCOUNT,
        auto_start=True,
        recovery_delays_ms=[1000, 5000, 0],
    )
    app = dict(
        common,
        name=marker["services"]["app"],
        dependency=marker["services"]["postgres"],
        host=str(root / "dependencies/service/pythonservice.exe"),
        executable=str(root / "dependencies/service/pythonservice.exe"),
        python_class=str(root / "dependencies/service/release_service_bootstrap")
        + ".ReleaseService",
        release=str(root / "releases" / marker["source_sha"]),
        python=str(root / "dependencies/venv/Scripts/python.exe"),
    )
    pg = dict(
        common,
        name=marker["services"]["postgres"],
        executable=str(root / "dependencies/postgres/bin/pg_ctl.exe"),
    )
    pg["image"] = subprocess.list2cmdline(
        [
            str(root / "dependencies/postgres/bin/pg_ctl.exe"),
            "runservice",
            "-N",
            pg["name"],
            "-D",
            str(root / "postgres-data"),
            "-w",
        ]
    )
    return app, pg


def validate_resources(marker, scm=None):
    local = scm is None
    scm = NativeSCM() if local else scm
    try:
        result = []
        for plan in plans(marker):
            image = plan.get("image", subprocess.list2cmdline([plan.get("host", "")]))
            current = scm.inspect(plan["name"])
            owned(current, plan, image)
            result.append(current)
        return result
    finally:
        if local:
            scm.close()


def stop_and_prove_exit(marker):
    with NativeController(marker) as controller:
        return controller.apply("stop")


def remove_resources(marker):
    with NativeController(marker) as controller:
        return controller.apply("remove")


class NativeController:
    def __init__(self, marker):
        self.marker = marker
        self.scm = NativeSCM(mutating=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.scm.close()

    def apply(self, action):
        app, pg = plans(self.marker)
        current = validate_resources(self.marker, self.scm)
        if action == "install":
            for plan, state in zip((pg, app), reversed(current), strict=True):
                if state is None:
                    self.scm.install(
                        plan,
                        plan.get("image", subprocess.list2cmdline([plan.get("host", "")])),
                        plan.get("dependency"),
                    )
        elif action in {"stop", "restart", "remove"}:
            for plan, state in zip((app, pg), current, strict=True):
                if state is not None:
                    self.scm.stop(plan["name"])
                    self.scm.wait(plan["name"], "stopped")
            if action == "remove":
                for plan, state in zip((app, pg), current, strict=True):
                    if state is not None:
                        self.scm.remove(plan["name"])
        elif action not in {"start", "status"}:
            raise ValueError("Unknown lifecycle action")
        if action in {"start", "restart"}:
            preflight(Path(self.marker["root"]))
            for plan in (pg, app):
                self.scm.start(plan["name"])
                self.scm.wait(plan["name"], "running")
        return validate_resources(self.marker, self.scm)


def preflight(root):
    marker = load_marker(root)
    import json

    from .lab_install import verify_tree

    config = json.loads((root / "config.json").read_text())
    if config["host"] != "127.0.0.1" or config["port"] != marker["ports"]["api"]:
        raise ValueError("Release API binding mismatch")
    for name in ("service", "postgres"):
        receipt = json.loads((root / "state" / f"{name}-runtime.json").read_text())
        verify_tree(root / "dependencies" / name, receipt["files"])
    return marker


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("install", "start", "stop", "restart", "status", "remove")
    )
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    marker = load_marker(args.root)
    with lifecycle_lock(args.root), NativeController(marker) as controller:
        print(controller.apply(args.action))


if __name__ == "__main__":
    main()


class ReleaseService(LabService):
    marker_reader = staticmethod(load_marker)

    def service_preflight(self, root):
        return preflight(root)

    def health_port(self, marker):
        return marker["ports"]["api"]

    def health_ready(self, response, health, instance):
        return (
            response.status in (200, 503)
            and health.get("instance_id") == instance
            and all(health.get(key) == "ok" for key in ("database", "worker", "storage"))
        )

    @staticmethod
    def contract_builder(root):
        marker = load_marker(root)
        import os

        env = {
            k: v for k, v in os.environ.items() if not k.upper().startswith(("TESTER_", "PYTHON"))
        }
        env.update(
            TESTER_CONFIG_FILE=str(root / "config.json"),
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
            GUARDIANS_ENABLED="false",
        )
        return dict(
            argv=[str(root / "dependencies/venv/Scripts/python.exe"), "-m", "src.api.supervisor"],
            cwd=str(root / "releases" / marker["source_sha"]),
            env=env,
            sha=marker["source_sha"],
        )


if __name__ == "__main__":
    main()
