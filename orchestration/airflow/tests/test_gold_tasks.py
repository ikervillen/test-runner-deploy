from datetime import UTC, datetime

import pendulum
import pytest
from gold_tasks import (
    backup_command,
    local_moment,
    maintain_command,
    publish_command,
    restore_drill_command,
)

MADRID = pendulum.timezone("Europe/Madrid")


@pytest.mark.parametrize(
    ("instant", "local"),
    [
        (datetime(2026, 11, 6, 14, 40, tzinfo=UTC), "2026-11-06T15:40:00"),
        (datetime(2026, 7, 1, 13, 40, tzinfo=UTC), "2026-07-01T15:40:00"),
    ],
)
def test_local_moment_follows_plant_daylight_saving(instant, local):
    assert local_moment(instant, MADRID) == local


def test_local_moment_rejects_naive_instants():
    with pytest.raises(ValueError, match="timezone-aware"):
        local_moment(datetime(2026, 11, 6, 15, 40), MADRID)


def test_publish_passes_only_the_scheduled_moment():
    command = publish_command(datetime(2026, 11, 6, 14, 40, tzinfo=UTC))
    assert command == [
        "calidad-gold",
        "publish",
        "--settings",
        "/config/settings.json",
        "--at",
        "2026-11-06T15:40:00",
    ]


def test_each_backup_attempt_gets_a_new_destination():
    run = datetime(2026, 11, 7, 1, 30, tzinfo=UTC)
    first, retry = backup_command(run, 1), backup_command(run, 2)
    assert first[-2:] == ["--destination", "/backup/20261107T013000Z-try1"]
    assert retry[-1] == "/backup/20261107T013000Z-try2"


def test_maintenance_keeps_the_configured_rollback_window():
    assert maintain_command() == [
        "calidad-gold",
        "maintain",
        "--root",
        "/gold",
        "--keep-days",
        "30",
    ]
    assert maintain_command(7)[-1] == "7"


def test_restore_drill_uses_scratch_space_on_the_backup_volume():
    assert restore_drill_command()[-2:] == ["--scratch", "/backup/.restore-drill"]
