"""Qt bağlantı panelini gerçek sunucularla test eder; önce local_databases.py --keep.

Kullanıcı profillerine/anahtarlarına dokunmaz. Test tabloları yalnızca geçici niq_it DB'lerinde.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    home = tempfile.mkdtemp(prefix="niq-connect-check-")
    os.environ.update(NIQ_HOME=home, KEYRING_SERVICE="niq-test-" + Path(home).name,
                      OPENAI_API_KEY="", OPENROUTER_API_KEY="")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from sqlalchemy import create_engine, text

    import main_desktop
    from backend.db.dialects import build_url
    from backend.routes.routes import state

    manifest = json.loads((Path(tempfile.gettempdir()) / "niq-local-test-servers.json").read_text())
    servers = []
    for key, raw in manifest.items():
        host, port, database, user, password = raw.split(":", 4)
        dialect = "postgresql" if "POSTGRES" in key else "mssql" if "MSSQL" in key else "mysql"
        engine = create_engine(build_url(dialect, host, int(port), database, user, password))
        with engine.begin() as conn:
            if dialect == "mssql":
                conn.execute(text("IF OBJECT_ID('smoke_orders') IS NOT NULL DROP TABLE smoke_orders"))
            else:
                conn.execute(text("DROP TABLE IF EXISTS smoke_orders"))
            conn.execute(text("CREATE TABLE smoke_orders (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO smoke_orders VALUES(1),(2),(3)"))
        engine.dispose()
        from urllib.parse import quote
        uri = f"{dialect}://{quote(user)}:{quote(password)}@{host}:{port}/{database}"
        servers.append({"name": key.removeprefix("NIQ_IT_"), "uri": uri, "database": database})
    path = Path(home) / "smoke.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE smoke_orders(id INTEGER PRIMARY KEY); INSERT INTO smoke_orders VALUES(1),(2),(3);")
    servers.append({"name": "SQLITE", "uri": str(path), "database": str(path)})

    class AI:
        ai_available = True
        model = "local-test-double"
        config = {"provider": "local"}

        def suggest_questions(self, *args, **kwargs):
            return ["Kaç sipariş var?"]

        def generate_sql(self, *args, **kwargs):
            return {"sql": "SELECT COUNT(*) AS total FROM smoke_orders", "explanation": "Sipariş sayısı"}

    state.ai_service = AI()
    qt = QApplication(sys.argv[:1])
    window = main_desktop.NIQDesktopApp()
    window.setWindowTitle("NIQ - Local connection tests")
    window.show()
    started = time.monotonic()
    launched = False
    script = """
    (async () => {
      const results = [];
      const wait = async (check) => {
        for (let i=0;i<100;i++) { if(check()) return; await new Promise(r=>setTimeout(r,100)); }
        throw Error('UI step timeout: '+NIQ.$('db-status').textContent);
      };
      const assert = (ok, message) => { if(!ok) throw Error(message); };
      try {
        for (const server of SERVERS) {
          NIQ.connect.show();
          NIQ.$('db-status').textContent='';
          NIQ.$('db_paste').value=server.uri;
          NIQ.$('db_paste').dispatchEvent(new Event('change'));
          await wait(()=>NIQ.$('db-status').textContent.startsWith('Alanlar dolduruldu'));
          if(server.name!=='SQLITE') {
            NIQ.$('db_database').value='does_not_exist';
            NIQ.$('db_database').dispatchEvent(new Event('input'));
            NIQ.$('db-test-btn').click();
            await wait(()=>NIQ.$('db-status').textContent.startsWith('Test başarısız'));
            NIQ.$('db-list-btn').click();
            await wait(()=>NIQ.$('db-status').textContent.includes('veritabanı bulundu'));
            NIQ.$('db-list-select').value=server.database;
            NIQ.$('db-list-select').dispatchEvent(new Event('change'));
          }
          NIQ.$('db-test-btn').click();
          await wait(()=>NIQ.$('db-status').textContent.startsWith('Test başarılı'));
          NIQ.$('db-connect-btn').click();
          await wait(()=>NIQ.state.connected && NIQ.$('connect').hidden);
          assert(!NIQ.$('db_password').value && !NIQ.$('db_paste').value, 'Secret remained in form');
          await NIQ.chat.ask('Kaç sipariş var?');
          assert(NIQ.$('stream').querySelector('td')?.textContent==='3', 'Wrong SQL result');
          NIQ.connect.show();
          NIQ.$('db-save-btn').click();
          await wait(()=>NIQ.$('confirm').open);
          NIQ.$('confirm-no').click();
          await wait(()=>!NIQ.$('confirm').open);
          assert((await NIQ.api('/connections')).data.length===0, 'Unapproved profile was saved');
          NIQ.$('db-close-btn').click();
          await wait(()=>!NIQ.state.connected);
          results.push(server.name);
        }
        document.querySelector('[data-type="mssql"]').click();
        NIQ.$('db_password').value='stale'; NIQ.$('db_ssh_host').value='stale'; NIQ.$('db_driver').value='stale';
        document.querySelector('[data-type="sqlite"]').click();
        assert(!NIQ.$('db_password').value && !NIQ.$('db_ssh_host').value && !NIQ.$('db_driver').value,'Type switch kept old fields');
        window.__connectionResult={passed:true,servers:results};
      } catch(e) { window.__connectionResult={passed:false,servers:results,error:e.message}; }
    })();
    """.replace("SERVERS", json.dumps(servers))

    def observed(raw):
        nonlocal launched
        if not raw:
            return
        result = json.loads(raw)
        if result.get("ready") and not launched:
            launched = True
            window.web_view.page().runJavaScript(script)
        if result.get("result"):
            print(json.dumps(result["result"], ensure_ascii=True), flush=True)
            window.shutdown()
            qt.exit(0 if result["result"]["passed"] else 1)

    def poll():
        if time.monotonic() - started > 150:
            print('{"passed":false,"error":"Connection smoke timeout"}', flush=True)
            window.shutdown()
            qt.exit(1)
            return
        window.web_view.page().runJavaScript("JSON.stringify({ready:!!window.NIQ?.connect && "
                                            "document.querySelectorAll('#db-types button').length===4 && "
                                            "!document.getElementById('connect').hidden,result:window.__connectionResult})", observed)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(250)
    return qt.exec()


if __name__ == "__main__":
    raise SystemExit(main())
