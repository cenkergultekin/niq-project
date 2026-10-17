/* Account setup stays in NIQ. OAuth credentials never enter the renderer. */
(function () {
    const { $, el, api } = NIQ;
    let data = null;
    let attempt = null;
    let pollTimer = null;
    let modelRequest = 0;
    const defaults = { openai: 'gpt-4o-mini', anthropic: 'claude-haiku-5.5', openrouter: 'openai/gpt-6-luna', local: 'llama3.1' };
    const current = () => data?.accounts.find((a) => a.id === $('ai-account').value);
    const say = (message) => { $('ai-account-status').textContent = message; };
    const guarded = (fn) => async () => {
        try { await fn(); }
        catch (error) { if (!error.cancelled) say(error.message); }
    };

    async function showAccount(fetchModels = false) {
        const account = current();
        modelRequest++;
        NIQ.clear($('ai-models'));
        $('ai-model').value = account?.model || '';
        $('ai-label').value = account?.label || '';
        $('ai-model').disabled = !account;
        $('ai-label').disabled = !account;
        $('ai-model-refresh').hidden = !account || !account.connected;
        $('ai-reauth').hidden = !account || account.provider !== 'chatgpt';
        $('ai-disconnect').hidden = !account || !account.connected;
        if (fetchModels && account?.connected) await loadModels();
    }

    async function loadModels() {
        const account = current();
        if (!account) return;
        const version = ++modelRequest;
        say('Modeller getiriliyor…');
        const models = (await api('/ai/accounts/' + account.id + '/models')).data;
        if (version !== modelRequest || current()?.id !== account.id) return;
        NIQ.clear($('ai-models'));
        models.forEach((m) => $('ai-models').appendChild(el('option', { value: m.id, label: m.name })));
        if (!$('ai-model').value && models.length) $('ai-model').value = models[0].id;
        say(models.length ? '' : 'Bu hesap için kullanılabilir model bulunamadı.');
    }

    function renderNewProvider() {
        const provider = $('ai-new-provider').value;
        $('ai-new-url-field').hidden = !['azure', 'compatible', 'local'].includes(provider);
        $('ai-new-key-field').hidden = provider === 'local';
        $('ai-new-model').value = defaults[provider] || '';
        $('ai-new-model').placeholder = provider === 'azure' ? 'Azure deployment adı' : 'Model adı';
        $('ai-new-url').value = provider === 'local' ? 'http://127.0.0.1:11434/v1' : '';
        $('ai-new-url').placeholder = provider === 'azure' ? 'https://kaynak.openai.azure.com' : 'https://sunucu/v1';
    }

    async function load(preferred) {
        data = (await api('/ai/accounts')).data;
        const select = NIQ.clear($('ai-account'));
        select.appendChild(el('option', { value: '', text: 'Mevcut sağlayıcı ayarı' }));
        data.accounts.forEach((a) => select.appendChild(el('option', {
            value: a.id, text: a.label + (a.label === data.providers[a.provider].label ? '' : ' · ' + data.providers[a.provider].label) + (a.connected ? '' : ' · yeniden giriş gerekli'),
        })));
        select.value = preferred || data.active;
        const voice = NIQ.clear($('ai-voice'));
        voice.appendChild(el('option', { value: '', text: data.active ? 'Ses kapalı' : 'Mevcut sağlayıcı' }));
        data.accounts.filter((a) => a.connected && data.providers[a.provider].voice).forEach((a) =>
            voice.appendChild(el('option', { value: a.id, text: a.label + ' · API bakiyesi' })));
        voice.value = data.voice;
        const providers = NIQ.clear($('ai-new-provider'));
        Object.entries(data.providers).filter(([id]) => id !== 'chatgpt').forEach(([id, p]) =>
            providers.appendChild(el('option', { value: id, text: p.label })));
        providers.value = 'openai';
        renderNewProvider();
        await showAccount();
    }

    async function poll() {
        if (!attempt) return;
        const id = attempt;
        try {
            const result = (await api('/ai/auth/status/' + id)).data;
            if (attempt !== id) return;
            $('ai-login-status').textContent = result.message;
            if (['pending', 'exchanging'].includes(result.status)) {
                pollTimer = setTimeout(poll, 1500);
                return;
            }
            attempt = null;
            $('ai-cancel').hidden = true;
            if (result.status === 'completed') {
                await load(result.account_id);
                await loadModels();
                // Sign-in doesn't change billing until the user selects this account.
                say('Modelini seçip Hesabı kullan ile devam et.');
            }
        } catch (error) {
            if (attempt !== id) return;
            $('ai-login-status').textContent = error.message;
            pollTimer = setTimeout(poll, 4000);
        }
    }

    async function login(provider, accountId = '') {
        if (attempt) await api('/ai/auth/cancel', { body: { attempt } });
        clearTimeout(pollTimer);
        attempt = null;
        const result = (await api('/ai/auth/start', { body: { provider, account_id: accountId } })).data;
        attempt = result.attempt;
        $('ai-cancel').hidden = false;
        $('ai-login-status').textContent = 'Tarayıcıda girişini tamamla.';
        pollTimer = setTimeout(poll, 1000);
    }

    NIQ.accounts = {
        load,
        bind() {
            $('ai-chatgpt').onclick = guarded(() => login('chatgpt'));
            $('ai-openrouter').onclick = guarded(() => login('openrouter'));
            $('ai-reauth').onclick = guarded(() => login('chatgpt', current().id));
            $('ai-cancel').onclick = guarded(async () => {
                if (attempt) await api('/ai/auth/cancel', { body: { attempt } });
                attempt = null; clearTimeout(pollTimer);
                $('ai-cancel').hidden = true;
                $('ai-login-status').textContent = 'Giriş iptal edildi.';
            });
            $('ai-account').onchange = guarded(() => showAccount(true));
            $('ai-new-provider').onchange = renderNewProvider;
            $('ai-model-refresh').onclick = guarded(loadModels);
            $('ai-welcome-dismiss').onclick = guarded(async () => {
                await api('/ai/welcome/dismiss', { body: {} }); $('ai-welcome').close();
            });
            $('ai-use').onclick = guarded(async () => {
                const account = current();
                if (account && $('ai-label').value.trim() !== account.label) {
                    await api('/ai/accounts/' + account.id + '/label', { body: { label: $('ai-label').value.trim() } });
                }
                const selected = await api('/ai/select', { body: { account_id: account?.id || '', model: $('ai-model').value.trim() } });
                await api('/ai/select', { body: { kind: 'voice', account_id: $('ai-voice').value } });
                await load(); await NIQ.refreshHealth(); say('Hesap seçildi.');
                if (selected.data.welcome_required) $('ai-welcome').showModal();
            });
            $('ai-add').onclick = guarded(async () => {
                const button = $('ai-add');
                button.disabled = true;
                try {
                    await api('/ai/accounts', { body: {
                        provider: $('ai-new-provider').value, label: $('ai-new-label').value.trim(),
                        api_key: $('ai-new-key').value.trim(), base_url: $('ai-new-url').value.trim(), model: $('ai-new-model').value.trim(),
                    } });
                    $('ai-new-key').value = '';
                    await load(); await NIQ.refreshHealth(); say('Hesap bağlandı.');
                } finally { button.disabled = false; }
            });
            $('ai-disconnect').onclick = guarded(async () => {
                const result = await api('/ai/accounts/' + current().id + '/disconnect', { body: {} });
                await load(); await NIQ.refreshHealth(); say(result.message || 'Çıkış yapıldı.');
            });
            $('ai-mcp-copy').onclick = guarded(async () => {
                const config = (await api('/mcp/config')).data.config;
                $('ai-mcp-config').textContent = config;
                $('ai-mcp-config').hidden = false;
                try {
                    if (NIQ.desktop?.copyMcpConfiguration) {
                        await new Promise((resolve, reject) => NIQ.desktop.copyMcpConfiguration((ok) => ok ? resolve() : reject(new Error('clipboard'))));
                    } else await navigator.clipboard.writeText(config);
                    say('MCP yapılandırması kopyalandı.');
                }
                catch (_) { say('MCP yapılandırmasını seçerek kopyalayabilirsin.'); }
            });
        },
    };
})();
