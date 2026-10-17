# AI hesapları

17 Ekim 2026 araştırması ve uygulama kararı. NIQ herkese açık, MIT lisanslı ve yerelde çalışan bir uygulamadır.

## ChatGPT girişi

[OpenAI'nin açık kaynak akışı](https://developers.openai.com/siwc/token-sharing-open-source) bu dağıtım için doğrudan ChatGPT planı kullanımını destekliyor. Ücretli veya uzaktan barındırılan bir ürün aynı uygunluğu otomatik devralmaz; önce OpenAI'nin ticari uygunluk süreci tamamlanmalı.

Uygulandı: sistem tarayıcısı, loopback callback, PKCE S256, tek kullanımlık/süreli state ve nonce, dinamik NIQ kaydı, ID token imza/issuer/audience/expiry/nonce doğrulaması, kayıt başına ayrı hesap ve dönen scope'ta plan kullanım izninin denetimi. Başka uygulamaların client ID'leri veya giriş dosyaları kullanılmaz. İlk kayıt `dynamic_agent_client`, token değişimi ve sonraki girişler sağlayıcının döndürdüğü client ID'yi kullanır. E-posta/subject workspace kimliği sayılmaz. [Kayıt ve giriş](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)

Host ID bilgisayar için sabittir. Yenileme masaüstü/MCP süreçleri arasında hesap bazlı dosya kilidiyle serileştirilir; refresh token güvenli kasada değiştirilir. Çıkışta refresh izni iptal edilmeye çalışılır; doğrulanamazsa kullanıcıya bildirilir. [Hesap ve oturum](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)

Modeller hesabın gerçek kataloğundan gelir; gizli modeller gösterilmez ve sunucu sırası korunur. Kullanıcı hesabı/modeli seçmeden mevcut ücretlendirme değişmez. Public Responses API `store:false`, `stream:true` ve gerekli geçmiş ile kullanılır. Yalnızca `response.completed` başarıdır. Eksik yanıt/kopma/limit başarılı SQL sayılmaz. Function tool'lar NIQ namespace'i altında, reasoning/tool geçmişi korunarak çalışır. [Çıkarım](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)

ChatGPT girişi ses transkripsiyonunu kapsamaz. Ses için ayrıca OpenAI API/OpenRouter hesabı seçilir ve ücret API bakiyesinden düşer. Hosted MCP bu akışta desteklenmez; NIQ'nun yerel MCP sunucusu ayrıdır. [Önizleme sınırları](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)

İlk kullanımda bir defalık plan bildirimi, soru çubuğunda plan durumu, kullanım bağlantısı ve limitte birincil kullanım yönetimi eylemi eklendi. Düğme logosu [resmî giriş sayfasının](https://developers.openai.com/siwc/website) sunduğu `chatgpt-logo-white.svg` dosyasıdır. [Arayüz koşulları](https://developers.openai.com/siwc/ui-ux-guidelines)

## Sağlayıcılar

| Sağlayıcı | Giriş | Kullanım kaynağı |
| --- | --- | --- |
| ChatGPT | Resmî OAuth / PKCE | İzin verilen ChatGPT planı/kredileri |
| OpenRouter | Resmî OAuth / PKCE veya API anahtarı | OpenRouter bakiyesi |
| OpenAI | API anahtarı | OpenAI API projesi |
| Anthropic | API anahtarı, native Messages SDK | Claude Console API bakiyesi |
| Gemini | API anahtarı | Google AI API hesabı |
| Groq · Llama | API anahtarı | Groq hesabı |
| Mistral | API anahtarı | Mistral hesabı |
| DeepSeek | API anahtarı | DeepSeek hesabı |
| Azure OpenAI | Yönetici tarafından önceden yapılandırılmış kaynak + API anahtarı | Azure kaynağı |
| OpenAI uyumlu | HTTPS adresi, anahtar ve model | Seçilen sunucunun hesabı |
| Yerel | Loopback adresi ve model | Yerel model |

Her sağlayıcıdan birden fazla adlandırılmış hesap eklenebilir. Hesap dosyası sır içermez. Uzun OAuth kayıtları Windows credential boyut sınırına uygun güvenli kasa parçalarına yazılır; tamamı yazılmadan aktif kayıt değiştirilmez. Uzak endpoint HTTPS olmalı; adresin içinde parola/query tokenı/fragment kabul edilmez.

Azure kaynak adresinden `/openai/v1` türetilir, model alanı deployment adıdır. [Microsoft belgesi](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/switching-endpoints)

OpenRouter girişi ChatGPT/Claude aboneliği sayılmaz. NIQ bağlantısını kaldırmak sağlayıcıdaki API anahtarını iptal etmez; kullanıcıya bildirilir. [OpenRouter OAuth](https://openrouter.ai/docs/guides/overview/auth/oauth)

Anthropic, başkaları için geliştirilen ürünlerde Console anahtarı veya desteklenen cloud erişimini yönlendiriyor. NIQ, Claude Code kimliğini taklit etmez veya abonelik limitlerine üçüncü taraf trafiği yönlendirmez. Native JSON schema ve tool use/result biçimleri kullanılır, thinking imzaları korunur. [Kullanım politikası](https://support.claude.com/en/articles/13189465-log-in-to-your-claude-account), [araçlar](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools), [JSON çıktı](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

## MCP

İlk masaüstü açılışında önce giriş seçenekleri gösterilir; tarayıcı ancak kullanıcı giriş düğmesine basınca başlar. Giriş zorunludur; doğrulanan hesap başarı mesajından sonra otomatik olarak DB izin ekranına ilerler. Model seçimi sohbet çubuğundadır; erişilebiliyorsa Luna önerilir. API formunda yalnızca sağlayıcı, isim ve anahtar bulunur. Sonraki ekran yerel veritabanlarının yalnızca adlarını bulmak için açık izin ister; öncesinde keşif, otomatik bağlantı, tablo/kolon veya içerik okuması yapılmaz. Sonra kullanıcı istediği DB'leri seçip erişim verir; mevcut bilgilerle otomatik bağlanılır ve sohbet ekranına geçilir. Şema ancak seçili erişim onayından sonra tanınır, içerik yalnızca soru gönderildiğinde sorgulanır. Otomatik arka plan veri profillemesi kaldırıldı; profiling ayrı kullanıcı eylemidir.

Ayarlar'dan yapılandırma kopyalanır. Kaynak kurulum Python + `niq_mcp.py`, paketli dağıtım aynı dizindeki `NIQ-MCP.exe` kullanır. GUI exe'sinin stdout'u olmadığından ayrı console executable kullanılır; installer ikisini taşır.

İstemci kendi modeliyle şema okuyup `run_sql` çağırabilir; NIQ AI anahtarı gerekmez. `ask`, NIQ'de seçilmiş hesabı kullanır. MCP'ye token/anahtar/parola, giriş başlatma veya hesap değiştirme aracı verilmez. Veritabanı yazmaları mevcut SQL güvenlik katmanında reddedilir.

## Kurumsal sınırlar

Otomatik kota rotasyonu, sessiz ücretli fallback veya anahtar paylaşımı yoktur. Devam eden istek, hesap seçimi değişse de başladığı hesabı korur. Sonuç satırlarının modele gönderilmesi açık onaya bağlıdır. OAuth callback kodları erişim loglarından çıkarılır. Mevcut sınır tek işletim sistemi kullanıcısının yerel kasasıdır; merkezi SSO/RBAC/Key Vault, yönetici politikası veya sunucu bütçe kontrolü uygulandığı iddia edilmez.

## Kabul ve sonraki parçalar

Sentetik imzalı OIDC, PKCE, replay/expiry/denial, eksik plan izni, yanlış subject/client/audience/issuer/nonce, token rotasyonu, büyük kasa kaydı ve yarım stream testleri var. Gerçek Qt'de giriş onayı, model seçimi, ilk plan bildirimi, ayrı ses hesabı ve MCP kopyalama sınandı. Subprocess MCP handshake, SQLite SELECT ve yazma reddi sınandı. Gerçek ChatGPT/OpenRouter iznini kullanıcı tamamlar; sentetik test gerçek uygunluğu veya bakiyeyi kanıtlamaz.

[Nous Portal](https://hermes-agent.nousresearch.com/docs/integrations/nous-portal), Hermes için abonelik/OAuth sunuyor; bu belge NIQ adına üçüncü taraf kayıt/izin koşullarını tek başına kanıtlamıyor. Hermes client ID'si kopyalanmadı. Ek abonelik sağlayıcıları kendi kayıt/izin koşullarıyla değerlendirilecek. Microsoft Entra ID ve merkezi kurum politikaları ayrı genişletmelerdir. DB entegrasyon CI işi kapsam dışıdır.

OAuth dönüşü, kod içermeyen süreli sonuç adresine yönlendirilir. Başarı/hata sayfası yerel fontlar ve NIQ/sağlayıcı logolarıyla responsive gösterilir. İlk hesap ekranında ortalanmış piksel NIQ yazısı, sönümlenen imleç etkileşimi ve yavaş lacivert akış vardır; hareket azaltma tercihi desteklenir.

Ek sağlayıcıların uçları ve modelleri resmî belgelerden doğrulandı: [Groq uyumluluğu](https://console.groq.com/docs/openai), [Groq modelleri](https://console.groq.com/docs/models), [Mistral geçiş](https://docs.mistral.ai/resources/migration-guides), [DeepSeek](https://api-docs.deepseek.com/). Llama model ailesidir; doğrudan bir abonelik giriş sağlayıcısı olarak gösterilmez.

NIQ Planı yükselt ekranı şimdilik yalnız tanıtım durumudur; ödeme, plan hakkı veya anahtarsız model kullanımı açmaz. Bu kapsam kullanıcı tarafından onaylandı. Giriş başarısızlığında yeniden bağlan düğmesi yerine ChatGPT/API/plan seçenekleri görünür kalır. Üst yenile/F5 kontrolü kaldırıldı.

ChatGPT akışında `response.completed.output` boş gelebiliyor; tamamlanmış öğeler `response.output_item.done` olaylarında toplanır ve yalnız `response.completed` geldikten sonra kullanılır. Bu davranış gerçek hesapla doğrulandı. Akış hatası/kopması tamamlanmış tek öğeyi başarılı yanıt saymaz; reasoning ve function call geçmişi korunur.

Çoklu kaynak seçimindeki JSON şeması diğer görevlerle aynı `name`, `strict`, `schema` sarmalayıcısını kullanır. Ham object şeması Responses `text.format.type` alanını yanlışlıkla `object` yaparak 400 üretiyordu; düzeltildi. Gerçek ChatGPT hesabıyla sentetik kaynak seçimi, SQLite SQL üretimi/salt-okunur sorgu ve function tool dönüşü doğrulandı.

Sorgu gecikmesi: bağımsız seçili DB’ler en fazla dört worker ile paralel sorgulanır; kaynak sırası ve ayrı sonuçlar korunur. Tek sorunun AI/düzeltme/analiz adımları 90 saniyelik ortak bütçe kullanır; bütçe içindeki sağlayıcı çağrısında timeout en çok 30 saniye, SDK otomatik retry kapalıdır. DB sorgu sınırı ayrıca 30 saniyedir; bunlar kesin 90 saniyelik uçtan uca SLA sayılmaz. ChatGPT akışında olaylar geldikçe bütçe kontrol edilir. Sohbet beklerken geçen süre görünür; loglar soru/sonuç satırı olmadan kaynak ve toplam süreyi tutar. GPT-6.1 Sol ile tek sentetik dört-DB ölçümünde sıralı 16,08 sn, paralel 11,60 sn; genellenmiş performans garantisi değil.
