"""
SSH tüneli: veritabanı sunucusuna doğrudan erişilemiyorsa (yalnızca bastion/jump host üzerinden
açıksa) yerel bir porttan SSH üzerinden yönlendirme yapar.

Güven modeli: SSH sunucu anahtarı ilk görüldüğünde kullanıcıya parmak izi gösterilip onay istenir
(`HostKeyUnknown`, kritik adım). Onaylanan parmak izi `known_hosts.json` içinde saklanır; sonradan
değişirse bağlantı reddedilir (`HostKeyChanged`), bu onaylanabilir bir durum değildir.
"""
import base64
import hashlib
import json
import logging
import select
import socketserver
import threading
from pathlib import Path
from typing import Dict, Optional

import paramiko

from backend.config.config import data_dir

logger = logging.getLogger(__name__)


class SSHTunnelError(Exception):
    """Kullanıcıya gösterilebilir tünel hatası."""


class HostKeyUnknown(SSHTunnelError):
    def __init__(self, host: str, port: int, fingerprint: str):
        super().__init__(f"SSH sunucusu {host}:{port} ilk kez görülüyor. Parmak izi: {fingerprint}")
        self.host, self.port, self.fingerprint = host, port, fingerprint


class HostKeyChanged(SSHTunnelError):
    pass


def fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class KnownHosts:
    """Onaylanmış SSH sunucu parmak izleri (%APPDATA%/NIQ/known_hosts.json)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else data_dir() / "known_hosts.json"

    def _read(self) -> Dict[str, str]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, host: str, port: int) -> Optional[str]:
        return self._read().get(f"{host}:{port}")

    def add(self, host: str, port: int, fp: str) -> None:
        data = self._read()
        data[f"{host}:{port}"] = fp
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class _PinPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, known: KnownHosts, host: str, port: int, trust_new: bool):
        self.known, self.host, self.port, self.trust_new = known, host, port, trust_new

    def missing_host_key(self, client, hostname, key):
        # paramiko standart dışı portlarda hostname'i "[host]:port" verir; tutarlılık için kendi host adımızı kullanırız
        hostname = self.host
        fp = fingerprint(key)
        saved = self.known.get(hostname, self.port)
        if saved == fp:
            return
        if saved is not None:
            raise HostKeyChanged(
                f"{hostname} SSH sunucu anahtarı değişti (beklenen {saved}, gelen {fp}). "
                "Bu bir ortadaki adam saldırısı olabilir; sunucu yöneticisine danışın."
            )
        if not self.trust_new:
            raise HostKeyUnknown(hostname, self.port, fp)
        self.known.add(hostname, self.port, fp)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server: "_Server" = self.server  # type: ignore[assignment]
        try:
            channel = server.transport.open_channel(
                "direct-tcpip", (server.remote_host, server.remote_port), self.request.getpeername())
        except Exception as exc:
            logger.warning("SSH kanalı açılamadı: %s", exc)
            return
        try:
            while True:
                readable, _, _ = select.select([self.request, channel], [], [], 30)
                if self.request in readable:
                    data = self.request.recv(32768)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(32768)
                    if not data:
                        break
                    self.request.sendall(data)
        finally:
            channel.close()
            self.request.close()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, transport, remote_host: str, remote_port: int):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.transport, self.remote_host, self.remote_port = transport, remote_host, remote_port


class SSHTunnel:
    """127.0.0.1:<yerel port> -> SSH sunucusu üzerinden -> remote_host:remote_port"""

    def __init__(self, ssh_host: str, ssh_port: int, ssh_user: str, remote_host: str, remote_port: int,
                 key_path: Optional[str] = None, secret: Optional[str] = None,
                 trust_new_host: bool = False, known_hosts: Optional[KnownHosts] = None):
        self.ssh_host, self.ssh_port, self.ssh_user = ssh_host, int(ssh_port or 22), ssh_user
        self.remote_host, self.remote_port = remote_host, int(remote_port)
        self.key_path, self.secret = key_path or None, secret or None
        self.trust_new_host = trust_new_host
        self.known_hosts = known_hosts or KnownHosts()
        self._client: Optional[paramiko.SSHClient] = None
        self._server: Optional[_Server] = None
        self.local_port: Optional[int] = None

    def start(self) -> int:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_PinPolicy(self.known_hosts, self.ssh_host, self.ssh_port, self.trust_new_host))
        use_defaults = not (self.key_path or self.secret)
        try:
            client.connect(
                self.ssh_host, port=self.ssh_port, username=self.ssh_user or None,
                key_filename=self.key_path, passphrase=self.secret if self.key_path else None,
                password=self.secret if not self.key_path else None,
                look_for_keys=use_defaults, allow_agent=use_defaults, timeout=10, banner_timeout=10, auth_timeout=10,
            )
        except SSHTunnelError:
            client.close()
            raise
        except paramiko.AuthenticationException as exc:
            client.close()
            raise SSHTunnelError("SSH kimlik doğrulaması başarısız. Kullanıcı adı, anahtar dosyası veya parolayı kontrol edin.") from exc
        except FileNotFoundError as exc:
            client.close()
            raise SSHTunnelError(f"SSH anahtar dosyası bulunamadı: {self.key_path}") from exc
        except (paramiko.SSHException, OSError) as exc:
            client.close()
            raise SSHTunnelError(f"SSH sunucusuna bağlanılamadı ({self.ssh_host}:{self.ssh_port}): {exc}") from exc

        transport = client.get_transport()
        transport.set_keepalive(30)
        self._client = client
        self._server = _Server(transport, self.remote_host, self.remote_port)
        self.local_port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        logger.info("SSH tüneli açıldı: 127.0.0.1:%s -> %s:%s via %s", self.local_port,
                    self.remote_host, self.remote_port, self.ssh_host)
        return self.local_port

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._client is not None:
            self._client.close()
            self._client = None
        self.local_port = None
