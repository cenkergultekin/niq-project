"""Windows girişi, SSL seçenekleri, sorgu zaman aşımı ve SSH tüneli (gerçek bir test SSH sunucusuyla)."""
import socket
import socketserver
import sqlite3
import threading

import paramiko
import pytest

from backend.config.config import Config
from backend.db import diagnostics
from backend.db.connector import Connector, apply_query_timeout, friendly_error
from backend.db.dialects import build_url
from backend.db.profiles import Profile, parse_connection_input
from backend.db.tunnel import HostKeyChanged, HostKeyUnknown, KnownHosts, SSHTunnel, SSHTunnelError, fingerprint
from backend.routes import routes


# --- Windows girişi, SSL --------------------------------------------------------------
def odbc_part(url: str) -> str:
    from urllib.parse import unquote_plus
    return unquote_plus(url.split("odbc_connect=")[1])


def test_windows_auth_url_has_no_credentials():
    odbc = odbc_part(build_url("mssql", "srv", 1433, "db", "", "", {"auth": "windows"}))
    assert "Trusted_Connection=yes" in odbc and "UID=" not in odbc and "PWD=" not in odbc


def test_sql_auth_url_has_credentials():
    odbc = odbc_part(build_url("mssql", "srv", 1433, "db", "sa", "p}w", {}))
    assert "UID={sa}" in odbc and "PWD={p}}w}" in odbc and "Trusted_Connection" not in odbc


@pytest.mark.parametrize("ssl, expected", [
    ("disable", ["Encrypt=no"]),
    ("require", ["Encrypt=yes", "TrustServerCertificate=yes"]),
    ("verify", ["Encrypt=yes", "TrustServerCertificate=no"]),
])
def test_mssql_ssl_modes(ssl, expected):
    odbc = odbc_part(build_url("mssql", "srv", 1433, "db", "u", "p", {"ssl": ssl}))
    assert all(part in odbc for part in expected)


def test_postgres_ssl_modes():
    assert build_url("postgresql", "h", 5432, "d", "u", "p", {"ssl": "require"}).endswith("?sslmode=require")
    verify = build_url("postgresql", "h", 5432, "d", "u", "p", {"ssl": "verify", "ssl_ca": "C:/ca.pem"})
    assert "sslmode=verify-full" in verify and "sslrootcert=C%3A%2Fca.pem" in verify
    assert "sslmode" not in build_url("postgresql", "h", 5432, "d", "u", "p", {})


def test_profile_option_validation():
    assert Profile(type="mssql", database="d", options={"auth": "windows"}).uses_windows_auth
    with pytest.raises(ValueError):
        Profile(type="postgresql", database="d", options={"auth": "windows"})
    with pytest.raises(ValueError):
        Profile(type="mssql", database="d", options={"ssl": "maybe"})
    with pytest.raises(ValueError):
        Profile(type="sqlite", database="/x.db", options={"ssh_host": "jump"})
    clean = Profile(type="mssql", database="d", options={"bogus": "x", "ssh_host": "jump"}).options
    assert clean == {"ssh_host": "jump"}


def test_parse_windows_auth_strings():
    ado = parse_connection_input("Server=srv;Database=Nw;Integrated Security=SSPI;")
    assert ado["options"] == {"auth": "windows"} and ado["type"] == "mssql"
    odbc = parse_connection_input("Driver={ODBC Driver 18 for SQL Server};Server=srv;Database=Nw;Trusted_Connection=yes;")
    assert odbc["options"]["auth"] == "windows"
    jdbc = parse_connection_input("jdbc:sqlserver://srv:1433;databaseName=Nw;integratedSecurity=true")
    assert jdbc["options"] == {"auth": "windows"}


# --- zaman aşımı ----------------------------------------------------------------------
def test_mssql_query_timeout_applied_to_driver_connection():
    class FakeConn:
        timeout = 0
    conn = FakeConn()
    apply_query_timeout("mssql", conn, 17)
    assert conn.timeout == 17


def test_sqlite_query_timeout_aborts_runaway_query(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr(Config, "QUERY_TIMEOUT", 1)
    connector = Connector(Profile(type="sqlite", database=str(path)))
    result = connector.execute_readonly(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c", 10)
    assert not result.is_successful and "saniye sınırını aştı" in result.error


def test_friendly_timeout_messages():
    assert "saniye" in friendly_error(Exception("('HYT00', '[HYT00] [Microsoft][ODBC Driver 18] Query timeout expired (0)')"))
    assert "saniye" in friendly_error(Exception("canceling statement due to statement timeout"))
    assert "saniye" not in friendly_error(Exception("Login timeout expired"))


# --- SSH tüneli: gerçek paramiko sunucusu ---------------------------------------------
class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(4096):
            self.request.sendall(data.upper())


class FakeSSHServer(paramiko.ServerInterface):
    def __init__(self):
        self.destinations = {}

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("tunnel", "secret") else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.destinations[chanid] = destination
        return paramiko.OPEN_SUCCEEDED


def pump(channel, target):
    try:
        while True:
            data = channel.recv(4096)
            if not data:
                break
            target.sendall(data)
    except OSError:
        pass


def handle_connection(client, host_key, iface):
    """Her bağlantı kendi iş parçacığında: istemci el sıkışmanın ortasında koparırsa (host anahtarı reddi gibi)
    kabul döngüsü ölmesin."""
    try:
        transport = paramiko.Transport(client)
        transport.add_server_key(host_key)
        transport.start_server(server=iface)
        while True:
            channel = transport.accept(timeout=30)
            if channel is None:
                return
            host, port = iface.destinations[channel.get_id()]
            target = socket.create_connection((host, port))
            threading.Thread(target=pump, args=(channel, target), daemon=True).start()

            def back(ch=channel, tg=target):
                try:
                    while data := tg.recv(4096):
                        ch.sendall(data)
                except OSError:
                    pass
            threading.Thread(target=back, daemon=True).start()
    except (paramiko.SSHException, OSError, EOFError):
        return


def serve_ssh(listener, host_key, iface):
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=handle_connection, args=(client, host_key, iface), daemon=True).start()


@pytest.fixture(scope="module")
def ssh_env():
    echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoHandler)
    echo.daemon_threads = True
    threading.Thread(target=echo.serve_forever, daemon=True).start()

    host_key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    threading.Thread(target=serve_ssh, args=(listener, host_key, FakeSSHServer()), daemon=True).start()
    yield {"ssh_port": listener.getsockname()[1], "echo_port": echo.server_address[1], "host_key": host_key}
    listener.close()
    echo.shutdown()


