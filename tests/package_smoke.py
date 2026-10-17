"""Çalışan .exe API'sinde gerçek AI -> SQL doğrulama -> DB akışını sınar.

Önce local_databases.py --keep ve connection_smoke.py çalıştırılmalı.
Ardından API anahtarı ortamında açılmış .exe'nin yerel portunu ver:
python tests/package_smoke.py --port PORT
Yalnızca üretilmiş niq_it test DB'lerine bağlanır; sonunda demo DB'ye döner.
"""
import argparse
import json
import tempfile
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    origin = f"http://127.0.0.1:{args.port}/api"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    passed = []

    def request(path, body=None):
        req = urllib.request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=90) as response:
            result = json.load(response)
        assert result.get("success"), f"API başarısız: {path}"
        return result.get("data", result)

    def ask(question, expected):
        result = request("/query", {"question": question, "mode": "query"})
        assert result.get("success"), "Sorgu başarısız"
        rows = result.get("results", [])
        assert len(rows) == 1 and expected in rows[0].values(), "Beklenen sentetik sonuç alınamadı"

    try:
        health = request("/health")
        assert health["ai_service_available"], "Paket mevcut bir API anahtarı ortamında açılmalı"
        request("/db/demo", {})
        ask("How many customers are there?", 6)
        passed.append("SQLITE_REAL_AI")
        manifest = json.loads((Path(tempfile.gettempdir()) / "niq-local-test-servers.json").read_text())
        for key, raw in manifest.items():
            host, port, database, user, password = raw.split(":", 4)
            assert host == "127.0.0.1" and database == "niq_it", "Yalnızca yerel test DB'si kullanılabilir"
            dialect = "postgresql" if "POSTGRES" in key else "mssql" if "MSSQL" in key else "mysql"
            request("/connect", {"type": dialect, "host": host, "port": int(port), "database": database,
                                 "user": user, "password": password, "name": "Paket kontrolü"})
            ask("How many rows are in smoke_orders?", 3)
            passed.append(key.removeprefix("NIQ_IT_") + "_REAL_AI")
        print(json.dumps({"passed": True, "checks": passed}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"passed": False, "checks": passed, "error": str(exc)}), flush=True)
        return 1
    finally:
        request("/db/demo", {})


if __name__ == "__main__":
    raise SystemExit(main())
