"""İzin verilen kaynaklarda erişilebilir DB/şema envanteri. Satır verisi okunmaz."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from backend.db import diagnostics
from backend.db.connector import Connector
from backend.db.dialects import DIALECTS
from backend.db.profiles import get_password
from backend.db.sources import read_docker_connection


def collect_catalog(store, grants):
    sources = []
    unavailable = []
    # Kaydedilmiş profillerdeki hesap yalnızca mevcut OS kasasından alınır.
    for profile in store.list():
        sources.append((profile, get_password(profile) or "", profile.name))
    discovered = diagnostics.discover(store)
    for source in discovered["local_servers"]:
        try:
            if source["source"] == "docker":
                profile, password = read_docker_connection(source)
            elif source["type"] == "mssql":
                from backend.db.profiles import Profile
                profile = Profile(type="mssql", host=source["host"], port=source["port"], options={"auth": "windows"})
                password = ""
            else:
                unavailable.append({"name": source["name"], "reason": "Kayıtlı erişim bilgisi bulunamadı."})
                continue
            if not any(p.type == profile.type and p.host == profile.host and p.port == profile.port and p.user == profile.user for p, _, _ in sources):
                sources.append((profile, password, source["name"]))
        except Exception:
            unavailable.append({"name": source["name"], "reason": "Bağlantı bilgileri alınamadı."})

    def databases(source):
        profile, password, label = source
        connector = Connector(profile, password=password)
        try:
            names = [profile.database] if profile.type == "sqlite" else connector.list_databases()
            return [(replace(profile, database=name, id=profile.id if profile.uses_ssh else uuid.uuid4().hex[:10]), password, label)
                    for name in names]
        except Exception:
            return {"name": label, "reason": "Bu hesapla sunucuya erişilemedi."}
        finally:
            connector.close()

    candidates = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(databases, sources):
            if isinstance(result, dict):
                unavailable.append(result)
            else:
                candidates.extend(result)

    def inspect_database(source):
        profile, password, label = source
        connector = Connector(profile, password=password)
        try:
            tables = connector.get_tables()
            # Yalnızca gerçekten giriş ve metadata okuması yapılabilen DB'ler sunulur.
            named = replace(profile, name=f"{label} / {profile.database}")
            return {"id": grants.issue(named, password, database_locked=True), "name": profile.database,
                    "platform": profile.type, "platform_label": DIALECTS[profile.type].label, "source": label,
                    "table_count": len(tables), "recommended": bool(tables), "tables": tables}
        except Exception:
            return None
        finally:
            connector.close()

    unique = {profile.key: (profile, password, label) for profile, password, label in candidates}
    with ThreadPoolExecutor(max_workers=8) as pool:
        databases_ = [item for item in pool.map(inspect_database, unique.values()) if item]
    return {"databases": databases_, "platform_count": len({item["platform"] for item in databases_}),
            "database_count": len(databases_), "unavailable": unavailable}
