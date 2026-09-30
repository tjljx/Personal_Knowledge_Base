"""Consistent database export plus integrity-checked immutable document blobs."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from knowledge_api.config import Settings, get_settings
from knowledge_api.models import DocumentVersion

SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def referenced_blobs(engine: Engine) -> set[str]:
    with Session(engine) as db:
        return set(db.scalars(select(DocumentVersion.sha256)).all())


def dump_database(settings: Settings, target: Path) -> str:
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("内存数据库无法备份")
        with sqlite3.connect(url.database) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        return "sqlite"
    if url.get_backend_name() == "postgresql":
        safe_url = url.set(drivername="postgresql", password=None)
        environment = os.environ.copy()
        if url.password is not None:
            environment["PGPASSWORD"] = url.password
        try:
            result = subprocess.run(
                [
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    f"--dbname={safe_url.render_as_string(hide_password=False)}",
                    f"--file={target}",
                ],
                env=environment,
                capture_output=True,
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("PostgreSQL 备份工具不可用或执行超时") from exc
        if result.returncode != 0:
            raise RuntimeError("PostgreSQL 导出失败；请检查连接和备份权限")
        return "pg_custom"
    raise ValueError("仅支持 SQLite 和 PostgreSQL 备份")


def create_backup(settings: Settings, destination: Path, engine: Engine | None = None) -> dict:
    destination = destination.resolve()
    storage = settings.storage_root.resolve()
    if destination == storage or storage in destination.parents:
        raise ValueError("备份目录不能放在原文件存储目录内")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "INCOMPLETE").write_text("备份未完成，请勿用于恢复。", encoding="utf-8")
    owns_engine = engine is None
    engine = engine or create_engine(settings.database_url, pool_pre_ping=True)
    try:
        before = referenced_blobs(engine)
        database_file = destination / "database.dump"
        database_format = dump_database(settings, database_file)
        after = referenced_blobs(engine)
        files = {}
        for digest in sorted(before | after):
            if not SHA256.fullmatch(digest):
                raise ValueError("数据库包含无效文件哈希")
            source = storage / "blobs" / digest[:2] / digest
            if source.is_symlink() or not source.is_file():
                raise FileNotFoundError("原文件缺失，备份未完成")
            target = destination / "blobs" / digest[:2] / digest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if file_hash(target) != digest:
                raise ValueError("原文件校验失败，备份未完成")
            files[digest] = target.stat().st_size
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "database_format": database_format,
            "database_sha256": file_hash(database_file),
            "blobs": files,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "INCOMPLETE").unlink()
        return manifest
    finally:
        if owns_engine:
            engine.dispose()


def verify_backup(destination: Path) -> dict:
    destination = destination.resolve()
    if (destination / "INCOMPLETE").exists():
        raise ValueError("备份未完成")
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("备份格式不兼容")
    database_file = destination / "database.dump"
    if file_hash(database_file) != manifest["database_sha256"]:
        raise ValueError("数据库备份校验失败")
    for digest, expected_size in manifest["blobs"].items():
        if not SHA256.fullmatch(digest):
            raise ValueError("清单包含无效文件哈希")
        path = destination / "blobs" / digest[:2] / digest
        if path.is_symlink() or path.stat().st_size != expected_size or file_hash(path) != digest:
            raise ValueError("原文件备份校验失败")
    if manifest["database_format"] == "sqlite":
        with sqlite3.connect(f"file:{database_file.as_posix()}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite 数据库完整性校验失败")
            required = {row[0] for row in db.execute("SELECT sha256 FROM document_versions")}
        if not required.issubset(manifest["blobs"]):
            raise ValueError("数据库引用的原文件未全部备份")
    elif manifest["database_format"] == "pg_custom":
        try:
            result = subprocess.run(
                ["pg_restore", "--list", str(database_file)],
                capture_output=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("PostgreSQL 恢复工具不可用或校验超时") from exc
        if result.returncode != 0:
            raise ValueError("PostgreSQL 备份格式校验失败")
    else:
        raise ValueError("未知数据库备份格式")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backup and verify KB database and immutable files"
    )
    parser.add_argument("action", choices=["create", "verify"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    manifest = (
        create_backup(get_settings(), args.directory)
        if args.action == "create"
        else verify_backup(args.directory)
    )
    print(
        json.dumps(
            {
                "created_at": manifest["created_at"],
                "database_format": manifest["database_format"],
                "blob_count": len(manifest["blobs"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
