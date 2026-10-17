"""
Kritik adımlar için onay mekanizması.

Okuma, keşif, teşhis ve salt-okunur sorgu onaysız çalışır. Kalıcı bir şey
yazan veya veri paylaşımını artıran adımlar (profil kaydetme/silme, anahtar
silme, AI'a veri göndermeyi açma) `approved: true` olmadan reddedilir ve
ne olacağını anlatan bir özet döner. Arayüz bu özeti kullanıcıya gösterir;
agent (CLI) `--yes` ile açıkça onaylar.
"""
from typing import Any, Dict

CRITICAL_ACTIONS = {
    "save_ai_account": "AI hesabını ve API anahtarını bu bilgisayarın güvenli kasasına kaydet ve sohbet için seç",
    "connect_ai_account": "Sağlayıcıya tarayıcıdan giriş yap; verdiğin izinle hesabını NIQ'ye bağla ve giriş bilgilerini güvenli kasada sakla",
    "disconnect_ai_account": "Bu AI hesabının yerel giriş bilgilerini kaldır; ChatGPT yenileme iznini sağlayıcıda iptal etmeyi dene",
    "read_connection_credentials": "Seçtiğin yerel sunucunun veritabanı bağlantı bilgilerini NIQ'de kullan. Parola ekranda gösterilmez ve kaydedilmez",
    "discover_database_catalog": "Bu bilgisayardaki sunucuların mevcut erişim bilgilerini kullanarak yalnızca veritabanı adlarını bul. Tablo, kolon ve içerik okunmaz; sırlar gösterilmez veya kaydedilmez",
    "access_selected_databases": "Seçtiğin veritabanlarına NIQ erişimi ver. Bağlantı bilgileri otomatik doldurulur; tablo/kolon adları sohbet için tanınır. İçerik yalnızca soru gönderdiğinde sorgulanır",
    "save_connection": "Bağlantı profilini ve parolasını bu bilgisayara kaydet",
    "delete_connection": "Bağlantı profilini ve kayıtlı parolasını sil",
    "clear_api_key": "Kayıtlı AI API anahtarını sil",
    "trust_ssh_host": "SSH sunucusunun parmak izine güven ve kaydet",
    "enable_value_samples": "Kolonların örnek değerlerini (durum kodları, ülkeler gibi) AI sağlayıcısına göndermeyi aç",
    "create_schedule": "Zamanlanmış rapor/uyarı oluştur (uygulama açıkken kendiliğinden çalışır, sonuçları seçili kanallara gönderir)",
    "delete_job": "Zamanlanmış işi ve geçmiş çalışmalarını sil",
    "configure_notifications": "Bildirim kanalını (Telegram/webhook) ayarla: sonuç verileri bu kanala gönderilebilir",
    "enable_ai_analysis": "Sonuç satırlarının ilk 20 satırını AI sağlayıcısına göndermeyi aç",
}


class ApprovalRequired(Exception):
    def __init__(self, action: str, summary: str):
        super().__init__(summary)
        self.action = action
        self.summary = summary


def require_approval(data: Dict[str, Any], action: str, detail: str = "") -> None:
    """data['approved'] True değilse ApprovalRequired fırlatır."""
    if data.get("approved") is True:
        return
    summary = CRITICAL_ACTIONS[action] + (f": {detail}" if detail else "")
    raise ApprovalRequired(action, summary)
