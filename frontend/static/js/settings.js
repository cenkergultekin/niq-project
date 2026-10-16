/**
 * Ayarlar sayfası: sağlayıcı, model, API anahtarı ve limitler.
 */
(function () {
    const $ = (id) => document.getElementById(id);
    let data = null;

    function status(message, type) {
        const el = $('settings-status');
        if (!el) return;
        el.textContent = message;
        el.className = 'status-message ' + (type || 'info');
    }

    async function api(path, method, body) {
        const send = async (payload) => {
            const response = await fetch('/api' + path, {
                method: method || 'GET',
                headers: { 'Content-Type': 'application/json' },
                body: payload ? JSON.stringify(payload) : undefined,
            });
            return { response, result: await response.json() };
        };
        let { response, result } = await send(body);
        // Kritik adım: sunucu onay özeti döndürürse kullanıcıya sor, kabul ederse onaylı tekrar dene
        if (response.status === 409 && result.needs_approval) {
            if (!window.confirm(result.summary + '\n\nOnaylıyor musunuz?')) throw new Error('İşlem iptal edildi');
            ({ response, result } = await send({ ...(body || {}), approved: true }));
        }
        if (!response.ok || result.success === false) throw new Error(result.error || 'İstek başarısız');
        return result;
    }

    function formatPrice(model) {
        if (!model || !model.price) return '';
        return `Fiyat: $${model.price.input} giriş / $${model.price.output} çıkış (1M belirteç)`;
    }

    function renderModels() {
        const provider = $('set_provider').value;
        const select = $('set_model');
        const catalog = data.catalog[provider] || [];
        const current = data.models[provider];
        select.innerHTML = '';
        catalog.forEach((m) => {
            const option = document.createElement('option');
            option.value = m.id;
            option.textContent = m.name + (m.price ? `  ·  $${m.price.input}/$${m.price.output}` : '');
            select.appendChild(option);
        });
        if (current && !catalog.some((m) => m.id === current)) {
            const option = document.createElement('option');
            option.value = current;
            option.textContent = current + ' (özel)';
            select.appendChild(option);
        }
        select.value = current;
        $('set_model_custom').value = '';
        $('set_model_price').textContent = formatPrice(catalog.find((m) => m.id === select.value));

        const isLocal = provider === 'local';
        $('set_key_group').style.display = isLocal ? 'none' : '';
        $('set_base_url_group').style.display = isLocal ? '' : 'none';
        const hasKey = data.providers[provider].has_key;
        $('set_api_key').value = '';
        $('set_api_key').placeholder = hasKey ? 'Kayıtlı (değiştirmek için yeni anahtar girin)' : 'Anahtarı yapıştırın';
        $('set_clear_key_btn').style.display = hasKey && !isLocal ? '' : 'none';
    }

    function render() {
        const providerSelect = $('set_provider');
        providerSelect.innerHTML = '';
        Object.entries(data.providers).forEach(([id, p]) => {
            const option = document.createElement('option');
            option.value = id;
            option.textContent = p.label + (p.has_key || id === 'local' ? '' : ' (anahtar yok)');
            providerSelect.appendChild(option);
        });
        providerSelect.value = data.provider;
        $('set_base_url').value = data.local_base_url || '';
        $('set_max_tokens').value = data.max_output_tokens;
        $('set_row_limit').value = data.row_limit;
        $('set_ai_analysis').checked = !!data.ai_analysis;
        $('set_trust_cert').checked = !!data.trust_server_cert;
        renderModels();
    }

    async function load() {
        try {
            data = (await api('/settings')).data;
            render();
        } catch (error) {
            status('Ayarlar yüklenemedi: ' + error.message, 'error');
        }
    }

    function payload(extra) {
        const provider = $('set_provider').value;
        const model = $('set_model_custom').value.trim() || $('set_model').value;
        const body = {
            provider,
            models: { [provider]: model },
            local_base_url: $('set_base_url').value.trim(),
            max_output_tokens: Number($('set_max_tokens').value),
            row_limit: Number($('set_row_limit').value),
            ai_analysis: $('set_ai_analysis').checked,
            trust_server_cert: $('set_trust_cert').checked,
        };
        const key = $('set_api_key').value.trim();
        if (key) body.api_key = key;
        return Object.assign(body, extra || {});
    }

    async function save(extra) {
        data = (await api('/settings', 'POST', payload(extra))).data;
        render();
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (!$('settings-page')) return;
        $('set_provider').addEventListener('change', renderModels);
        $('set_model').addEventListener('change', () => {
            const catalog = data.catalog[$('set_provider').value] || [];
            $('set_model_price').textContent = formatPrice(catalog.find((m) => m.id === $('set_model').value));
        });
        $('set_save_btn').addEventListener('click', async () => {
            try {
                await save();
                status('Ayarlar kaydedildi.', 'success');
            } catch (error) {
                status(error.message, 'error');
            }
        });
        $('set_clear_key_btn').addEventListener('click', async () => {
            try {
                await save({ clear_api_key: true, api_key: '' });
                status('Anahtar silindi.', 'success');
            } catch (error) {
                status(error.message, 'error');
            }
        });
        $('set_test_btn').addEventListener('click', async () => {
            status('Test ediliyor...', 'info');
            try {
                await save();
                const result = (await api('/settings/test', 'POST')).data;
                status(`Bağlantı başarılı: ${result.provider} / ${result.model} (${result.latency_ms} ms)`, 'success');
            } catch (error) {
                status(error.message, 'error');
            }
        });
        document.querySelectorAll('.sidebar-nav-item[data-page="settings-page"]').forEach((btn) =>
            btn.addEventListener('click', load));
        load();
    });
})();
