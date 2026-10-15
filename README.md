# NIQ Desktop - Natural Intelligence Query

Doğal dilde sorduğunuz soruları Microsoft SQL Server için salt-okunur, doğrulanmış T-SQL sorgularına çeviren masaüstü uygulaması. PySide6 (Qt WebEngine) arayüzü, yerel Flask API'si ve OpenAI / OpenRouter / yerel (Ollama, LM Studio) model desteği.

## Nasıl çalışır

1. Bağlanır, tabloları listeler, seçtiğiniz tabloların şemasını (kolon tipleri, birincil ve yabancı anahtarlar) okur.
2. Soruyu şema ve daha önce başarılı olmuş benzer sorularla birlikte modele gönderir; model JSON olarak `{sql, explanation}` döndürür.
3. SQL, `sqlglot` ile AST düzeyinde doğrulanır: tek ifade, yalnızca SELECT/CTE/UNION, yasak fonksiyon yok, yalnızca seçilen tablolar.
4. Sorgu satır limitiyle, her zaman rollback edilen bir işlemde çalışır. Hata olursa hata modele geri verilir ve en fazla 2 kez düzeltilir.
5. Geçmiş, veritabanınıza hiçbir şey yazılmadan yerelde (`%APPDATA%\NIQ\history.db`) tutulur.

## Veritabanı bağlantısı

Desteklenenler: **SQL Server, PostgreSQL, MySQL / MariaDB, SQLite**. Yeni tür, `backend/db/dialects.py` içine bir kayıt eklenerek gelir.

Arayüzde bağlantı dört adımdır: **Tür** (yerelde bulunan sunucular ayrıca önerilir), **Bilgiler**, **Test**, **Bağlan**.
- Bir bağlantı URL'si, ODBC/ADO/JDBC dizesi ya da dosya yolu yapıştırırsanız tür, sunucu, port, veritabanı ve kullanıcı otomatik dolar. Port, sunucu ve sürücü varsayılanları türe göre tahmin edilir.
- "Veritabanlarını getir" sunucudaki adları listeler.
- **Test** adım adım ilerler (sürücü, ağ, giriş, tablo erişimi, yetki); başarısız adım ne yapılacağını söyler.
- Profiller parolayla birlikte yalnızca siz onaylarsanız kaydedilir; parola Windows Credential Manager'da durur.
- Soru yazarken ilgili tablolar tahmin edilip seçilir.

Kritik adımlar (profil kaydetme/silme, API anahtarı silme, sonuç satırlarını AI'a göndermeyi açma) onay ister; okuma, keşif, test ve sorgu onaysızdır.

### Agent / komut satırı

`niq_cli.py` aynı yapıyı JSON çıktıyla sunar; adımlar ve kurallar için [AGENTS.md](AGENTS.md).
```bash
python niq_cli.py discover
python niq_cli.py diagnose --input "postgresql://kullanici@host/db" --password-env PGPASS
python niq_cli.py connections save --input "postgresql://kullanici@host/db" --name uretim --password-env PGPASS --yes
python niq_cli.py ask --connection uretim "geçen ayın toplam satışı"
```

## Güvenlik

- Veritabanına yazma yoktur: INSERT/UPDATE/DELETE/DDL/EXEC/`SELECT INTO`/`OPENROWSET` reddedilir. Yine de uygulamayı **salt-okunur bir SQL kullanıcısıyla** kullanmanız önerilir.
- API anahtarları ve veritabanı parolası Windows Credential Manager'da saklanır; dağıtılan exe'ye veya dosyaya yazılmaz.
- Yerel sunucu rastgele boş bir porta bağlanır, yalnızca `127.0.0.1` Host/Origin kabul eder.
- Sonuç satırları varsayılan olarak AI sağlayıcısına gönderilmez. Yalnızca Ayarlar'dan "AI yorumu"nu açarsanız ilk 20 satır gider.

## Kurulum

### Son kullanıcı
Release'teki `NIQDesktop_Setup.exe` dosyasını kurun, **Ayarlar** sayfasından sağlayıcıyı seçip API anahtarını girin.

### Geliştirici
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
python main_desktop.py          # masaüstü uygulaması
python app.py                   # yalnızca web arayüzü: http://127.0.0.1:5000
pytest                          # testler
ruff check .                    # lint
```
Geliştirme sırasında anahtarı `.env` dosyasına da yazabilirsiniz (`OPENROUTER_API_KEY=...` veya `OPENAI_API_KEY=...`); `.env` paketlenmez.

## Modeller

Ayarlar sayfasından sağlayıcı ve model seçilir. OpenRouter için varsayılan `openai/gpt-6-luna`; `claude-haiku-5.5`, `gpt-6-luna-pro` ve `gpt-4o-mini` listede, fiyatlar OpenRouter'dan canlı okunur. Özel model adı da yazılabilir. Yerel modeller için OpenAI uyumlu bir sunucu adresi (varsayılan `http://127.0.0.1:11434/v1`) girin.

Düşünen (reasoning) modeller görünmeyen belirteçleri de çıktı limitinden harcar; bu yüzden varsayılan limit 2048'dir ve Ayarlar'dan değiştirilebilir.

## Gereksinimler

- Windows 10+; SQL Server için Microsoft ODBC Driver 17/18 (yüklü en yeni otomatik seçilir). PostgreSQL, MySQL ve SQLite sürücüleri uygulamayla gelir.
- Python 3.10+ (yalnızca geliştirme için)

## Paketleme

```bash
build_desktop.bat                  # dist\NIQ-Desktop.exe
makensis installer-desktop.nsi     # NSIS kuruluysa installer
```

## Sorun giderme

- **Login failed**: kullanıcı adı/parola; **Cannot connect**: sunucu adı ve TCP/IP; **Driver not found**: ODBC sürücüsü kurulu mu.
- **Sertifika hatası (Driver 18)**: Ayarlar > Gelişmiş > "Sunucu sertifikasına güven".
- **Cevap kesiliyor / boş**: Ayarlar > Gelişmiş > çıktı belirteç limitini artırın.
- Loglar: `%APPDATA%\NIQ\logs\niq.log`.

## Lisans

MIT License - [LICENSE.txt](LICENSE.txt)
