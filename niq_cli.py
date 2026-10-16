"""
NIQ komut satırı: agent'lar ve betikler için JSON çıktılı, etkileşimsiz arayüz.

Tüm komutlar stdout'a tek bir JSON yazar. Çıkış kodları:
  0 başarılı, 1 hata, 2 kullanım hatası, 3 kullanıcı onayı gerekli (kritik adım).

Okuma, keşif, teşhis ve salt-okunur sorgu onaysızdır. Profil kaydetme/silme gibi
kritik adımlar `--yes` olmadan çalışmaz; agent bu durumda kullanıcıdan onay alıp
komutu `--yes` ile yeniden çalıştırmalıdır. Parola asla argüman olarak verilmez:
`--password-env DEGISKEN` (varsayılan NIQ_DB_PASSWORD) veya `--password-stdin`.

Örnek akış (AGENTS.md'ye bakın):
  python niq_cli.py discover
  python niq_cli.py parse "postgresql://u:p@host/db"
  python niq_cli.py diagnose --input "postgresql://u@host/db" --password-env PGPASS
  python niq_cli.py connections save --input "postgresql://u@host/db" --name uretim --password-env PGPASS --yes
  python niq_cli.py tables --connection uretim
  python niq_cli.py ask --connection uretim "geçen ayın toplam satışı"
"""
import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from backend.config.config import PROVIDERS, Settings
from backend.core.approval import ApprovalRequired, require_approval
from backend.core.utils import ValidationError, setup_logging
from backend.db import diagnostics
from backend.db.connector import Connector, friendly_error
from backend.db.dialects import DIALECTS
from backend.db.profiles import (
    Profile,
    ProfileStore,
    get_password,
    parse_connection_input,
    set_password,
    set_ssh_secret,
)
from backend.db.tunnel import HostKeyUnknown
from backend.services.ai_service import AIService, AIServiceError, AIUnavailableError
from backend.services.history import HistoryStore
from backend.services.jobs import JobStore, Scheduler, run_job, wait_forever
from backend.services.knowledge import KnowledgeStore
from backend.services.query_service import run_question, run_sql
from backend.services.schedule import parse_schedule

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_APPROVAL = 0, 1, 2, 3


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR, extra: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.extra = extra or {}


def emit(payload: Dict[str, Any], code: int = EXIT_OK) -> int:
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return code


# --- profil argümanları -------------------------------------------------------------
def add_profile_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("bağlantı (üçünden biri)")
    group.add_argument("--connection", "-c", help="kayıtlı profil adı veya kimliği")
    group.add_argument("--input", help="URL, ODBC/ADO/JDBC dizesi veya SQLite dosya yolu")
    group.add_argument("--type", choices=sorted(DIALECTS), help="veritabanı türü")
    group.add_argument("--host")
    group.add_argument("--port", type=int)
    group.add_argument("--database", "-d")
    group.add_argument("--user", "-u")
    group.add_argument("--name", help="profil adı (kaydederken)")
    group.add_argument("--password-env", default="NIQ_DB_PASSWORD", help="parolanın okunacağı ortam değişkeni")
    group.add_argument("--password-stdin", action="store_true", help="parolayı stdin'den oku")
    group.add_argument("--auth", choices=["sql", "windows"], help="SQL Server: windows = Windows girişi (parola gerekmez)")
    group.add_argument("--ssl", choices=["disable", "require", "verify"], help="şifreli bağlantı modu")
    group.add_argument("--ssl-ca", help="sertifika yetkilisi dosyası (verify için)")
    group.add_argument("--ssh-host", help="SSH tüneli: atlama sunucusu")
    group.add_argument("--ssh-port", type=int)
    group.add_argument("--ssh-user")
    group.add_argument("--ssh-key", help="SSH özel anahtar dosyası")
    group.add_argument("--ssh-password-env", default="NIQ_SSH_PASSWORD", help="SSH parolası/anahtar parolası ortam değişkeni")
    group.add_argument("--yes", action="store_true", help="kritik adımı onayla (profil kaydet/sil, yeni SSH sunucu anahtarına güven)")


