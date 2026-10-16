# AGENTS.md (NIQ)

Bu dosya, NIQ'yu kullanan veya geliştiren bir agent içindir. NIQ doğal dil sorusunu salt-okunur SQL'e çevirir; veritabanı bağlantısı hem arayüzden hem `niq_cli.py` ile kurulabilir.

## Kurallar

- Okuma, keşif, teşhis ve salt-okunur sorgu **onaysız** yapılır: `types`, `discover`, `parse`, `diagnose`, `databases`, `tables`, `schema`, `sql`, `ask`, `connections list`, `ai`.
- Kalıcı bir şey yazan **kritik adımlar** kullanıcıdan onay almadan çalıştırılmaz:
  - `connections save` (profil ve parolayı bu bilgisayara kaydeder)
  - `connections delete`
  - yeni bir SSH sunucusunun anahtarına güvenmek (`trust_ssh_host`): çıkış kodu 3 ve parmak izi döner, kullanıcıya göster
  - AI sağlayıcısına sonuç satırı göndermeyi açmak, kayıtlı API anahtarını silmek (API'de `approved: true`)
  Bu komutlar `--yes` olmadan çıkış kodu **3** ve `needs_approval: true` döner. Kullanıcıya `summary` alanını göster, onay alırsan aynı komutu `--yes` ile tekrar çalıştır.
- Parolayı komut satırı argümanına **yazma**. `--password-env DEGISKEN` (varsayılan `NIQ_DB_PASSWORD`) veya `--password-stdin` kullan. Parolayı sohbete, loga veya dosyaya yazma.
- Veritabanına yazma yoktur ve yapma: sorgular tek SELECT olarak doğrulanır, salt-okunur oturumda çalışır, her zaman rollback edilir. Mümkünse kullanıcıdan salt-okunur bir hesap iste (`permission_hint` alanı hazır komutu verir).
- Tüm komutlar stdout'a JSON yazar. Çıkış kodları: 0 başarılı, 1 hata, 2 kullanım hatası, 3 onay gerekli.

## Bir veritabanına bağlanma adımları

1. **Keşfet** (onaysız): `python niq_cli.py discover`
   Yüklü sürücüleri ve `localhost` üzerinde dinleyen sunucuları (SQL Server 1433, PostgreSQL 5432, MySQL 3306) listeler.
2. **Kullanıcının verdiği metni çöz** (onaysız): `python niq_cli.py parse "<url | odbc | jdbc | dosya yolu>"`
   Tür, sunucu, port, veritabanı ve kullanıcıyı çıkarır. Eksik olanı kullanıcıya sor. Varsayılanlar bilinir: port (1433/5432/3306), sunucu `localhost`, sürücü (yüklü en yeni ODBC).
3. **Test et** (onaysız): `python niq_cli.py diagnose --input "<metin>" --password-env NIQ_DB_PASSWORD`
   Adımlar: sürücü, ağ, giriş, tablo erişimi, yetki. Başarısız adımın `hint` alanı ne yapılacağını söyler. Veritabanı adı bilinmiyorsa: `python niq_cli.py databases --type postgresql --host H --user U`.
4. **Kaydetmeyi öner** (kritik, onay gerekir): `python niq_cli.py connections save --input "<metin>" --name uretim --password-env NIQ_DB_PASSWORD` önce `--yes`'siz çalıştır, çıkış kodu 3 ise özeti kullanıcıya göster, onaylarsa `--yes` ekle. Kaydetmek zorunlu değildir; tek seferlik işlerde `--input` ile devam et.
5. **Keşfet ve sor** (onaysız):
   - `python niq_cli.py tables --connection uretim`
   - `python niq_cli.py schema --connection uretim --tables orders,customers`
   - `python niq_cli.py ask --connection uretim "geçen ayın toplam satışı"` (`--tables` verilmezse sorudan tahmin edilir; AI anahtarı gerekir, `python niq_cli.py ai status` ile bak)
   - `python niq_cli.py sql --connection uretim "SELECT ..."` kendi SELECT'ini aynı güvenlik katmanıyla çalıştırır.

## Bağlantı seçenekleri

- SQL Server Windows girişi: `--auth windows` (kullanıcı/parola gerekmez; uygulamanın çalıştığı Windows hesabı kullanılır).
- Şifreli bağlantı: `--ssl disable|require|verify` (`verify` için PostgreSQL/MySQL'de `--ssl-ca dosya.pem`).
- SSH tüneli (veritabanı yalnızca atlama sunucusundan erişilebilirse): `--ssh-host H --ssh-user U [--ssh-port 22] [--ssh-key anahtar] [--ssh-password-env NIQ_SSH_PASSWORD]`. Sunucu anahtarı ilk kez görülünce komut çıkış kodu 3 ile durur; parmak izini kullanıcıya gösterip onay alırsan `--yes` ile tekrar çalıştır. Kaydedilen parmak izi sonradan değişirse bağlantı reddedilir, bu onaylanamaz: kullanıcıyı uyar.
- Sorgu zaman aşımı sabittir (30 sn); aşılırsa hata mesajı daha dar bir soru önerir.

## Desteklenen veritabanları

SQL Server (`mssql`), PostgreSQL (`postgresql`), MySQL / MariaDB (`mysql`), SQLite (`sqlite`, dosya salt-okunur açılır). Yeni tür eklemek için `backend/db/dialects.py` içine bir `Dialect` kaydı eklenir; bağlantı, şema okuma, SQL doğrulama ve AI istemi bu kayıttan beslenir.

## MCP

`niq_mcp.py` aynı güvenlik katmanını MCP aracı olarak sunar (stdio). Agent olarak NIQ'yu MCP ile kullanıyorsan: önce `list_connections`,
iş terimi geçen sorularda `get_glossary`, sonra `ask` veya kendi SELECT'inle `run_sql`. Yeni bağlantı, zamanlama ve bildirim
kanalı kurma araçları bilerek yoktur; bunlar kullanıcı onayı gerektirir.

## Geliştirme

```bash
pip install -r requirements-dev.txt
pytest          # testler (gerçek SQLite dosyasıyla)
ruff check .    # lint
python main_desktop.py   # masaüstü uygulaması
python app.py            # yalnızca web arayüzü
```

Kod haritası: `backend/db/` (türler, profiller, bağlayıcı, teşhis, SSH tüneli), `backend/services/` (AI, sorgu akışı, analiz agent'ı, bilgi katmanı, zamanlanmış işler, bildirim, dışa aktarma, hazır sorular), `frontend/static/js/` (core, voice, connect, chat, views), `niq_mcp.py` (MCP), `backend/core/sql_guard.py` (AST doğrulama), `backend/core/approval.py` (kritik adım onayı), `backend/routes/routes.py` (HTTP API), `niq_cli.py` (agent arayüzü).

Yeni kritik bir adım eklerken `approval.CRITICAL_ACTIONS`'a kaydet, API'de `require_approval(...)`, CLI'da `--yes` ile bağla ve test yaz. Anahtarlar, parolalar ve `.env` repoya girmez.
