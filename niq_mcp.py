"""
NIQ MCP sunucusu: agent'lara (Claude Desktop, Claude Code, Cursor vb.) güvenli veritabanı erişimi.

Agent ham bağlantı almaz. NIQ'nun katmanlarını kullanır: salt-okunur doğrulanmış SQL, iş sözlüğü, tablo notları,
doğrulanmış örnekler. Bağlantılar yalnızca kullanıcının daha önce NIQ'da kaydettiği profillerdir; parola agent'a hiç
gösterilmez ve agent yeni bağlantı, zamanlama veya bildirim kanalı kuramaz.

Çalıştırma (stdio):   python niq_mcp.py
Claude Desktop için claude_desktop_config.json:
  {"mcpServers": {"niq": {"command": "C:\\\\yol\\\\NIQ\\\\venv\\\\Scripts\\\\python.exe", "args": ["C:\\\\yol\\\\NIQ\\\\niq_mcp.py"]}}}
"""
import functools
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer

from backend.core.utils import ValidationError, setup_logging
from backend.services.agent_api import AgentAPI

INSTRUCTIONS = """NIQ gives you safe, read-only access to the user's databases through saved connections.
Workflow: list_connections -> list_tables -> describe_tables -> ask (natural language) or run_sql (your own SELECT).
Always call get_glossary first when the question uses business terms: it holds the user's definitions
(e.g. what counts as an 'active customer'); follow them exactly. Queries are validated (single SELECT only), run in a
read-only session and row-limited. If ask or run_sql returns an error, read it and retry with a corrected query.
You cannot create connections, schedules or notification channels; ask the user to do that in NIQ."""


def build_server(api: Optional[AgentAPI] = None) -> MCPServer:
    api = api or AgentAPI()
    server = MCPServer("niq", instructions=INSTRUCTIONS)

    def guarded(fn):
        """Doğrulama hatalarını agent'ın okuyup düzeltebileceği sonuçlara çevirir."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            try:
                return fn(*args, **kwargs)
            except ValidationError as exc:
                return {"success": False, "error": str(exc)}
            except Exception as exc:  # bağlantı/tünel hatası gibi
                return {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        return wrapper

    @server.tool()
    def status() -> Dict[str, Any]:
        """NIQ durumu: kayıtlı bağlantı sayısı, AI sağlayıcısı hazır mı, rapor sayısı."""
        return api.status()

    @server.tool()
    def list_connections() -> List[Dict[str, Any]]:
        """Kullanıcının NIQ'da kayıtlı veritabanı bağlantıları (parolasız). Diğer araçlar bu adları kullanır."""
        return api.list_connections()

    @server.tool()
    @guarded
    def list_tables(connection: str) -> Dict[str, Any]:
        """Bağlantıdaki tabloları ve varsa kullanıcının yazdığı tablo notlarını listeler."""
        return api.list_tables(connection)

    @server.tool()
    @guarded
    def describe_tables(connection: str, tables: List[str]) -> Dict[str, Any]:
        """Tabloların kolonlarını (tip), birincil/yabancı anahtarlarını, notlarını ve bilinen kategori değerlerini verir."""
        return api.describe_tables(connection, tables)

    @server.tool()
    @guarded
    def get_glossary(connection: str) -> Dict[str, Any]:
        """Kullanıcının iş sözlüğü ve tablo notları: iş terimlerinin (aktif müşteri, net ciro) kesin tanımları. Önce bunu oku."""
        return api.get_glossary(connection)

    @server.tool()
    @guarded
    def ask(connection: str, question: str, tables: Optional[List[str]] = None) -> Dict[str, Any]:
        """Doğal dil sorusunu cevaplar: NIQ tabloyu seçer, sözlük ve doğrulanmış örneklerle SQL üretir, doğrular, salt-okunur çalıştırır."""
        return api.ask(connection, question, tables)

    @server.tool()
    @guarded
    def run_sql(connection: str, sql: str) -> Dict[str, Any]:
        """Kendi yazdığın tek bir SELECT'i (CTE/UNION serbest) doğrulanmış salt-okunur oturumda çalıştırır. Yazma ve çoklu ifade reddedilir."""
        return api.run_sql(connection, sql)

    @server.tool()
    @guarded
    def add_glossary_term(connection: str, term: str, definition: str, sql_hint: str = "") -> Dict[str, Any]:
        """Kullanıcının söylediği bir iş tanımını sözlüğe ekler (yalnızca kullanıcı açıkça tanımladıysa; tahmin yazma)."""
        return api.add_glossary_term(connection, term, definition, sql_hint)

    @server.tool()
    def list_reports() -> List[Dict[str, Any]]:
        """Kullanıcının zamanlanmış rapor ve uyarıları ve son durumları."""
        return api.list_reports()

    @server.tool()
    def recent_results(limit: int = 10) -> List[Dict[str, Any]]:
        """Zamanlanmış rapor/uyarıların son çalışma sonuçları (gelen kutusu)."""
        return api.recent_results(limit)

    @server.resource("niq://connections")
    def connections_resource() -> str:
        """Kayıtlı bağlantı adları."""
        import json
        return json.dumps(api.list_connections(), ensure_ascii=False)

    return server


def main() -> None:
    setup_logging("WARNING")
    build_server().run()


if __name__ == "__main__":
    main()
