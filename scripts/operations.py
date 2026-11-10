"""Owner-only cold backups and disposable restore drills. No production restore target."""

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.db.models import Base

ROOT = Path(__file__).resolve().parents[1]

# Executed inside the app/tool image to reach unexposed Qdrant over its Docker network.
# No job text/vectors are printed except the binary snapshot pipe into a private file.
QDRANT_RPC = r"""
import hashlib,json,math,os,sys,shutil,tempfile
import httpx
action=sys.argv[1]
origin=os.environ.get("QDRANT_URL","http://qdrant:6333")
with httpx.Client(base_url=origin,timeout=120,trust_env=False) as client:
    def call(method,path,**kwargs):
        response=client.request(method,path,**kwargs)
        response.raise_for_status()
        return response.json()["result"]
    if action=="inspect":
        result={}
        prefix=os.environ.get("QDRANT_COLLECTION_PREFIX","career_jobs")+"_"
        for item in call("GET","/collections")["collections"]:
            name=item["name"]
            if not name.startswith(prefix): continue
            config=call("GET","/collections/"+name)["config"]["params"]["vectors"]
            offset=None;digests=[];sample=None
            while True:
                batch={"limit":128,"with_payload":True,"with_vector":True}
                if offset is not None: batch["offset"]=offset
                page=call("POST","/collections/"+name+"/points/scroll",json=batch)
                for point in page["points"]:
                    digests.append(hashlib.sha256(json.dumps(point,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest())
                    if sample is None: sample=point
                offset=page["next_page_offset"]
                if offset is None: break
            query_ok=False
            if sample is not None:
                found=call("POST","/collections/"+name+"/points/query",json={
                    "query":sample["vector"],"filter":{"must":[{"has_id":[sample["id"]]}]},
                    "params":{"exact":True},"limit":1})["points"]
                query_ok=bool(found and str(found[0]["id"])==str(sample["id"])
                              and math.isfinite(found[0]["score"]))
                if not query_ok: raise ValueError("Restored vector query failed")
            result[name]={"points":len(digests),"digest":hashlib.sha256("".join(sorted(digests)).encode()).hexdigest(),"vectors":config,"query_ok":query_ok}
        print(json.dumps(result,sort_keys=True))
    elif action=="create":
        print(json.dumps(call("POST","/collections/"+sys.argv[2]+"/snapshots")))
    elif action=="download":
        with client.stream("GET","/collections/"+sys.argv[2]+"/snapshots/"+sys.argv[3]) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(): sys.stdout.buffer.write(chunk)
    elif action=="delete":
        call("DELETE","/collections/"+sys.argv[2]+"/snapshots/"+sys.argv[3])
    elif action=="restore":
        # A pipe's fstat size is zero; spool to disk so multipart has the correct length.
        with tempfile.TemporaryFile() as snapshot:
            shutil.copyfileobj(sys.stdin.buffer,snapshot)
            snapshot.seek(0)
            result=call("POST","/collections/"+sys.argv[2]+"/snapshots/upload",
                params={"priority":"snapshot","wait":"true"},
                files={"snapshot":("backup.snapshot",snapshot,"application/octet-stream")})
        if result is not True: raise ValueError("Snapshot restore not confirmed")
"""


class Compose:
    def __init__(
        self,
        path: Path,
        project: str,
        *,
        env_file: Path | None = None,
        env: dict[str, str] | None = None,
    ):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,60}", project):
            raise ValueError("Invalid Compose project name")
        self.prefix = ["docker", "compose", "-f", str(path), "-p", project]
        if env_file:
            self.prefix.extend(["--env-file", str(env_file)])
        self.env = {**os.environ, **(env or {})}

    def run(self, *args: str, stdin=None, stdout=None, timeout=180) -> bytes:
        result = subprocess.run(
            [*self.prefix, *args],
            cwd=ROOT,
            env=self.env,
            stdin=stdin,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            # Errors may contain source content; keep the CLI output body-free.
            logs = ROOT / "backups"
            logs.mkdir(mode=0o700, exist_ok=True)
            path = logs / ("operation-error-" + secrets.token_hex(5) + ".log")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(result.stderr or b"No stderr returned")
            raise RuntimeError("Compose operation failed: " + args[0])
        return result.stdout or b""

    def qdrant(self, service: str, action: str, *args: str, **kwargs):
        return self.run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            "python",
            service,
            "-c",
            QDRANT_RPC,
            action,
            *args,
            **kwargs,
        )


def private_directory(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / "backups").resolve()):
        raise ValueError("Backups must be under ignored backups/")
    resolved.mkdir(parents=True, exist_ok=False, mode=0o700)
    return resolved


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def table_fingerprints(stack: Compose) -> dict:
    tables = sorted([*Base.metadata.tables, "alembic_version"])
    pairs = []
    for table in tables:
        if table == "cv_documents":
            pairs.append("'cv_documents',json_build_object('rows',0,'digest',md5(''))")
        else:
            pairs.append(
                f"'{table}',(SELECT json_build_object('rows',count(*),'digest',"
                "md5(coalesce(string_agg(md5(to_jsonb(t)::text),'' "
                f"ORDER BY md5(to_jsonb(t)::text)),''))) FROM public.\"{table}\" t)"
            )
    sql = "SELECT json_build_object(" + ",".join(pairs) + ")"
    return json.loads(
        stack.run(
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
            "sh",
            sql,
        )
    )


