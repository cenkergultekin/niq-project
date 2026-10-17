"""Yerel gerçek sunucu testleri: python tests/local_databases.py [--keep | --stop].

Docker gerekir. Yalnızca niq-local-* geçici test kapsayıcıları kullanılır.
Parolalar rastgele üretilir, komut argümanlarına yazılmaz. --keep sonraki
yerel kontroller için sunucuları açık bırakır; --stop onları durdurur.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(tempfile.gettempdir()) / "niq-local-test-servers.json"
SERVERS = {
    "postgres": ("postgres:17", 55432, 5432, "postgresql", "niq_admin", "NIQ_IT_POSTGRES"),
    "mysql": ("mysql:8.4", 53307, 3306, "mysql", "root", "NIQ_IT_MYSQL"),
    "mariadb": ("mariadb:11.8", 53306, 3306, "mysql", "root", "NIQ_IT_MARIADB"),
    "mssql": ("mcr.microsoft.com/mssql/server:2022-latest", 51433, 1433, "mssql", "sa", "NIQ_IT_MSSQL"),
}


def docker(*args, env=None):
    result = subprocess.run(["docker", *args], env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def stop():
    for name in SERVERS:
        container = "niq-local-" + name
        # Yalnız bu aracın etiketlediği test sunucuları durdurulur.
        running = docker("ps", "-q", "--filter", f"name=^/{container}$", "--filter", "label=niq.local-test=true")
        if running:
            docker("stop", container)
    print("Yerel test sunucuları kapalı.", flush=True)


def configure_postgres_tls():
    docker("exec", "-u", "postgres", "niq-local-postgres", "openssl", "req", "-x509", "-newkey", "rsa:2048",
           "-noenc", "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
           "-keyout", "/var/lib/postgresql/data/server.key", "-out", "/var/lib/postgresql/data/server.crt")
    docker("exec", "-u", "postgres", "niq-local-postgres", "psql", "-U", "niq_admin", "-d", "niq_it",
           "-c", "ALTER SYSTEM SET ssl = 'on'")
    docker("exec", "-u", "postgres", "niq-local-postgres", "psql", "-U", "niq_admin", "-d", "niq_it",
           "-c", "SELECT pg_reload_conf()")
    docker("cp", "niq-local-postgres:/var/lib/postgresql/data/server.crt",
           str(Path(tempfile.gettempdir()) / "niq-local-postgres.crt"))


def start(name):
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import create_engine, text

    from backend.db.dialects import build_url

    image, port, internal, dialect, user, env_name = SERVERS[name]
    password = "Niq_" + secrets.token_hex(16) + "!"
    env = dict(os.environ)
    if name == "postgres":
        env.update(POSTGRES_PASSWORD=password, POSTGRES_USER=user, POSTGRES_DB="niq_it")
        variables = ["POSTGRES_PASSWORD", "POSTGRES_USER", "POSTGRES_DB"]
    elif name == "mssql":
        env.update(ACCEPT_EULA="Y", MSSQL_PID="Developer", MSSQL_SA_PASSWORD=password)
        variables = ["ACCEPT_EULA", "MSSQL_PID", "MSSQL_SA_PASSWORD"]
    else:
        prefix = "MARIADB" if name == "mariadb" else "MYSQL"
        env.update({prefix + "_ROOT_PASSWORD": password, prefix + "_DATABASE": "niq_it", prefix + "_ROOT_HOST": "%"})
        variables = [prefix + "_ROOT_PASSWORD", prefix + "_DATABASE", prefix + "_ROOT_HOST"]
    args = ["run", "--rm", "-d", "--name", "niq-local-" + name, "--label", "niq.local-test=true",
            "-p", f"127.0.0.1:{port}:{internal}"]
    for variable in variables:
        args += ["-e", variable]
    docker(*args, image, env=env)
    database = "master" if name == "mssql" else "niq_it"
    url = build_url(dialect, "127.0.0.1", port, database, user, password)
    connect_args = {"timeout": 2} if name == "mssql" else {"connect_timeout": 2}
    engine = create_engine(url, connect_args=connect_args, isolation_level="AUTOCOMMIT")
    try:
        for _ in range(90):
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                    if name == "mssql":
                        conn.execute(text("IF DB_ID('niq_it') IS NULL CREATE DATABASE niq_it"))
                if name == "postgres":
                    configure_postgres_tls()
                print(f"{name}: hazır (127.0.0.1:{port})", flush=True)
                return env_name, f"127.0.0.1:{port}:niq_it:{user}:{password}"
            except Exception:
                time.sleep(1)
        raise RuntimeError(f"{name} 90 saniyede başlamadı; docker logs niq-local-{name} ile bakın.")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--desktop", action="store_true", help="Qt mikrofon ve bağlantı paneli kontrollerini de çalıştır")
    parser.add_argument("--real-mic", action="store_true", help="Fiziksel mikrofon kontrolünü de aç; düğmeye basıp durdur")
    args = parser.parse_args()
    if args.stop:
        stop()
        MANIFEST.unlink(missing_ok=True)
        return 0
    os.environ.setdefault("NIQ_HOME", tempfile.mkdtemp(prefix="niq-local-home-"))
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            configured = dict(pool.map(start, SERVERS))
        # Yalnızca geçici test veritabanlarının üretilmiş sırları; kullanıcı profili değildir.
        MANIFEST.write_text(json.dumps(configured), encoding="utf-8")
        env = {**os.environ, **configured}
        code = subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, env=env)
        if not code and (args.desktop or args.real_mic):
            for script in ("connection_smoke.py", "desktop_smoke.py"):
                code = subprocess.call([sys.executable, str(ROOT / "tests" / script)], cwd=ROOT, env=env)
                if code:
                    break
        if not code and args.real_mic:
            code = subprocess.call([sys.executable, str(ROOT / "tests/desktop_smoke.py"), "--real-mic"], cwd=ROOT, env=env)
        return code
    finally:
        if not args.keep:
            stop()
            MANIFEST.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
