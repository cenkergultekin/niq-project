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
    "save_connection": "Bağlantı profilini ve parolasını bu bilgisayara kaydet",
    "delete_connection": "Bağlantı profilini ve kayıtlı parolasını sil",
    "clear_api_key": "Kayıtlı AI API anahtarını sil",
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
