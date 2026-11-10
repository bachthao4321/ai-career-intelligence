"""Local-only HTTPS/auth/CV/admin smoke using disposable production volumes."""

import json
import os
import secrets
import ssl
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from scripts.operations import ROOT
from tests.cv_helpers import pdf_bytes


def main() -> None:
    project = "career-tls-test-" + secrets.token_hex(4)
    credentials = {
        "PUBLIC_DOMAIN": "localhost",
        "PUBLIC_ORIGIN": "https://localhost:18443",
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "ANALYTICS_ROLE_PASSWORD": secrets.token_hex(32),
        "APP_ROLE_PASSWORD": secrets.token_hex(32),
        "DEMO_PASSWORD": secrets.token_hex(24),
        "DEMO_COOKIE_SECRET": secrets.token_hex(32),
        "ADMIN_KEY": secrets.token_hex(32),
        "DEMO_USERNAME": "viewer",
        "LLM_GLOBAL_DAILY_CALL_LIMIT": "0",
        "CLIENT_REQUESTS_PER_MINUTE": "60",
    }
    environment = {**os.environ, **credentials}
    command = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        "compose.production.yaml",
        "-f",
        "infra/compose.tls-test.yaml",
    ]

    def run(*args, **kwargs):
        return subprocess.run(
            [*command, *args],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            timeout=180,
            **kwargs,
        ).stdout

    output = ROOT / "data/evaluations/m6-local-tls"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca = output / "root.crt"
    started = time.monotonic()
    try:
        rendered = json.loads(run("config", "--format", "json"))
        for name, service in rendered["services"].items():
            if name != "proxy":
                assert not service.get("ports"), "Backend port exposed"
            else:
                assert all(port.get("host_ip") == "127.0.0.1" for port in service["ports"])
        run("up", "-d", "--build", "--wait", "--wait-timeout", "120")
        run(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            """import os,psycopg
from sqlalchemy.engine import make_url
url=make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql")
with psycopg.connect(url.render_as_string(hide_password=False)) as c:
    flags=c.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
                    "FROM pg_roles WHERE rolname=current_user").fetchone()
    assert not any(flags)
    try:
        c.execute("CREATE TABLE public.forbidden_by_app_role(id int)")
    except psycopg.errors.InsufficientPrivilege:
        c.rollback()
    else:
        raise AssertionError("Application role must not create tables")
""",
        )
        # Trigger local CA issuance, without trusting it in the host/system keychain.
        for attempt in range(10):
            try:
                with httpx.Client(verify=False, trust_env=False, timeout=3) as trigger:
                    trigger.get("https://localhost:18443/")
                break
            except httpx.RequestError:
                if attempt == 9:
                    raise
                time.sleep(0.5)
        ca.write_bytes(
            run("exec", "-T", "proxy", "cat", "/data/caddy/pki/authorities/local/root.crt")
        )
        context = ssl.create_default_context(cafile=str(ca))
        with httpx.Client(
            base_url="https://localhost:18443", verify=context, trust_env=False, timeout=70
        ) as client:
            assert client.get("/").status_code == 401
            response = client.get("/", auth=("viewer", credentials["DEMO_PASSWORD"]))
            assert response.status_code == 200
            assert (
                "Secure" in response.headers["set-cookie"]
                and "HttpOnly" in response.headers["set-cookie"]
            )
            assert client.get("/docs").status_code == 200
            assert (
                client.post(
                    "/search", json={"query": "Python"}, headers={"Origin": "https://evil.example"}
                ).status_code
                == 403
            )
            origin = {"Origin": credentials["PUBLIC_ORIGIN"]}
            assert (
                client.post(
                    "/search",
                    content=b"x" * 2097153,
                    headers={**origin, "Content-Type": "application/json"},
                ).status_code
                == 413
            )
            records = json.loads((ROOT / "examples/jobs.synthetic.json").read_text())
            assert isinstance(records, list)
            assert (
                client.post("/admin/import", json={"records": records}, headers=origin).status_code
                == 403
            )
            response = client.post(
                "/admin/import",
                json={"records": records},
                headers={**origin, "X-Admin-Key": credentials["ADMIN_KEY"]},
            )
            response.raise_for_status()
            overview = client.get("/platform/overview").json()
            assert overview["scope"] == "public_sources" and overview["listings"] == len(records)
            response = client.post(
                "/search", json={"query": "Python", "rerank": True}, headers=origin
            )
            response.raise_for_status()
            assert response.json()["rerank"]["status"] == "disabled"
            response = client.post("/ask", json={"question": "How many jobs?"}, headers=origin)
            response.raise_for_status()
            assert response.json()["status"] == "ok"
            response = client.post(
                "/cv", content=pdf_bytes(), headers={**origin, "Content-Type": "application/pdf"}
            )
            response.raise_for_status()
            profile = response.json()
            path = "/cv/" + profile["id"]
            bearer = {**origin, "Authorization": "Bearer " + profile["access_token"]}
            assert client.get(path, headers=bearer).status_code == 200
            response = client.post(path + "/matches", json={"top_k": 1}, headers=bearer)
            response.raise_for_status()
            assert response.json()["items"]
            assert client.delete(path, headers=bearer).status_code == 204
            assert client.get(path, headers=bearer).status_code == 404
            run("restart", "api")
            # Wait for API health after restart; signed cookie and DB counters survive.
            run("up", "--no-deps", "--no-recreate", "-d", "--wait", "--wait-timeout", "60", "api")
            assert client.get("/platform/overview").json()["listings"] == len(records)

            def spoofed_request(number: int) -> int:
                return client.get(
                    "/platform/overview", headers={"X-Forwarded-For": f"198.51.100.{number + 1}"}
                ).status_code

            # More than two fixed-minute budgets, as a short burst, also covers a
            # minute boundary without relying on a single sequential request's timing.
            with ThreadPoolExecutor(max_workers=8) as pool:
                statuses = list(pool.map(spoofed_request, range(125)))
            assert set(statuses) <= {200, 429}, statuses
            assert 429 in statuses, "Forged forwarding headers must not bypass the client limit"
        run(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            """import os,psycopg
from sqlalchemy.engine import make_url
url=make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql")
with psycopg.connect(url.render_as_string(hide_password=False)) as c:
    assert c.execute("SELECT coalesce(sum(calls),0) FROM llm_daily_usage").fetchone()[0]==0
""",
        )
        report = {
            "tls_verified_with_local_ca": True,
            "auth_admin_csrf_cv_restart": True,
            "application_db_role_restricted": True,
            "forwarded_ip_spoofing_blocked": True,
            "backend_ports_exposed": False,
            "model_calls": 0,
            "public_deployment": False,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        (output / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    except subprocess.CalledProcessError as exc:
        # Keep logs private, and never print configuration/environment secrets.
        (output / "failure.log").write_bytes(exc.stderr or b"Operation failed")
        (output / "failure.log").chmod(0o600)
        raise RuntimeError("Production smoke failed; see ignored local failure log") from None
    finally:
        run("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
