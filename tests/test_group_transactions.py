import sqlite3

import pytest

from app.db import Database


def test_create_group_rolls_back_if_members_cannot_be_saved(tmp_path):
    db = Database(tmp_path / "fleet.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.create_group("Broken", ["00:11:22:33:44:55", "00:11:22:33:44:55"])

        assert db.list_groups() == []
    finally:
        db.close()


def test_update_group_rolls_back_name_if_members_cannot_be_saved(tmp_path):
    db = Database(tmp_path / "fleet.db")
    try:
        group = db.create_group("Original")

        with pytest.raises(sqlite3.IntegrityError):
            db.update_group(
                group["id"],
                "Changed",
                ["00:11:22:33:44:55", "00:11:22:33:44:55"],
            )

        assert db.get_group(group["id"])["name"] == "Original"
        assert db.get_group(group["id"])["macs"] == []
    finally:
        db.close()
