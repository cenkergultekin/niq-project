/* Account-entitled models live at the composer, never in the login steps. */
(function () {
    const { $, el, api } = NIQ;
    let models = [], active = '', account = '', recommended = '';
    const shortName = (id) => /(^|\/)gpt-6-luna$/.test(id) ? 'Luna' : id.split('/').pop();
    function close() {
        $('chat-model-menu').hidden = true;
        $('chat-model-button').setAttribute('aria-expanded', 'false');
    }
    function render() {
        const query = $('chat-model-search').value.toLocaleLowerCase('tr');
        const list = NIQ.clear($('chat-model-options'));
        [...models].sort((a, b) => Number(b.id === recommended) - Number(a.id === recommended)).filter((m) =>
            (m.id + ' ' + m.name).toLocaleLowerCase('tr').includes(query)).forEach((m) => {
            const button = el('button', { class: 'model-option', role: 'option', 'aria-selected': String(m.id === active),
                onclick: async () => {
                    button.disabled = true;
                    try {
                        await api('/ai/select', { body: { account_id: account, model: m.id } });
                        active = m.id; NIQ.models.label(active); close(); await NIQ.refreshHealth();
                    } catch (error) { NIQ.toast(error.message, 'error'); }
                    finally { button.disabled = false; }
                } }, el('span', { text: shortName(m.id) }), m.id === recommended ? el('small', { text: 'Önerilen' }) : null);
            list.appendChild(button);
        });
    }
    NIQ.models = {
        label(model) { if (model) $('chat-model-label').textContent = shortName(model); },
        bind() {
            $('chat-model-button').onclick = async () => {
                if (!$('chat-model-menu').hidden) { close(); return; }
                $('chat-model-menu').hidden = false;
                $('chat-model-button').setAttribute('aria-expanded', 'true');
                NIQ.clear($('chat-model-options')).appendChild(el('p', { class: 'note', text: 'Modeller getiriliyor…' }));
                try {
                    const data = (await api('/ai/accounts')).data;
                    const item = data.accounts.find((a) => a.id === data.active && a.connected);
                    if (!item) throw new Error('Önce bir hesap bağla.');
                    account = item.id; active = item.model;
                    models = (await api('/ai/accounts/' + account + '/models')).data;
                    recommended = (models.find((m) => /(^|\/)gpt-6-luna$/.test(m.id)) ||
                        models.find((m) => m.id === data.providers[item.provider].default_model) || models[0])?.id || '';
                    $('chat-model-search').value = ''; render(); $('chat-model-search').focus();
                } catch (error) {
                    NIQ.clear($('chat-model-options')).appendChild(el('p', { class: 'error-text', text: error.message }));
                }
            };
            $('chat-model-search').oninput = render;
            document.addEventListener('click', (event) => {
                if (!event.target.closest('#chat-model-menu, #chat-model-button')) close();
            });
            document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
        },
    };
})();
