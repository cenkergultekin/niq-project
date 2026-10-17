# NIQ Desktop - Natural Intelligence Query

Doğal dil sorularını SQL Server, PostgreSQL, MySQL/MariaDB ve SQLite üzerinde salt-okunur, doğrulanmış sorgulara çeviren masaüstü uygulaması. PySide6 arayüzü, yerel Flask API'si, ChatGPT planı ve çoklu AI hesabı desteği.

## AI hesabı bağlama

İlk açılışta **Continue with ChatGPT** veya **API anahtarı ekle** seçilir. Giriş zorunludur; doğrulanan bağlantı başarı mesajından sonra otomatik olarak veritabanı izin ekranına ilerler. ChatGPT'nin yerel açık kaynak uygulamalar için resmî akışı kullanılır; plan uygunluğu sağlayıcıda belirlenir.

API formunda yalnızca logolu sağlayıcı seçimi, isim ve anahtar vardır: OpenAI, Anthropic, OpenRouter ve Gemini. Aynı sağlayıcıya birden fazla hesap eklenebilir. Azure seçeneği, yönetici `AZURE_OPENAI_ENDPOINT` ve `AZURE_OPENAI_DEPLOYMENT` değerlerini önceden tanımladıysa görünür. Anahtar doğrulanmadan kaydedilmez; işletim sisteminin güvenli kasasında tutulur. Model sohbet çubuğundan seçilir; erişilebiliyorsa önerilen Luna varsayılandır.

Sohbet ve ses hesapları ayrıdır. ChatGPT girişi ses transkripsiyonunu kapsamaz; ses hesabı kullanıcı tarafından ayrıca seçilir. Limit dolduğunda farklı ücretli hesaba otomatik geçilmez. Araştırma kaynakları, güvenlik kararları ve kalan kurumsal parçalar: [AI hesapları](ai-accounts.md).

## Nasıl çalışır

Tek bir kutu: sorunu yaz (ya da mikrofona bas ve konuş), cevabı tablo olarak al. Tablo seçmen gerekmez.

1. Bağlanır bağlanmaz şemadan ve iş sözlüğünden üretilmiş **hazır sorular** çıkar; birine tıklaman yeter.
2. Soruyu yazarsın. NIQ ilgili tabloları kendisi bulur (küçük şemada tümü, büyükte ad/not/geçmiş eşleşmesi ya da modelden seçim, yabancı anahtar komşularıyla).
3. Model, şema + **iş sözlüğü** + doğrulanmış önceki sorularınla JSON olarak `{sql, açıklama, takip soruları}` döndürür.
4. SQL `sqlglot` ile AST düzeyinde doğrulanır (tek SELECT, yasak fonksiyon yok), salt-okunur oturumda, satır ve süre limitiyle çalışır. Hata olursa hata modele geri verilir ve en fazla 2 kez düzeltilir.
5. **Konuşmalı**: "bunu ülkeye göre kır", "sadece Almanya" gibi takipler önceki turu bağlam alır. Cevabın altındaki takip soruları tıkla-sor'dur.
6. **Düzelt ve öğren**: yanlış SQL'i yerinde düzeltip çalıştır, ya da doğru cevaba "Doğru" de. Doğrulananlar sonraki benzer sorularda öncelikli örnek olur.
7. **Derin analiz**: "satışlar neden düştü?", "bu ayı geçen ayla karşılaştır" gibi sorular birkaç sorguyla çözülür (ya da `/analiz`). Sonuç satırları modele gittiği için bir kez onay ister.
8. Sonuçları CSV/Excel indirebilir, grafik olarak görebilir, **zamanlanmış rapor veya uyarı**ya çevirebilirsin ("her pazartesi 09:00'da haftalık satışı gönder", "iade oranı %5'i geçerse haber ver"). Bildirimler uygulama içine, Telegram'a veya webhook'a gider.
9. Geçmiş, bilgi katmanı ve işler veritabanına hiçbir şey yazılmadan yerelde (`%APPDATA%\NIQ`) tutulur.