def make_tunnel(env, tmp_path, secret="secret", trust=False, user="tunnel"):
    return SSHTunnel("127.0.0.1", env["ssh_port"], user, "127.0.0.1", env["echo_port"], secret=secret,
                     trust_new_host=trust, known_hosts=KnownHosts(tmp_path / "kh.json"))


def echo_through(port):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(b"merhaba")
        return sock.recv(100)


def test_unknown_ssh_host_requires_approval_then_is_remembered(ssh_env, tmp_path):
    expected = fingerprint(ssh_env["host_key"])
    tunnel = make_tunnel(ssh_env, tmp_path)
    with pytest.raises(HostKeyUnknown) as info:
        tunnel.start()
    assert info.value.fingerprint == expected and expected.startswith("SHA256:")

    trusted = make_tunnel(ssh_env, tmp_path, trust=True)
    port = trusted.start()
    assert echo_through(port) == b"MERHABA"  # veri tünelden geçti
    trusted.close()

    remembered = make_tunnel(ssh_env, tmp_path)  # onay istemeden
    assert echo_through(remembered.start()) == b"MERHABA"
    remembered.close()


def test_changed_ssh_host_key_is_refused(ssh_env, tmp_path):
    known = KnownHosts(tmp_path / "kh.json")
    known.add("127.0.0.1", ssh_env["ssh_port"], "SHA256:eski-ve-farkli")
    with pytest.raises(HostKeyChanged):
        make_tunnel(ssh_env, tmp_path, trust=True).start()  # güven bayrağı bile değişimi geçirmez


def test_wrong_ssh_password(ssh_env, tmp_path):
    with pytest.raises(SSHTunnelError, match="kimlik doğrulaması"):
        make_tunnel(ssh_env, tmp_path, secret="yanlis", trust=True).start()


def test_connector_builds_url_through_tunnel(ssh_env, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.tunnel.KnownHosts", lambda: KnownHosts(tmp_path / "kh.json"))
    profile = Profile(type="postgresql", host="127.0.0.1", port=ssh_env["echo_port"], database="d", user="u", options={
        "ssh_host": "127.0.0.1", "ssh_port": str(ssh_env["ssh_port"]), "ssh_user": "tunnel"})
    connector = Connector(profile, password="pw", ssh_secret="secret", trust_ssh_host=True)
    try:
        url = connector._url()
        assert f"@127.0.0.1:{connector._tunnel.local_port}/d" in url and str(ssh_env["echo_port"]) not in url
    finally:
        connector.close()
    assert connector._tunnel is None


def test_named_instance_cannot_be_tunnelled(tmp_path):
    profile = Profile(type="mssql", host=".\\SQLEXPRESS", database="d", options={"ssh_host": "jump"})
    with pytest.raises(SSHTunnelError, match="Adlandırılmış"):
        Connector(profile).open_tunnel()


def test_diagnose_reports_ssh_step_and_api_asks_approval(ssh_env, tmp_path, monkeypatch, client):
    monkeypatch.setattr("backend.db.tunnel.KnownHosts", lambda: KnownHosts(tmp_path / "kh.json"))
    monkeypatch.setattr("backend.db.tunnel.KnownHosts.__init__", lambda self, path=None: setattr(self, "path", tmp_path / "kh2.json"))
    body = {"type": "postgresql", "host": "127.0.0.1", "port": ssh_env["echo_port"], "database": "d", "user": "u",
            "password": "p", "ssh_host": "127.0.0.1", "ssh_port": ssh_env["ssh_port"], "ssh_user": "tunnel",
            "ssh_password": "secret"}
    denied = client.post("/api/db/diagnose", json=body)
    assert denied.status_code == 409
    assert denied.get_json()["action"] == "trust_ssh_host" and "SHA256:" in denied.get_json()["summary"]

    accepted = client.post("/api/db/diagnose", json={**body, "approved": True})
    assert accepted.status_code == 200
    steps = {s["id"]: s for s in accepted.get_json()["data"]["steps"]}
    assert steps["ssh"]["status"] == "ok" and steps["network"]["status"] == "skip"
    assert steps["login"]["status"] == "fail"  # hedef PostgreSQL değil, yankı sunucusu: tünel çalıştı, giriş başarısız

    assert routes.state.connector is None
    assert diagnostics  # modül yüklü


def test_mariadb_unknown_variable_is_not_reported_as_timeout():
    message = friendly_error(Exception("(1193, \"Unknown system variable 'max_execution_time'\")"))
    assert "saniye" not in message and "Unknown system variable" in message
    assert "saniye" in friendly_error(Exception("Query execution was interrupted"))
