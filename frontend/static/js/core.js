/**
 * NIQ çekirdeği: API istemcisi (onay akışıyla), DOM yardımcıları, biçimlendirme, bildirim ve görünüm yönlendirme.
 */
(function () {
    const NIQ = (window.NIQ = {
        state: { connected: false, profile: null, aiReady: false, provider: '', model: '', conversationId: null },
        views: {},
    });

    const $ = (id) => document.getElementById(id);
    NIQ.$ = $;

    // Atmosfer içeriği engellemeden işaretçinin yönünde yavaşça kayar.
    const atmosphere = document.querySelector('.ambient');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let atmosphereFrame = 0;
    let pointerX = 0.5;
    let pointerY = 0.5;
    function paintAtmosphere() {
        atmosphereFrame = 0;
        if (!atmosphere || reducedMotion.matches) return;
        atmosphere.style.setProperty('--ambient-x', (pointerX - 0.5) * 55 + 'px');
        atmosphere.style.setProperty('--ambient-y', (pointerY - 0.5) * 40 + 'px');
        atmosphere.style.setProperty('--pointer-x', pointerX * 100 + '%');
        atmosphere.style.setProperty('--pointer-y', pointerY * 100 + '%');
    }
    window.addEventListener('pointermove', (event) => {
        if (reducedMotion.matches || event.pointerType === 'touch') return;
        pointerX = event.clientX / innerWidth;
        pointerY = event.clientY / innerHeight;
        if (!atmosphereFrame) atmosphereFrame = requestAnimationFrame(paintAtmosphere);
    }, { passive: true });
    window.addEventListener('blur', () => { pointerX = pointerY = 0.5; paintAtmosphere(); });
    reducedMotion.addEventListener('change', () => {
        if (reducedMotion.matches && atmosphere) atmosphere.removeAttribute('style');
    });

    NIQ.newId = () => 'c' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
    NIQ.state.conversationId = NIQ.newId();

    /** Güvenli DOM üretimi: metin her zaman textContent ile yazılır. */
    NIQ.el = function (tag, attrs, ...children) {
        const node = document.createElement(tag);
        for (const [key, value] of Object.entries(attrs || {})) {
            if (value === undefined || value === null || value === false) continue;
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value === true ? '' : value);
        }
        const append = (child) => {
            if (child === null || child === undefined || child === false) return;
            if (Array.isArray(child)) child.forEach(append);
            else node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        };
        children.forEach(append);
        return node;
    };

    NIQ.clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

    // ---- biçimlendirme ----
    const numberFormat = new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 2 });
    const isoDate = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?/;
    NIQ.formatValue = function (value) {
        if (value === null || value === undefined) return { text: 'boş', cls: 'nul' };
        if (typeof value === 'number') return { text: numberFormat.format(value), cls: 'num' };
        if (typeof value === 'boolean') return { text: value ? 'evet' : 'hayır', cls: '' };
        const text = String(value);
        if (isoDate.test(text) && !isNaN(Date.parse(text))) {
            const date = new Date(text);
            return { text: text.length <= 10 ? date.toLocaleDateString('tr-TR') : date.toLocaleString('tr-TR'), cls: '' };
        }
        return { text, cls: '' };
    };
    NIQ.timeAgo = function (iso) {
        if (!iso) return '';
        const date = new Date(iso);
        return date.toLocaleString('tr-TR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    };

    // ---- bildirim ve onay ----
    let toastTimer = null;
    NIQ.toast = function (message, kind) {
        const box = $('toast');
        box.textContent = message;
        box.className = kind === 'error' ? 'error' : '';
        box.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { box.hidden = true; }, kind === 'error' ? 6000 : 3000);
    };

    NIQ.confirm = function (message) {
        return new Promise((resolve) => {
            const dialog = $('confirm');
            $('confirm-text').textContent = message;
            const done = (answer) => { dialog.close(); resolve(answer); };
            $('confirm-yes').onclick = () => done(true);
            $('confirm-no').onclick = () => done(false);
            dialog.oncancel = () => resolve(false);
            dialog.showModal();
        });
    };

    /**
     * API çağrısı. Sunucu kritik adım için onay isterse (409) özeti gösterir; kullanıcı kabul ederse
     * isteği `approved: true` ile yeniden gönderir.
     */
    NIQ.api = async function (path, options) {
        const opts = options || {};
        const send = async (body) => {
            const response = await fetch('/api' + path, {
                method: opts.method || (opts.body ? 'POST' : 'GET'),
                headers: { 'Content-Type': 'application/json' },
                body: body ? JSON.stringify(body) : undefined,
            });
            let result = {};
            try { result = await response.json(); } catch (e) { /* gövde yok */ }
            return { response, result };
        };
        let { response, result } = await send(opts.body);
        if (response.status === 409 && result.needs_approval) {
            if (!(await NIQ.confirm(result.summary))) {
                const cancelled = new Error('İşlem iptal edildi');
                cancelled.cancelled = true;
                throw cancelled;
            }
            ({ response, result } = await send({ ...(opts.body || {}), approved: true }));
        }
        if (!response.ok || result.success === false) {
            const error = new Error(result.error || 'İstek başarısız (' + response.status + ')');
            error.payload = result;
            error.status = response.status;
            throw error;
        }
        return result;
    };

    // ---- yönlendirme ----
    NIQ.go = function (name) {
        const view = name || 'chat';
        if (view !== 'chat' && NIQ.chat) NIQ.chat.cancelRecording();
        document.querySelectorAll('.view').forEach((el) => el.classList.toggle('active', el.id === 'view-' + view));
        document.querySelectorAll('#links button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
        $('dock').hidden = view !== 'chat';
        if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
        const handler = NIQ.views[view];
        if (handler && handler.enter) handler.enter();
        window.scrollTo(0, 0);
    };

    // ---- bağlantı durumu ----
    NIQ.setConnection = function (profile) {
        NIQ.state.connected = !!profile;
        NIQ.state.profile = profile || null;
        const label = $('conn-label');
        label.textContent = profile ? (profile.scope_count > 1 ? `${profile.scope_count} veri kaynağı`
            : profile.name === 'Örnek satışlar' ? 'Satış verileri' : profile.name || profile.database || 'Veri kaynağı')
            : 'Veri kaynağı seç';
        $('db-close-btn').hidden = !profile;
        if (NIQ.chat) NIQ.chat.onConnectionChange();
    };

    NIQ.refreshHealth = async function () {
        try {
            const data = (await NIQ.api('/health')).data;
            NIQ.state.aiReady = data.ai_service_available;
            NIQ.state.provider = data.provider || '';
            NIQ.state.model = data.model || '';
            return data;
        } catch (e) {
            return null;
        }
    };
})();