def resolve_profile(args: argparse.Namespace, store: ProfileStore) -> Tuple[Profile, Optional[str], Optional[str]]:
    """Argümanlardan (profil, parola, ssh_sırrı) üretir. Kayıtlı profilde ikisi de None (keyring'den okunur)."""
    if args.connection:
        profile = store.get(args.connection)
        if not profile:
            raise CliError(f"Profil bulunamadı: {args.connection}. 'connections list' ile bakın.")
        return profile, None, None

    fields: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    password = ""
    if args.input:
        parsed = parse_connection_input(args.input)
        if not parsed.get("recognized"):
            raise CliError("--input tanınmadı. URL, ODBC dizesi veya dosya yolu verin.", EXIT_USAGE)
        password = parsed.pop("password", "")
        parsed.pop("recognized", None)
        options.update(parsed.pop("options", None) or {})
        fields.update({k: v for k, v in parsed.items() if v not in (None, "")})
    for key in ("type", "host", "port", "database", "user", "name"):
        value = getattr(args, key, None)
        if value not in (None, ""):
            fields[key] = value
    for arg_name, option in (("auth", "auth"), ("ssl", "ssl"), ("ssl_ca", "ssl_ca"), ("ssh_host", "ssh_host"),
                             ("ssh_port", "ssh_port"), ("ssh_user", "ssh_user"), ("ssh_key", "ssh_key")):
        value = getattr(args, arg_name, None)
        if value not in (None, ""):
            options[option] = value
    fields["options"] = options
    if "type" not in fields:
        raise CliError("Bağlantı belirtilmedi: --connection, --input veya --type kullanın.", EXIT_USAGE)

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    elif not password:
        password = os.environ.get(args.password_env, "")
    ssh_secret = os.environ.get(args.ssh_password_env, "") or None
    try:
        return Profile.from_dict(fields), password, ssh_secret
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc


def open_connector(args: argparse.Namespace, store: ProfileStore) -> Connector:
    profile, password, ssh_secret = resolve_profile(args, store)
    return Connector(profile, password=password if password is not None else None,
                     ssh_secret=ssh_secret, trust_ssh_host=bool(args.yes))


# --- komutlar -------------------------------------------------------------------------
def cmd_discover(args, store):
    return {"success": True, "data": diagnostics.discover()}


def cmd_types(args, store):
    return {"success": True, "data": [d.public() for d in DIALECTS.values()]}


def cmd_parse(args, store):
    parsed = parse_connection_input(args.text)
    parsed["has_password"] = bool(parsed.pop("password", ""))
    return {"success": parsed.get("recognized", False), "data": parsed}


def cmd_diagnose(args, store):
    profile, password, ssh_secret = resolve_profile(args, store)
    if password is None:
        password = get_password(profile) or ""
    result = diagnostics.diagnose(profile, password, ssh_secret=ssh_secret, trust_ssh_host=bool(args.yes))
    return {"success": result["ok"], "data": result}


def cmd_databases(args, store):
    connector = open_connector(args, store)
    try:
        return {"success": True, "data": connector.list_databases()}
    except Exception as exc:
        raise CliError(friendly_error(exc)) from exc
    finally:
        connector.close()


def cmd_connections(args, store):
    if args.action == "list":
        return {"success": True, "data": [p.public() for p in store.list()]}
    if args.action == "save":
        profile, password, ssh_secret = resolve_profile(args, store)
        require_approval({"approved": args.yes}, "save_connection", f"{profile.name} ({profile.user or 'kullanıcısız'})")
        existing = store.get(profile.name)
        if existing:
            profile.id = existing.id
        if password:
            set_password(profile, password)
        if ssh_secret:
            set_ssh_secret(profile, ssh_secret)
        store.save(profile)
        return {"success": True, "data": profile.public(), "message": "Profil kaydedildi."}
    if args.action == "delete":
        if not args.target:
            raise CliError("Silinecek profil adını verin.", EXIT_USAGE)
        profile = store.get(args.target)
        if not profile:
            raise CliError(f"Profil bulunamadı: {args.target}")
        require_approval({"approved": args.yes}, "delete_connection", profile.name)
        store.delete(args.target)
        return {"success": True, "message": "Profil silindi."}
    raise CliError("Bilinmeyen eylem", EXIT_USAGE)


def cmd_tables(args, store):
    connector = open_connector(args, store)
    try:
        return {"success": True, "data": connector.get_tables(refresh=True)}
    except Exception as exc:
        raise CliError(friendly_error(exc)) from exc
    finally:
        connector.close()