def backup(stack: Compose, output: Path) -> dict:
    destination = private_directory(output)
    running = stack.run("ps", "--status", "running", "--services").decode().splitlines()
    started = time.monotonic()
    try:
        if "api" in running:
            stack.run("stop", "api")
        tables = table_fingerprints(stack)
        vectors = json.loads(stack.qdrant("api", "inspect"))
        with (destination / "postgres.dump").open("xb") as target:
            stack.run(
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc '
                "--schema=public --no-owner --no-privileges "
                "--exclude-table-data=public.cv_documents",
                stdout=target,
            )
        files = {"postgres.dump": digest(destination / "postgres.dump")}
        for collection in sorted(vectors):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", collection):
                raise ValueError("Invalid collection name")
            snapshot = json.loads(stack.qdrant("api", "create", collection))["name"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", snapshot):
                raise ValueError("Invalid snapshot name")
            filename = collection + ".snapshot"
            try:
                with (destination / filename).open("xb") as target:
                    stack.qdrant("api", "download", collection, snapshot, stdout=target)
                files[filename] = digest(destination / filename)
            finally:
                stack.qdrant("api", "delete", collection, snapshot)
        if (
            table_fingerprints(stack) != tables
            or json.loads(stack.qdrant("api", "inspect")) != vectors
        ):
            raise ValueError("Source changed during backup; discard incomplete bundle")
        manifest = {
            "version": "m6-backup-v1",
            "captured_at": datetime.now(UTC).isoformat(),
            "files": files,
            "tables": tables,
            "collections": vectors,
            "excluded_table_data": ["cv_documents"],
            "postgres_major": 17,
            "qdrant_version": "1.20.0",
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for path in destination.iterdir():
            path.chmod(0o600)
    finally:
        if "api" in running:
            stack.run("start", "--wait", "--wait-timeout", "60", "api")
    return {
        "backup": str(destination.relative_to(ROOT)),
        "collections": len(vectors),
        "points": sum(value["points"] for value in vectors.values()),
        "pause_seconds": round(time.monotonic() - started, 2),
        "cv_profiles_in_backup": 0,
    }


def verify_bundle(path: Path) -> dict:
    path = path.resolve()
    if not path.is_relative_to((ROOT / "backups").resolve()):
        raise ValueError("Use ignored backups/")
    manifest = json.loads((path / "manifest.json").read_text())
    if (
        manifest.get("version") != "m6-backup-v1"
        or manifest.get("postgres_major") != 17
        or manifest.get("qdrant_version") != "1.20.0"
    ):
        raise ValueError("Unsupported backup format/versions")
    expected = {"postgres.dump", *(name + ".snapshot" for name in manifest["collections"])}
    if set(manifest["files"]) != expected:
        raise ValueError("Incomplete backup file list")
    for name, checksum in manifest["files"].items():
        if Path(name).name != name or not (path / name).resolve().is_relative_to(path):
            raise ValueError("Unsafe backup filename")
        if digest(path / name) != checksum:
            raise ValueError("Backup checksum mismatch")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", name) for name in manifest["collections"]):
        raise ValueError("Invalid collection name")
    return manifest


def restore_drill(bundle: Path, *, tool_image: str = "ai-career-intelligence:local") -> dict:
    manifest = verify_bundle(bundle)
    project = "career-restore-" + secrets.token_hex(5)
    stack = Compose(
        ROOT / "infra/compose.restore.yaml",
        project,
        env={"RESTORE_PASSWORD": secrets.token_hex(32), "RESTORE_TOOL_IMAGE": tool_image},
    )
    started = time.monotonic()
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "90", "postgres", "qdrant")
        with (bundle / "postgres.dump").open("rb") as source:
            stack.run(
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                "exec pg_restore --exit-on-error --clean --if-exists --no-owner --no-privileges "
                '-U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                stdin=source,
            )
        for collection in sorted(manifest["collections"]):
            with (bundle / (collection + ".snapshot")).open("rb") as source:
                stack.qdrant("tool", "restore", collection, stdin=source)
        if table_fingerprints(stack) != manifest["tables"]:
            raise ValueError("Restored database fingerprints differ")
        vectors = json.loads(stack.qdrant("tool", "inspect"))
        if vectors != manifest["collections"]:
            raise ValueError("Restored Qdrant data/query fingerprints differ")
        report = {
            "backup": str(bundle.relative_to(ROOT)),
            "restored_tables": len(manifest["tables"]),
            "collections": len(vectors),
            "points": sum(value["points"] for value in vectors.values()),
            "row_and_content_hashes_match": True,
            "vector_payload_hashes_match": True,
            "vector_queries_passed": all(
                value["query_ok"] for value in vectors.values() if value["points"]
            ),
            "rto_seconds": round(time.monotonic() - started, 2),
            "source_untouched": True,
            "cv_profiles_restored": 0,
            "captured_at": manifest["captured_at"],
        }
        output = bundle / "restore-report.json"
        with output.open("x") as stream:
            json.dump(report, stream, indent=2)
        output.chmod(0o600)
        return report
    finally:
        # This function only creates a fresh random project; production is never a target.
        stack.run("down", "--volumes", "--remove-orphans")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--compose-file", type=Path, default=ROOT / "compose.yaml")
    create.add_argument("--project", default="ai-career-intelligence")
    create.add_argument("--env-file", type=Path)
    create.add_argument(
        "--writers-idle",
        action="store_true",
        required=True,
        help="Confirm no operator import/index CLI is running; API is paused automatically",
    )
    restore = commands.add_parser("restore-drill")
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--tool-image", default="ai-career-intelligence:local")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "backup":
            if args.compose_file.resolve() not in {
                ROOT / "compose.yaml",
                ROOT / "compose.production.yaml",
            }:
                raise ValueError("Unsupported source Compose file")
            result = backup(
                Compose(args.compose_file, args.project, env_file=args.env_file), args.output
            )
        else:
            result = restore_drill(args.bundle.resolve(), tool_image=args.tool_image)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": "Operation failed; check stack, backup integrity and versions.",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