### Komut çubuğu

`/` yazınca ya da `Ctrl+K` ile komutlar tahmin edilir: `/analiz`, `/rapor`, `/yeni`, `/baglan`, `/gecmis`, `/raporlar`, `/sozluk`, `/ayarlar`, `/temizle`. Zamanlama gibi görünen cümleleri ("her gün ... gönder") kendisi fark edip plan önizlemesi gösterir.

### Sesli sorgu

Mikrofon düğmesine basıp konuş: sözlerin soru çubuğunda silik taslak olarak görünür; durdurduğunda düzenlenebilir metin olur. Göndermeden sorgulanmaz. Ses yalnızca mikrofonu başlattığında seçtiğin ses sağlayıcısına gider. OpenRouter'da ses girdili model, OpenAI API'de `gpt-4o-mini-transcribe` kullanılır.

## Veritabanı bağlantısı

Desteklenenler: **SQL Server, PostgreSQL, MySQL / MariaDB, SQLite**. Yeni tür, `backend/db/dialects.py` içine bir kayıt eklenerek gelir.

İlk açılışta giriş seçenekleri gösterilir; login kendiliğinden başlamaz; doğrulanan bir hesapla devam edilir. Sonra **İzin ver ve bul**, bu bilgisayardaki yalnızca DB adlarını listeler. Tablo/kolon/içerik okunmaz ve izin verilmeden ilerlenmez. Kullanıcı DB'leri seçip **Seçilenlere erişim ver** dediğinde mevcut bilgiler otomatik doldurulur ve sohbet ekranına geçilir. İçerik yalnızca soru gönderildiğinde sorgulanır; otomatik veri profillemesi yapılmaz. Mevcut erişim bilgisi bulunmayan kaynaklar otomatik bağlanmış gibi gösterilmez.

Sonraki kullanımlarda kaynak paneli veya isteğe bağlı gelişmiş bağlantı alanları kullanılabilir.
- Bir bağlantı URL'si, ODBC/ADO/JDBC dizesi ya da dosya yolu yapıştırırsanız tür, sunucu, port, veritabanı ve kullanıcı otomatik dolar. Port, sunucu ve sürücü varsayılanları türe göre tahmin edilir.
- "Veritabanlarını getir" adı henüz bilinmeyen veya yanlış yazılmış veritabanından bağımsız olarak sunucudaki adları listeler.
- **Test** adım adım ilerler (sürücü, ağ, giriş, tablo erişimi, yetki); başarısız adım ne yapılacağını söyler.
- Profiller parolayla birlikte yalnızca siz onaylarsanız kaydedilir; parola Windows Credential Manager'da durur.
- Kayıtsız bağlantının sırları yalnızca bellekte kalır. Bağlandıktan sonra profili parolayı yeniden girmeden kaydedebilirsiniz. Son kullanılan kayıtlı bağlantı, masaüstü portu değişse de hatırlanır; açıkça bağlantıyı kapatırsanız otomatik açılmaz.
- Soru yazarken ilgili tablolar tahmin edilip seçilir.
- **SQL Server Windows girişi** (Trusted Connection): kimlik doğrulamada "Windows girişi"ni seçin, kullanıcı/parola gerekmez.
- **Şifreli bağlantı**: Gelişmiş > SSL/TLS (otomatik, şifreli, sertifikayı doğrula, kapalı).
- **SSH tüneli**: Gelişmiş > SSH tüneli. Veritabanına yalnızca atlama sunucusundan erişiliyorsa doldurun. SSH sunucusunun parmak izi ilk bağlantıda onayınıza sunulur, sonradan değişirse bağlantı reddedilir.
- **Zaman aşımı**: sorgular 30 saniyede sürücü düzeyinde kesilir.
- **Çevrimdışı**: grafik kütüphanesi ve fontlar uygulamayla gelir, internet gerekmez (AI sağlayıcısı hariç; yerel model kullanırsanız o da gerekmez).