def cmd_schema(args, store):
    connector = open_connector(args, store)
    try:
        names = [t.strip() for t in args.tables.split(",")] if args.tables else connector.get_tables()[:20]
        schema = connector.get_database_schema(names)
        data = {name: {"columns": [c.describe() for c in info.columns], "primary_key": info.primary_key,
                       "foreign_keys": [f"{c} -> {t}.{r}" for c, t, r in info.foreign_keys]}
                for name, info in schema.tables.items()}
        return {"success": True, "data": data}
    except Exception as exc:
        raise CliError(friendly_error(exc)) from exc
    finally:
        connector.close()


def cmd_sql(args, store):
    connector = open_connector(args, store)
    try:
        tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
        result = run_sql(connector, args.query, tables)
        return result
    finally:
        connector.close()


def cmd_ask(args, store):
    connector = open_connector(args, store)
    history = HistoryStore()
    try:
        tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
        payload, _ = run_question(connector, AIService(), history, KnowledgeStore(), args.question, tables)
        return payload
    except (AIUnavailableError, AIServiceError) as exc:
        raise CliError(str(exc)) from exc
    finally:
        connector.close()


def _alert_condition(text: Optional[str], column: Optional[str]) -> Optional[Dict[str, Any]]:
    """'rows>0', '>5', '<=0.2' gibi metni koşula çevirir."""
    if not text:
        return None
    text = text.strip().replace(" ", "")
    if text in ("rows>0", "rows==0"):
        return {"op": text}
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if text.startswith(op):
            try:
                condition: Dict[str, Any] = {"op": op, "value": float(text[len(op):])}
            except ValueError:
                break
            if column:
                condition["column"] = column
            return condition
    raise CliError("--alert biçimi: rows>0, rows==0 veya >5, <=0.2 gibi bir karşılaştırma", EXIT_USAGE)


def cmd_jobs(args, store):
    jobs = JobStore()
    if args.action == "list":
        return {"success": True, "data": jobs.list()}
    if args.action == "runs":
        return {"success": True, "data": jobs.runs(limit=20, job_id=args.target and int(args.target))}
    if args.action in ("run", "delete"):
        if not args.target or not args.target.isdigit():
            raise CliError("İş numarasını verin.", EXIT_USAGE)
        job = jobs.get(int(args.target))
        if not job:
            raise CliError(f"İş bulunamadı: {args.target}")
        if args.action == "delete":
            require_approval({"approved": args.yes}, "delete_job", job["name"])
            jobs.delete(job["id"])
            return {"success": True, "message": "İş silindi."}
        return {"success": True, "data": run_job(job, jobs, store)}
    if args.action == "run-due":
        return {"success": True, "data": Scheduler(jobs, store).tick()}
    if args.action == "serve":
        wait_forever(Scheduler(jobs, store))
        return {"success": True}
    # add
    if not args.connection:
        raise CliError("Zamanlanacak iş için kayıtlı bir profil gerekir: --connection ad", EXIT_USAGE)
    schedule = parse_schedule(args.schedule or "")
    if not schedule:
        raise CliError("--schedule anlaşılamadı. Örnek: \"her pazartesi 09:00\", \"her gün 08:30\", \"her 30 dakikada\"", EXIT_USAGE)
    condition = _alert_condition(args.alert, args.column)
    connector = open_connector(args, store)
    try:
        sql = args.sql
        if not sql:
            if not args.question:
                raise CliError("--question veya --sql verin.", EXIT_USAGE)
            payload, _ = run_question(connector, AIService(), HistoryStore(), KnowledgeStore(), args.question, None)
            if not payload.get("success"):
                raise CliError(payload.get("error", "Soru çalışmadı"))
            sql = payload["sql"]
        require_approval({"approved": args.yes}, "create_schedule", args.name or args.question or sql[:40])
        job_id = jobs.create(
            name=args.name or (args.question or "Rapor")[:60], kind="alert" if condition else "report",
            profile_id=connector.profile.id, question=args.question or "", sql=sql, schedule=schedule,
            condition=condition, channels=[c for c in (args.channels or "inbox").split(",") if c])
        return {"success": True, "data": jobs.get(job_id), "sql": sql}
    finally:
        connector.close()


