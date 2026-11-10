import json
from pathlib import Path

import pytest

from scripts import operations


def test_backup_checksum_and_path_guards_run_before_any_restore(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(operations, "ROOT", tmp_path)
    bundle = operations.private_directory(tmp_path / "backups" / "fixture")
    assert bundle.stat().st_mode & 0o777 == 0o700
    dump = bundle / "postgres.dump"
    dump.write_bytes(b"synthetic-backup-fixture")
    manifest = {
        "version": "m6-backup-v1",
        "postgres_major": 17,
        "qdrant_version": "1.20.0",
        "collections": {},
        "files": {"postgres.dump": operations.digest(dump)},
    }
    path = bundle / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert operations.verify_bundle(bundle) == manifest
    dump.write_bytes(b"corrupted")
    monkeypatch.setattr(
        operations.Compose, "run", lambda *args, **kwargs: pytest.fail("Restore must not start")
    )
    with pytest.raises(ValueError, match="checksum"):
        operations.restore_drill(bundle)
    with pytest.raises(ValueError, match="Backups must"):
        operations.private_directory(tmp_path / "outside")
    with pytest.raises(ValueError, match="project name"):
        operations.Compose(Path("fixture.yaml"), "../production")