Kritik adımlar (profil kaydetme/silme, API anahtarı silme, sonuç satırlarını AI'a göndermeyi açma) onay ister; okuma, keşif, test ve sorgu onaysızdır.

### Agent / komut satırı

`niq_cli.py` aynı yapıyı JSON çıktıyla sunar; adımlar ve kurallar için [AGENTS.md](AGENTS.md).
```bash
python niq_cli.py discover
python niq_cli.py diagnose --input "postgresql://kullanici@host/db" --password-env PGPASS
python niq_cli.py connections save --input "postgresql://kullanici@host/db" --name uretim --password-env PGPASS --yes
python niq_cli.py ask --connection uretim "geçen ayın toplam satışı"
```

## MCP sunucusu (agent'lar için)

`niq_mcp.py`, Claude Desktop, Claude Code, Cursor gibi agent'lara NIQ'yu bir araç olarak açar. Agent ham veritabanı bağlantısı
yerine NIQ'nun katmanlarını kullanır: salt-okunur doğrulanmış SQL, iş sözlüğü, tablo notları ve doğrulanmış örnekler.
Parola agent'a gösterilmez; agent yalnızca sizin NIQ'da kaydettiğiniz profilleri kullanır ve yeni bağlantı, zamanlama
ya da bildirim kanalı kuramaz.

Araçlar: `list_connections`, `list_tables`, `describe_tables`, `get_glossary`, `ask`, `run_sql`, `add_glossary_term`,
`list_reports`, `recent_results`, `status`.

Claude Desktop için `claude_desktop_config.json`:
```json
{"mcpServers": {"niq": {"command": "C:\\yol\\NIQ\\venv\\Scripts\\python.exe", "args": ["C:\\yol\\NIQ\\niq_mcp.py"]}}}
```
Claude Code için: `claude mcp add niq -- C:\yol\NIQ\venv\Scripts\python.exe C:\yol\NIQ\niq_mcp.py`

Ayarlar'daki **MCP ile kullan** bölümünden kendi kurulumuna uygun yapılandırmayı kopyalayabilirsin. Paketli dağıtımda ayrı `NIQ-MCP.exe` stdio sunucusu kullanılır; installer bunu masaüstü exe'siyle birlikte kurar. İstemci kendi modeliyle şemayı okuyup `run_sql` çağırabilir; bu kullanım NIQ AI anahtarı gerektirmez. `ask`, NIQ'de seçilmiş hesabı kullanır.

## Güvenlik

- Veritabanına yazma yoktur: INSERT/UPDATE/DELETE/DDL/EXEC/`SELECT INTO`/`OPENROWSET` reddedilir. Yine de uygulamayı **salt-okunur bir SQL kullanıcısıyla** kullanmanız önerilir.
- Kaydedilen API anahtarları ve onaylanan bağlantı parolaları Windows Credential Manager'da saklanır; kayıtsız bağlantının sırları bellekte kalır. Dağıtılan exe'ye yazılmaz.
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

### Yerel doğrulama

Docker açıkken `python tests/local_databases.py --desktop`, PostgreSQL 17, gerçek MySQL 8.4, MariaDB 11.8 ve SQL Server 2022'yi yalnızca localhost'ta geçici test sunucuları olarak açar. SQLite testleriyle birlikte tüm paketi, Qt bağlantı panelini ve sentetik mikrofon akışını çalıştırıp sunucuları kapatır. PostgreSQL için geçici TLS sertifikası da üretir. Kullanıcının veritabanına, profillerine ve anahtarlarına dokunmaz.

Fiziksel mikrofon için `--real-mic` ekleyin; açılan test penceresinde mikrofona basıp durdurun. Bu masaüstü kontrollerinde ses yerel test dublöründe işlenir, dışarı gönderilmez. Tekrar inceleme için `--keep`, sonrasında kapatmak için `python tests/local_databases.py --stop` kullanılabilir. Yerel portlar: 55432, 53307, 53306 ve 51433. SQL Server TLS için ODBC Driver 18 gerekir; Windows girişi, yetkili bir Windows/SQL Server ortamı gerektirir.

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