def cmd_ai(args, store):
    config = Settings.ai_config()
    status = {"provider": config["provider"], "model": config["model"], "has_key": bool(config["api_key"]),
              "providers": {k: {"label": v["label"], "has_key": Settings.has_api_key(k)} for k, v in PROVIDERS.items()}}
    if args.action == "test":
        try:
            status["ping"] = AIService().ping()
        except (AIUnavailableError, AIServiceError) as exc:
            raise CliError(str(exc), extra={"data": status}) from exc
    return {"success": True, "data": status}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="niq", description="NIQ: doğal dille veritabanı sorgusu (agent arayüzü, JSON çıktı)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("types", help="desteklenen veritabanı türleri").set_defaults(func=cmd_types)
    sub.add_parser("discover", help="yüklü sürücüler ve yerel sunucular (onaysız)").set_defaults(func=cmd_discover)

    p = sub.add_parser("parse", help="bağlantı metninden alanları çıkar")
    p.add_argument("text")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("diagnose", help="bağlantıyı adım adım sına; hiçbir şey kaydetmez (onaysız)")
    add_profile_args(p)
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("databases", help="sunucudaki veritabanlarını listele")
    add_profile_args(p)
    p.set_defaults(func=cmd_databases)

    p = sub.add_parser("connections", help="profil listele / kaydet / sil")
    p.add_argument("action", choices=["list", "save", "delete"])
    p.add_argument("target", nargs="?", help="delete için profil adı")
    add_profile_args(p)
    p.set_defaults(func=cmd_connections)

    p = sub.add_parser("tables", help="tabloları listele")
    add_profile_args(p)
    p.set_defaults(func=cmd_tables)

    p = sub.add_parser("schema", help="tablo şemaları (kolon tipi, PK, FK)")
    add_profile_args(p)
    p.add_argument("--tables", help="virgülle ayrılmış tablolar (boşsa ilk 20)")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("sql", help="elle yazılmış SELECT'i guard ve salt-okunur modda çalıştır")
    add_profile_args(p)
    p.add_argument("query")
    p.add_argument("--tables", help="izin verilen tablolar (boşsa hepsi)")
    p.set_defaults(func=cmd_sql)

    p = sub.add_parser("ask", help="doğal dil sorusu -> SQL -> sonuç (AI anahtarı gerekir)")
    add_profile_args(p)
    p.add_argument("question")
    p.add_argument("--tables", help="virgülle ayrılmış tablolar (boşsa otomatik seçilir)")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("jobs", help="zamanlanmış rapor ve uyarılar (list, add, run, run-due, serve, delete, runs)")
    p.add_argument("action", choices=["list", "add", "run", "run-due", "serve", "delete", "runs"])
    p.add_argument("target", nargs="?", help="run/delete/runs için iş numarası")
    add_profile_args(p)
    p.add_argument("--question", help="add: zamanlanacak soru (SQL AI ile üretilir)")
    p.add_argument("--sql", help="add: doğrudan SELECT (AI kullanılmaz)")
    p.add_argument("--schedule", help="add: 'her pazartesi 09:00', 'her gün 08:30', 'her 30 dakikada'")
    p.add_argument("--alert", help="add: koşul (rows>0, >5, <=0.2); verilirse iş uyarı olur")
    p.add_argument("--column", help="add: koşulun uygulanacağı kolon (boşsa ilk sayısal değer)")
    p.add_argument("--channels", help="add: inbox,telegram,webhook (virgülle)")
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("ai", help="AI sağlayıcı durumu / test")
    p.add_argument("action", choices=["status", "test"], nargs="?", default="status")
    p.set_defaults(func=cmd_ai)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    setup_logging("WARNING")
    parser = build_parser()
    args = parser.parse_args(argv)
    store = ProfileStore()
    try:
        result = args.func(args, store)
        return emit(result, EXIT_OK if result.get("success", True) else EXIT_ERROR)
    except ApprovalRequired as exc:
        return emit({"success": False, "needs_approval": True, "action": exc.action, "summary": exc.summary,
                     "rerun_with": "--yes", "error": "Kritik adım: kullanıcıdan onay alın, sonra --yes ile tekrar çalıştırın."},
                    EXIT_APPROVAL)
    except CliError as exc:
        return emit({"success": False, "error": str(exc), **exc.extra}, exc.code)
    except HostKeyUnknown as exc:
        return emit({"success": False, "needs_approval": True, "action": "trust_ssh_host", "summary": str(exc),
                     "rerun_with": "--yes",
                     "error": "SSH sunucu anahtarı ilk kez görülüyor: parmak izini kullanıcıya gösterin, onay alınırsa --yes ile tekrar çalıştırın."},
                    EXIT_APPROVAL)
    except ValidationError as exc:
        return emit({"success": False, "error": str(exc)}, EXIT_ERROR)


if __name__ == "__main__":
    sys.exit(main())
