"""Durable, single-writer execution of authorized review commands.

Commands and receipts travel through a separate volume. The supervision service
never gains write access to CSV, Delta or industrial SQLite. A crash after Delta
commit is reconciled through the operation metadata before any ordinary ingest.
"""

import json
import logging
import os
import uuid
from pathlib import Path

from deltalake import write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__
from .commit_protocol import application_transaction, delta_configuration
from .common import IntegrityError, canonical, digest, now
from .review_adapters import ADMISSION_VERSION, ADMIT, KEEP, REPLACE, adapter, contextual_offers

LIMIT = 1_000_000
PER_TURN = 5


def state_hash(rows):
    return digest(sorted((canonical(k), r) for k, r in rows.items()))


def initialize(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS review_operations(
        decision_id TEXT PRIMARY KEY, command_hash TEXT NOT NULL, command_json TEXT NOT NULL,
        state TEXT NOT NULL, plan_json TEXT, result_json TEXT, created_at TEXT NOT NULL,
        finished_at TEXT);
      CREATE TABLE IF NOT EXISTS review_rejections(
        row_hash TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL REFERENCES review_operations(decision_id));
    """)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _scope(a, old):
    if a.config.family in {"LISTA_RECH", "LISTA_DEFECTOS"}:
        return {
            "ranges": [[old["origen_fenix"], old["FECHA"], old["FECHA"]]],
            "groups": [digest(a.model.group(old))],
        }
    return None


def _plan(config, control, command):
    expected = {
        "schema_version",
        "decision_id",
        "projection_issue_id",
        "source_issue_id",
        "environment",
        "plant",
        "family",
        "control_id",
        "issue_hash",
        "action_id",
        "action_version",
        "candidate_hash",
        "proposed_by",
        "authorized_by",
        "authorized_at",
    }
    if set(command) != expected or command["schema_version"] != 1:
        raise IntegrityError("Unknown review command schema")
    uuid.UUID(command["decision_id"])
    if (
        any(
            command[k] != value
            for k, value in {
                "environment": os.environ.get("INGESTA_CHANNEL", config.environment),
                "plant": config.plant,
                "family": config.family,
                "control_id": control.control_id,
                "action_version": ADMISSION_VERSION if command["action_id"] == ADMIT else "1.0.0",
            }.items()
        )
        or not command["proposed_by"]
        or not command["authorized_by"]
    ):
        raise IntegrityError("Review command belongs to a different scope or version")
    issue = control.db.execute(
        "SELECT * FROM issues WHERE issue_id=?", (command["source_issue_id"],)
    ).fetchone()
    if not issue or issue["status"] == "RESOLVED":
        raise IntegrityError("Review issue is absent or already resolved")
    payload = json.loads(issue["payload_json"])
    open_cases = control.db.execute(
        "SELECT issue_id,payload_json FROM issues WHERE status!='RESOLVED'"
    ).fetchall()
    choices = contextual_offers(config, [json.loads(row[1]) for row in open_cases])
    choice = choices[digest(payload)]
    if command["action_id"] not in choice["actions"] or command["issue_hash"] != choice.get(
        "issue_hash"
    ):
        raise IntegrityError("Review evidence changed or this action is unsupported")
    a = adapter(config, control.control_id)
    old = choice["accepted"]
    candidate = old
    if command["action_id"] in {REPLACE, ADMIT}:
        variants = {r["hash"]: r["row"] for r in choice["candidates"]}
        if command["candidate_hash"] not in variants:
            raise IntegrityError("Select an explicit candidate from the observed evidence")
        candidate = variants[command["candidate_hash"]]
    elif command["action_id"] != KEEP or command["candidate_hash"] is not None:
        raise IntegrityError("Invalid keep-first decision")
    target = old if old is not None else candidate
    scope = _scope(a, target)
    if scope:
        a.store.set_scope(scope)
    version, existing = a.store.read()
    if (control.setting("last_version") or "None") != str(version):
        raise IntegrityError("Review destination is not confirmed")
    if scope and version is not None:
        from .pipeline_retired import logical_rows

        control.ensure_inspection_index(version, logical_rows(control, a.store.table()))
    rows = a.rows(existing)
    if rows.get(a.key(target)) != old:
        raise IntegrityError("Accepted value changed since the evidence was captured")
    after = dict(rows)
    if old is not None:
        if not a.homologous(old, candidate):
            raise IntegrityError("Selected candidate is not the homologous observation")
        del after[a.key(old)]
    if a.key(candidate) in after:
        raise IntegrityError("The replacement would collide with another accepted identity")
    after[a.key(candidate)] = candidate
    if command["action_id"] in {REPLACE, ADMIT}:
        a.validate(after)
        if config.family == "SIP_MAQUINA_MOLDEO":
            codes = dict(config.line_code_by_origin)
            refs = payload.get("evidence", [])
            if isinstance(refs, dict):
                refs = [refs]
            if any(
                ref.get("origin") in codes and codes[ref["origin"]] != candidate["codigo_linea_zc"]
                for ref in refs
            ):
                raise IntegrityError("Correction changes the configured physical line")
    known = {r["hash"] for r in choice["candidates"]}
    if choice["accepted_hash"] is not None:
        known.add(choice["accepted_hash"])
    close = [command["source_issue_id"]]
    # Release related holds only if every known alternative is covered by this
    # exact decision. An unfamiliar third candidate remains pending.
    for row in open_cases:
        other = choices[digest(json.loads(row[1]))]
        if (
            other["actions"]
            and a.key(other["accepted"] or other["candidates"][0]["row"]) == a.key(target)
            and {
                h
                for h in [other["accepted_hash"], *(r["hash"] for r in other["candidates"])]
                if h is not None
            }
            <= known
        ):
            close.append(row[0])
    return {
        "before_version": version,
        "archive_context": config.archive_context,
        "scope": scope,
        "old": old,
        "candidate": candidate,
        "before_hash": state_hash(rows),
        "after_hash": state_hash(after),
        "reject": sorted(known - {a.signature(candidate)}),
        "close": sorted(set(close)),
    }


def _execute(config, control, command, plan):
    a = adapter(config, control.control_id)
    a.store.read_context = plan.get("archive_context")
    if plan["scope"]:
        a.store.set_scope(plan["scope"])
    operation = digest(["review-v1", control.control_id, command["decision_id"]])
    table = a.store.table()
    version, raw = a.store.read(table)
    rows = a.rows(raw)
    history = (
        [h for h in table.history() if h.get("ingesta_operation_id") == operation] if table else []
    )
    replacing = command["action_id"] == REPLACE
    writing = command["action_id"] in {REPLACE, ADMIT}
    expected_version = (plan["before_version"] if plan["before_version"] is not None else -1) + 1
    if history:
        if (
            len(history) != 1
            or history[0]["version"] != expected_version
            or version != history[0]["version"]
            or state_hash(rows) != plan["after_hash"]
        ):
            raise IntegrityError("Review commit cannot be reconciled")
    else:
        if version != plan["before_version"] or state_hash(rows) != plan["before_hash"]:
            raise IntegrityError("Review destination changed; decision must be reassessed")
        if writing:
            # Match all original business columns, including nullable values.
            # This is a checked compare-and-swap, never an unrestricted overwrite.
            fields = a.config.contract.schema.names
            predicate = " AND ".join(
                f"(t.`{f}` = s.`before_{f}` OR (t.`{f}` IS NULL AND s.`before_{f}` IS NULL))"
                for f in fields
            )
            scoped_overwrite = "overwrite_rows" in plan
            if scoped_overwrite:
                from .repeated_hour import RULE

                if command.get("automatic_policy") != RULE:
                    raise IntegrityError(
                        "Scoped overwrite requires the approved repeated-hour policy"
                    )
            arrow = a.arrow(plan["overwrite_rows"] if scoped_overwrite else [plan["candidate"]])
            if replacing and not scoped_overwrite:
                before = a.arrow([plan["old"]])
                for f in fields:
                    arrow = arrow.append_column("before_" + f, before[f])
            props = CommitProperties(
                custom_metadata={
                    "ingesta_operation_id": operation,
                    "ingesta_control_id": control.control_id,
                    "ingesta_plant": config.plant,
                    "ingesta_contract": config.contract.name,
                    "ingesta_release": __version__,
                    "ingesta_timestamp_policy": config.timestamp_policy,
                    "ingesta_review_decision": command["decision_id"],
                    "ingesta_review_rejected_hashes": canonical(plan["reject"]),
                },
                app_transactions=application_transaction(
                    control.control_id, plan["before_version"]
                ),
                max_commit_retries=0,
            )
            if scoped_overwrite:
                write_deltalake(
                    table,
                    arrow,
                    mode="overwrite",
                    predicate=plan["overwrite_predicate"],
                    commit_properties=props,
                )
            elif replacing:
                merger = table.merge(
                    arrow,
                    predicate,
                    source_alias="s",
                    target_alias="t",
                    commit_properties=props,
                ).when_matched_update({f: f"s.`{f}`" for f in fields})
                if plan.get("archive_context"):
                    merger = merger.when_not_matched_insert({f: f"s.`{f}`" for f in fields})
                metrics = merger.execute()
                if (
                    metrics["num_target_rows_updated"] + metrics["num_target_rows_inserted"] != 1
                    or metrics["num_target_rows_deleted"]
                ):
                    raise IntegrityError("Review did not update exactly one homologous row")
            else:
                write_deltalake(
                    table if table else str(a.store.path),
                    arrow,
                    mode="append" if table else "error",
                    configuration=delta_configuration(config),
                    commit_properties=props,
                )
            table = a.store.table()
            version, raw = a.store.read(table)
            rows = a.rows(raw)
            if (
                version != expected_version
                or state_hash(rows) != plan["after_hash"]
                or not any(h.get("ingesta_operation_id") == operation for h in table.history())
            ):
                raise IntegrityError("Review result differs from its durable intention")
    if state_hash(rows) != plan["after_hash"]:
        raise IntegrityError("Review verification failed")
    result = {
        "schema_version": 1,
        "decision_id": command["decision_id"],
        "command_hash": digest(command),
        "projection_issue_id": command["projection_issue_id"],
        "environment": command["environment"],
        "plant": config.plant,
        "family": config.family,
        "control_id": control.control_id,
        "status": "VERIFIED",
        "verified_at": now(),
        "delta_version": version,
        "operation_id": operation if writing else None,
        "before": plan["old"],
        "after": plan["candidate"],
        "action_id": command["action_id"],
        "closed_source_issues": plan["close"],
        "rejected_hashes": plan["reject"],
    }
    if command.get("automatic_policy"):
        result["automatic_policy"] = command["automatic_policy"]
        result["policy_slots"] = plan["policy_slots"]
        result["replaced_rows"] = plan["replaced_rows"]
    with control.db:
        control.db.execute(
            "INSERT INTO settings VALUES('review_epoch',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (command["decision_id"],),
        )
        if writing:
            control.db.execute(
                "INSERT OR IGNORE INTO batches VALUES(?,?,'COMPLETED',?,?,?,?)",
                (
                    "review:" + command["decision_id"],
                    operation,
                    canonical(
                        {k: v for k, v in plan.items() if k != "overwrite_rows"}
                        if command.get("automatic_policy")
                        else plan
                    ),
                    now(),
                    now(),
                    version,
                ),
            )
            control.db.execute(
                "INSERT INTO settings VALUES('last_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )
        control.db.executemany(
            "INSERT OR IGNORE INTO review_rejections VALUES(?,?)",
            [(h, command["decision_id"]) for h in plan["reject"]],
        )
        control.db.executemany(
            "UPDATE issues SET status='RESOLVED' WHERE issue_id=?", [(i,) for i in plan["close"]]
        )
        if plan["scope"]:
            from .pipeline_retired import synchronize

            synchronize(control, a.store.table())
            control.db.executemany(
                "DELETE FROM inspection_current WHERE group_id=?",
                [(g,) for g in plan.get("removed_group_ids", [])],
            )
            groups = a.model.inspection_groups(raw, control.cases())
            control.db.executemany(
                "INSERT INTO inspection_current VALUES(?,?,?,?,?,?) ON CONFLICT(group_id) "
                "DO UPDATE SET payload_json=excluded.payload_json,status=excluded.status,"
                "inspected=excluded.inspected,row_count=excluded.row_count",
                [
                    (
                        g["group_id"],
                        g["group"][0],
                        canonical(g),
                        g["status"],
                        g["INSPECC"],
                        g.get("defect_rows", g.get("observation_rows")),
                    )
                    for g in groups
                ],
            )
            from .mia_scope import group_holds

            control.update_inspection_holds(group_holds(control.cases()))
            control.db.execute(
                "UPDATE settings SET value=? WHERE key='inspection_index_version'", (str(version),)
            )
        control.db.execute(
            "UPDATE review_operations SET state='VERIFIED',result_json=?,finished_at=? "
            "WHERE decision_id=?",
            (canonical(result), now(), command["decision_id"]),
        )
        if command.get("automatic_policy"):
            from .repeated_hour import confirm

            confirm(control, command, plan)
    return result


def apply_command(config, control, command):
    """Must be called with the same family writer lock as ordinary ingestion."""
    identifier = command["decision_id"]
    uuid.UUID(identifier)
    prior = control.db.execute(
        "SELECT * FROM review_operations WHERE decision_id=?", (identifier,)
    ).fetchone()
    if prior:
        if prior["command_hash"] != digest(command):
            raise IntegrityError("Review decision identity reused with different content")
        if prior["state"] in {"VERIFIED", "REJECTED"}:
            return json.loads(prior["result_json"])
        plan = json.loads(prior["plan_json"])
    else:
        if control.pending():
            return None  # Ordinary recovery always precedes a new correction.
        plan = _plan(config, control, command)
        with control.db:
            control.db.execute(
                "INSERT INTO review_operations VALUES(?,?,?,'PREPARED',?,NULL,?,NULL)",
                (identifier, digest(command), canonical(command), canonical(plan), now()),
            )
    # Errors here preserve PREPARED. Do not ingest ordinary records until reconciled.
    return _execute(config, control, command, plan)


def process(config, control):
    root = os.environ.get("INGESTA_REVIEW_ROOT")
    root = Path(root) if root else None
    for pending in control.db.execute(
        "SELECT command_json FROM review_operations WHERE state='PREPARED' ORDER BY created_at"
    ).fetchall():
        command = json.loads(pending[0])
        if root is None and not command.get("automatic_policy"):
            raise IntegrityError("Pending review requires its configured command/result transport")
        result = apply_command(config, control, command)
        if root is not None and not command.get("automatic_policy"):
            atomic(root / "results" / config.family / (command["decision_id"] + ".json"), result)
    if root is None:
        return
    directory = root / "commands" / config.family
    if not directory.is_dir():
        return
    # The sender removes a command only after its verified receipt is imported.
    # Completed files cannot monopolize this bounded queue after HTTP/DB failures.
    processed = 0
    for path in sorted(directory.glob("*.json")):
        if processed >= PER_TURN:
            break
        try:
            identifier = str(uuid.UUID(path.stem))
        except ValueError:
            logging.getLogger(__name__).warning(
                "review_command_filename_invalid family=%s", config.family
            )
            continue
        result_path = root / "results" / config.family / (identifier + ".json")
        if result_path.exists():
            continue
        with path.open("rb") as stream:
            content = stream.read(LIMIT + 1)
        command = {}
        try:
            if len(content) > LIMIT:
                raise IntegrityError("Review command exceeds budget")
            command = json.loads(content)
            if not isinstance(command, dict) or command.get("decision_id") != identifier:
                raise IntegrityError("Review filename and decision identity differ")
            atomic(
                result_path.with_suffix(".progress.json"),
                {
                    "decision_id": identifier,
                    "command_hash": digest(command),
                    "status": "RECEIVED_BY_WRITER",
                    "at": now(),
                },
            )
            result = apply_command(config, control, command)
        except (IntegrityError, ValueError, KeyError) as exc:
            prior = control.db.execute(
                "SELECT state FROM review_operations WHERE decision_id=?", (identifier,)
            ).fetchone()
            if prior and prior[0] == "PREPARED":
                raise  # Uncertain write; retain and retry before ordinary ingest.
            if not isinstance(command, dict):
                command = {}
            result = {
                "schema_version": 1,
                "decision_id": identifier,
                "command_hash": digest(command) if command else digest(content.hex()),
                "status": "REJECTED",
                "error": str(exc),
                "verified_at": now(),
                "environment": command.get("environment"),
                "plant": command.get("plant", config.plant),
                "family": command.get("family", config.family),
                "control_id": command.get("control_id"),
                "writer_control_id": control.control_id,
            }
            if not prior and command.get("decision_id") == identifier:
                with control.db:
                    control.db.execute(
                        "INSERT INTO review_operations VALUES(?,?,?,'REJECTED',NULL,?,?,?)",
                        (
                            identifier,
                            digest(command),
                            canonical(command),
                            canonical(result),
                            now(),
                            now(),
                        ),
                    )
        if result is not None:
            atomic(result_path, result)
            result_path.with_suffix(".progress.json").unlink(missing_ok=True)
        processed += 1


def pending_families(families):
    """Discover command names only; the owner validates commands under its writer lock."""
    configured = os.environ.get("INGESTA_REVIEW_ROOT")
    if not configured:
        return set()
    root, pending = Path(configured), set()
    for family in families:
        for path in (root / "commands" / family).glob("*.json"):
            try:
                identifier = str(uuid.UUID(path.stem))
            except ValueError:
                continue
            if not (root / "results" / family / (identifier + ".json")).exists():
                pending.add(family)
                break
    return pending
