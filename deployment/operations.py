"""Audited maintenance of the active develop release, never a second writer."""

import argparse
import base64
import json
from pathlib import Path

from .manager import Deployment, Docker, MaintenanceStillRunning, load_site, locked, save

INVENTORY = """
from pathlib import Path
import json,shutil,os
from deltalake import DeltaTable
import pyarrow as pa
root=Path('/lake'); controls=root/'control'
data={'disk':shutil.disk_usage(root)._asdict(),'controls':[], 'tables':[], 'feeds':[]}
for p in sorted(controls.glob('*.sqlite*')):
 if p.is_file():data['controls'].append({'name':p.name,'bytes':p.stat().st_size})
for p in sorted((root/'silver').glob('*/*')):
 if (p/'_delta_log').is_dir():
  t=DeltaTable(str(p)); adds=pa.table(t.get_add_actions())
  data['tables'].append({'name':str(p.relative_to(root)), 'version':t.version(),
   'rows':sum(adds['num_records'].to_pylist()),'files':adds.num_rows})
for p in sorted((controls/'supervision').glob('*.json')):
 v=json.loads(p.read_text());data['feeds'].append({'name':p.name,'observed_at':v['observed_at'],
  'control_id':v['control_id'],'issues':len(v['issues']),'stats':v.get('stats')})
print(json.dumps(data))
"""


def inspect(docker, record):
    code = (
        "import base64;exec(base64.b64decode('"
        + base64.b64encode(INVENTORY.encode()).decode()
        + "'))"
    )
    return json.loads(docker.call("exec", record["container"], "python", "-c", code, timeout=120))


def execute(site, operation, report_path, *, remove_backup=False):
    docker = Docker(site["docker_context"])
    deployment = Deployment(site, "develop", docker)
    report = {"operation": operation, "status": "STARTED", "controls": []}
    with locked(deployment.root / "deployment.lock"):
        active = deployment.active()
        if not active or active["channel"] != "develop":
            raise RuntimeError("No active develop release")
        report["release"] = {k: active[k] for k in ("commit", "container", "volume", "image")}
        report["before"] = inspect(docker, active)
        save(report_path, report)
        if operation == "inspect":
            report["status"] = "INSPECTED"
            save(report_path, report)
            return report
        if active["manifest"].get("control_payload_format", 1) < 2:
            raise RuntimeError("Deploy a tested format-2 image before compacting controls")
        if (
            operation in {"trial", "reformulate", "rebuild-index"}
            and active["manifest"].get("control_payload_format", 1) < 3
        ):
            raise RuntimeError("Deploy a tested format-3 image before reformulating controls")
        safe_to_resume = True
        try:
            docker.drain(active, site["drain_seconds"])
            checked = docker.helper(active, ["check"])
            trial_volume = docker.create_control_trial(active) if operation == "trial" else None
            if trial_volume:
                report["trial_volume"] = trial_volume
                save(report_path, report)
            for family in checked["versions"]:
                if operation == "rebuild-index" and family not in {"LISTA_RECH", "LISTA_DEFECTOS"}:
                    continue
                filename = (
                    "control.sqlite"
                    if family == "SIP_MODELO"
                    else "control-" + family.lower() + ".sqlite"
                )
                if filename not in {r["name"] for r in report["before"]["controls"]}:
                    continue
                if operation == "trial":
                    result = docker.helper(
                        active,
                        ["trial-control", "--family", family, "--trial-root", "/trial"],
                        trial_volume=trial_volume,
                        timeout=3600,
                    )
                    report["controls"].append({"family": family, **result})
                    save(report_path, report)
                    continue
                if operation == "rebuild-index":
                    result = docker.helper(
                        active, ["rebuild-index", "--family", family], readonly=False, timeout=3600
                    )
                    report["controls"].append({"family": family, **result})
                    save(report_path, report)
                    continue
                command = ["compact-control", "--family", family, "--apply"]
                if operation == "reformulate":
                    command.append("--reformulate")
                if remove_backup:
                    command.append("--remove-backup")
                result = docker.helper(active, command, readonly=False, timeout=3600)
                report["controls"].append({"family": family, **result})
                save(report_path, report)
            report["verification"] = docker.helper(active, ["check"])
            report["status"] = {
                "trial": "TRIAL_VERIFIED",
                "reformulate": "REFORMULATED",
                "rebuild-index": "INDEX_REBUILT",
            }.get(operation, "COMPACTED")
        except Exception as exc:
            safe_to_resume = not isinstance(exc, MaintenanceStillRunning)
            report.update(status="FAILED", error=str(exc))
            raise
        finally:
            # The same tested image reads both text and compact controls. Never
            # reactivate an older image, and never force-kill an in-flight batch.
            if safe_to_resume:
                docker.resume(active)
                report["after"] = inspect(docker, active)
            else:
                report["recovery_required"] = "Confirm isolated helper termination before resuming"
            save(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument(
        "--operation",
        choices=["inspect", "compact", "trial", "reformulate", "rebuild-index"],
        required=True,
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--remove-backup", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            execute(
                load_site(args.site), args.operation, args.report, remove_backup=args.remove_backup
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
