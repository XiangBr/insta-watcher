import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command
from tests.conftest import BACKEND_DIR

TS = "2026-10-02T12:00:00.000000Z"


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_models_match_migrations(database_url: str) -> None:
    # `database_url` is already upgraded to head; any model change without a migration fails here.
    command.check(_config(database_url))


def _seed_old_database(path: Path) -> None:
    """A campaign and a delivery that points at it, as production has after a few days."""
    con = sqlite3.connect(path)
    con.execute(
        "insert into campaign (name, is_active, post_id, match_any_post, keywords, match_mode,"
        " dm_message, public_reply_enabled, public_reply_messages, created_at, updated_at)"
        " values (?, 1, ?, 0, ?, 'WHOLE_WORD', 'Oi', 0, '[]', ?, ?)",
        ("ebook", "18446074984116754", '["ebook"]', TS, TS),
    )
    con.execute(
        "insert into delivery (campaign_id, trigger, origin, source_id, actor_id, status,"
        " attempts, created_at, updated_at)"
        " values (1, 'COMMENT', 'WEBHOOK', 'c1', '9', 'SENT', 1, ?, ?)",
        (TS, TS),
    )
    con.commit()
    con.close()


def test_upgrading_a_database_with_data_keeps_every_row(tmp_path: Path) -> None:
    # Regression: a batch_alter_table on `campaign` tried to drop it while `delivery` still
    # referenced it. Foreign keys are on in the migration engine, so the upgrade failed and the
    # container could not boot. Empty test databases never showed it.
    path = tmp_path / "old.db"
    cfg = _config(f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(cfg, "0003")
    _seed_old_database(path)

    command.upgrade(cfg, "head")

    con = sqlite3.connect(path)
    campaign = con.execute(
        "select name, trigger_comments, trigger_story_replies from campaign"
    ).fetchall()
    assert campaign == [("ebook", 1, 0)]  # keeps answering comments, not story replies
    # The follow gate starts switched off for every existing campaign and delivery.
    gate = con.execute("select follow_gate_enabled, follow_gate_message from campaign").fetchall()
    assert gate == [(0, None)]
    states = con.execute("select gate_state, gate_taps, followup_status from delivery").fetchall()
    assert states == [(None, 0, None)]
    assert con.execute("select count(*) from delivery").fetchone() == (1,)
    leftovers = con.execute(
        "select name from sqlite_master where name like '%alembic_tmp%'"
    ).fetchall()
    assert leftovers == []
    # The idempotency constraints survived the upgrade.
    names = {row[0] for row in con.execute("select sql from sqlite_master where name='delivery'")}
    sql = " ".join(names)
    assert "uq_delivery_campaign_source" in sql
    assert "uq_delivery_trigger_source" in sql
    con.close()


def test_downgrade_keeps_the_rows_too(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    cfg = _config(f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(cfg, "0003")
    _seed_old_database(path)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "0003")

    con = sqlite3.connect(path)
    assert con.execute("select name from campaign").fetchall() == [("ebook",)]
    columns = [row[1] for row in con.execute("pragma table_info(campaign)")]
    assert "trigger_story_replies" not in columns
    assert con.execute("select count(*) from delivery").fetchone() == (1,)
    con.close()
