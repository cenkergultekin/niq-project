/**
 * Görünümler: geçmiş, raporlar, sözlük, ayarlar ve uygulama açılışı.
 */
(function () {
    const { $, el, api } = NIQ;

    function status(id, message, kind) {
        const box = $(id);
        box.textContent = message || '';
        box.className = 'status' + (kind ? ' ' + kind : '');
    }

    function needConnection(listId, text) {
        NIQ.clear($(listId)).appendChild(el('p', { class: 'muted', text: text || 'Önce bir veritabanına bağlan.' }));
    }

    function guarded(fn) {
        return async (...args) => {
            try { return await fn(...args); } catch (error) { if (!error.cancelled) NIQ.toast(error.message, 'error'); return null; }
        };
    }

    // ---- geçmiş ----
    NIQ.views.history = {
        async enter() {
            const list = NIQ.clear($('history-list'));
            if (!NIQ.state.connected) { needConnection('history-list'); return; }
            const queries = (await api('/queries?limit=100')).data;
            if (!queries.length) { list.appendChild(el('p', { class: 'muted', text: 'Henüz soru sormadın.' })); return; }
            queries.forEach((q) => {
                const meta = [NIQ.timeAgo(q.created_at), q.source, (q.tables_used || []).join(', ')].filter(Boolean).join(' · ');
                list.appendChild(el('div', { class: 'item' },
                    el('div', { class: 'item-main' },
                        el('button', { class: 'item-title history-question', text: q.question, title: 'Kayıtlı sonucu göster',
                            onclick: () => { NIQ.go('chat'); NIQ.chat.showStored(q); } }),
                        el('div', { class: 'item-meta' }, meta + ' ',
                            q.verified ? el('span', { class: 'badge ok', text: 'doğrulandı' }) : null,
                            !q.is_successful ? el('span', { class: 'badge error', text: 'hata' }) : null)),
                    el('div', { class: 'item-actions' },
                        el('button', { class: 'link-btn', text: 'Soruyu düzenle', onclick: () => NIQ.chat.editQuestion(q.question) }),
                        el('button', { class: 'link-btn', text: 'Yeniden çalıştır', onclick: async (event) => {
                            event.currentTarget.disabled = true;
                            NIQ.go('chat'); await NIQ.chat.ask(q.question);
                        } }),
                        el('button', { class: 'link-btn', text: 'Sil', onclick: guarded(async () => {
                            if (!(await NIQ.confirm('Bu soruyu ve kayıtlı sonucunu geçmişten sil?'))) return;
                            await api('/queries/' + q.id, { method: 'DELETE', body: {} });
                            NIQ.views.history.enter();
                        }) }))));
            });
        },
    };

    // ---- raporlar ----
    const STATUS_LABEL = { ok: 'tamam', alert: 'uyarı', quiet: 'sessiz', error: 'hata' };

    NIQ.views.reports = {
        async enter() {
            const jobsList = NIQ.clear($('jobs-list'));
            const inbox = NIQ.clear($('inbox-list'));
            if (!NIQ.state.connected && !NIQ.state.profile) {
                // Zamanlanan işler profil bazlıdır; listeyi yine de göster
            }
            const jobs = (await api('/jobs')).data;
            if (!jobs.length) jobsList.appendChild(el('p', { class: 'muted', text: 'Henüz zamanlanmış rapor yok. Yukarıya ne zaman ne gönderilsin yaz.' }));
            jobs.forEach((job) => {
                const meta = [job.schedule_text, job.condition_text, job.channels.join(', ')].filter(Boolean).join(' · ');
                jobsList.appendChild(el('div', { class: 'item' },
                    el('div', { class: 'item-main' },
                        el('div', { class: 'item-title' }, (job.kind === 'alert' ? 'Uyarı: ' : 'Rapor: ') + job.name, ' ',
                            job.enabled ? null : el('span', { class: 'badge', text: 'duraklatıldı' })),
                        el('div', { class: 'item-meta', text: meta }),
                        el('div', { class: 'item-meta' }, job.last_run ? `Son: ${NIQ.timeAgo(job.last_run)} ` : 'Henüz çalışmadı ',
                            job.last_status ? el('span', { class: 'badge ' + job.last_status, text: STATUS_LABEL[job.last_status] || job.last_status }) : null)),
                    el('div', { class: 'item-actions' },
                        el('button', { class: 'link-btn', text: 'Çalıştır', onclick: guarded(async () => {
                            const result = (await api(`/jobs/${job.id}/run`, { method: 'POST', body: {} })).data;
                            NIQ.toast(result.message || 'Çalıştı');
                            NIQ.views.reports.enter();
                        }) }),
                        el('button', { class: 'link-btn', text: job.enabled ? 'Duraklat' : 'Sürdür', onclick: guarded(async () => {
                            await api(`/jobs/${job.id}/toggle`, { body: { enabled: !job.enabled } });
                            NIQ.views.reports.enter();
                        }) }),
                        el('button', { class: 'link-btn', text: 'Sil', onclick: guarded(async () => {
                            await api('/jobs/' + job.id, { method: 'DELETE', body: {} });
                            NIQ.views.reports.enter();
                        }) }))));
            });

            const runs = (await api('/inbox?limit=20')).data;
            if (!runs.length) inbox.appendChild(el('p', { class: 'muted', text: 'Sonuç yok.' }));
            runs.forEach((run) => {
                const row = el('div', { class: 'item' }, el('div', { class: 'item-main' },
                    el('div', { class: 'item-title' }, run.job_name + ' ', el('span', { class: 'badge ' + run.status, text: STATUS_LABEL[run.status] || run.status })),
                    el('div', { class: 'item-meta', text: `${NIQ.timeAgo(run.ran_at)} · ${run.message || ''}` })));
                if (run.rows && run.rows.length) {
                    const details = el('details', {}, el('summary', { text: `${run.row_count} satır` }));
                    NIQ.chat.renderTable(run.rows, 20).forEach((n) => details.appendChild(n));
                    row.firstChild.appendChild(details);
                }
                inbox.appendChild(row);
            });
        },
    };

    // ---- sözlük ----
    NIQ.views.glossary = {
        async enter() {
            const connected = NIQ.state.connected;
            if (!connected) { needConnection('glossary-list'); NIQ.clear($('notes-list')); return; }
            const settings = (await api('/settings')).data;
            $('g-values').checked = !!settings.ai_value_samples;
            await Promise.all([this.loadTerms(), this.loadNotes()]);
        },
        async loadTerms() {
            const list = NIQ.clear($('glossary-list'));
            const terms = (await api('/knowledge/glossary')).data;
            if (!terms.length) list.appendChild(el('p', { class: 'muted', text: 'Sözlük boş. Karışabilecek iş terimlerini ekle.' }));
            terms.forEach((term) => list.appendChild(el('div', { class: 'item' },
                el('div', { class: 'item-main' }, el('div', { class: 'item-title', text: term.term }),
                    el('div', { class: 'item-meta', text: term.definition + (term.sql_hint ? '  |  ' + term.sql_hint : '') })),
                el('div', { class: 'item-actions' }, el('button', { class: 'link-btn', text: 'Sil', onclick: guarded(async () => {
                    await api('/knowledge/glossary/' + term.id, { method: 'DELETE', body: {} });
                    this.loadTerms();
                }) })))));
        },
        async loadNotes() {
            const [tables, notes] = await Promise.all([api('/tables'), api('/knowledge/notes')]);
            const select = NIQ.clear($('n-table'));
            tables.data.forEach((t) => select.appendChild(el('option', { value: t, text: t })));
            const list = NIQ.clear($('notes-list'));
            Object.entries(notes.data).forEach(([table, info]) => list.appendChild(el('div', { class: 'item' },
                el('div', { class: 'item-main' }, el('div', { class: 'item-title', text: table }), el('div', { class: 'item-meta', text: info.note })),
                el('div', { class: 'item-actions' }, el('button', { class: 'link-btn', text: 'Sil', onclick: guarded(async () => {
                    await api('/knowledge/notes', { body: { table, note: '' } });
                    this.loadNotes();
                }) })))));
        },
    };

    function bindGlossary() {
        $('g-add').addEventListener('click', guarded(async () => {
            await api('/knowledge/glossary', { body: { term: $('g-term').value, definition: $('g-def').value, sql_hint: $('g-hint').value } });
            ['g-term', 'g-def', 'g-hint'].forEach((id) => { $(id).value = ''; });
            await NIQ.views.glossary.loadTerms();
        }));
        $('n-save').addEventListener('click', guarded(async () => {
            await api('/knowledge/notes', { body: { table: $('n-table').value, note: $('n-note').value } });
            $('n-note').value = '';
            await NIQ.views.glossary.loadNotes();
        }));
        $('g-profile').addEventListener('click', guarded(async () => {
            $('g-profile-status').textContent = 'Taranıyor...';
            const summary = (await api('/knowledge/profile', { method: 'POST', body: {} })).data;
            $('g-profile-status').textContent = `${summary.tables} tablo, ${summary.columns} kolon profillendi.`;
        }));
        $('g-values').addEventListener('change', async (event) => {
            try { await api('/settings', { body: { ai_value_samples: event.target.checked } }); }
            catch (error) { event.target.checked = !event.target.checked; if (!error.cancelled) NIQ.toast(error.message, 'error'); }
        });
    }

    // ---- ayarlar ----
    let settings = null;

    function priceText(model) {
        return model && model.price ? `Fiyat: $${model.price.input} giriş, $${model.price.output} çıkış (1M belirteç)` : '';
    }

    function renderModels() {
        const provider = $('s-provider').value;
        const select = NIQ.clear($('s-model'));
        const catalog = settings.catalog[provider] || [];
        const current = settings.models[provider];
        catalog.forEach((m) => select.appendChild(el('option', { value: m.id, text: m.name + (m.price ? `  ($${m.price.input}/$${m.price.output})` : '') })));
        if (current && !catalog.some((m) => m.id === current)) select.appendChild(el('option', { value: current, text: current + ' (özel)' }));
        select.value = current;
        $('s-model-custom').value = '';
        $('s-price').textContent = priceText(catalog.find((m) => m.id === select.value));
        const local = provider === 'local';
        $('s-key-field').hidden = local;
        $('s-url-field').hidden = !local;
        const hasKey = settings.providers[provider].has_key;
        $('s-key').value = '';
        $('s-key').placeholder = hasKey ? 'Kayıtlı (değiştirmek için yeni anahtar gir)' : 'Anahtarı yapıştır';
        $('s-clear-key').hidden = !hasKey || local;
    }

    function renderSettings() {
        const select = NIQ.clear($('s-provider'));
        Object.entries(settings.providers).forEach(([id, p]) => {
            select.appendChild(el('option', { value: id, text: p.label + (p.has_key || id === 'local' ? '' : ' (anahtar yok)') }));
        });
        select.value = settings.provider;
        $('s-url').value = settings.local_base_url || '';
        $('s-tokens').value = settings.max_output_tokens;
        $('s-rows').value = settings.row_limit;
        $('s-stt').value = settings.stt_model || '';
        $('s-analysis').checked = !!settings.ai_analysis;
        $('s-trust').checked = !!settings.trust_server_cert;
        renderModels();
    }

    function settingsPayload(extra) {
        const provider = $('s-provider').value;
        const model = $('s-model-custom').value.trim() || $('s-model').value;
        const body = {
            provider, models: { [provider]: model }, local_base_url: $('s-url').value.trim(),
            max_output_tokens: Number($('s-tokens').value), row_limit: Number($('s-rows').value),
            stt_model: $('s-stt').value.trim(), ai_analysis: $('s-analysis').checked, trust_server_cert: $('s-trust').checked,
        };
        if ($('s-key').value.trim()) body.api_key = $('s-key').value.trim();
        return { ...body, ...(extra || {}) };
    }

    async function loadNotify() {
        const data = (await api('/notify')).data;
        $('b-chat').value = data.telegram_chat_id || '';
        $('b-hook').value = data.webhook_url || '';
        $('b-token').value = '';
        $('b-token-note').textContent = data.telegram_has_token ? 'Belirteç kayıtlı. Değiştirmek için yenisini yaz.' : 'Belirteç yok. @BotFather ile bir bot oluştur.';
    }

    NIQ.views.settings = {
        async enter() {
            try {
                settings = (await api('/settings')).data;
                renderSettings();
                await NIQ.accounts.load();
                await loadNotify();
            } catch (error) {
                status('s-status', 'Ayarlar yüklenemedi: ' + error.message, 'error');
            }
        },
    };

    function bindSettings() {
        $('s-provider').addEventListener('change', renderModels);
        $('s-model').addEventListener('change', () => {
            $('s-price').textContent = priceText((settings.catalog[$('s-provider').value] || []).find((m) => m.id === $('s-model').value));
        });
        const save = async (extra, message) => {
            try {
                settings = (await api('/settings', { body: settingsPayload(extra) })).data;
                renderSettings();
                if (message) status('s-status', message, 'ok');
                await NIQ.refreshHealth();
                return true;
            } catch (error) {
                if (!error.cancelled) status('s-status', error.message, 'error');
                return false;
            }
        };
        $('s-save').addEventListener('click', () => save(null, 'Ayarlar kaydedildi.'));
        $('s-clear-key').addEventListener('click', () => save({ clear_api_key: true, api_key: '' }, 'Anahtar silindi.'));
        $('s-test').addEventListener('click', async () => {
            status('s-status', 'Test ediliyor...');
            if (!(await save(null, null))) return;
            try {
                const result = (await api('/settings/test', { method: 'POST', body: {} })).data;
                status('s-status', `Bağlantı başarılı: ${result.provider} / ${result.model} (${result.latency_ms} ms)`, 'ok');
            } catch (error) {
                status('s-status', error.message, 'error');
            }
        });

        const notifyBody = () => {
            const body = { telegram_chat_id: $('b-chat').value.trim(), webhook_url: $('b-hook').value.trim() };
            if ($('b-token').value.trim()) body.telegram_token = $('b-token').value.trim();
            return body;
        };
        $('b-save').addEventListener('click', async () => {
            try { await api('/notify', { body: notifyBody() }); await loadNotify(); status('b-status', 'Bildirim ayarları kaydedildi.', 'ok'); }
            catch (error) { if (!error.cancelled) status('b-status', error.message, 'error'); }
        });
        $('b-test').addEventListener('click', async () => {
            const channels = [];
            if ($('b-chat').value.trim()) channels.push('telegram');
            if ($('b-hook').value.trim()) channels.push('webhook');
            if (!channels.length) { status('b-status', 'Önce Telegram ya da webhook bilgisini gir', 'error'); return; }
            try {
                const result = (await api('/notify/test', { body: { channels } })).data;
                const lines = Object.entries(result).map(([k, v]) => `${k}: ${v === 'ok' ? 'gönderildi' : v}`);
                status('b-status', lines.join(' | '), Object.values(result).every((v) => v === 'ok') ? 'ok' : 'error');
            } catch (error) { status('b-status', error.message, 'error'); }
        });
    }

    // ---- açılış ----
    const VALID_VIEWS = ['chat', 'history', 'reports', 'glossary', 'settings'];

    async function boot() {
        NIQ.chat.bind();
        bindGlossary();
        bindSettings();
        NIQ.accounts.bind();
        NIQ.models.bind();
        document.querySelectorAll('#links button').forEach((b) => b.addEventListener('click', () => NIQ.go(b.dataset.view)));
        $('job-plan-btn').addEventListener('click', () => {
            const text = $('job-text').value.trim();
            if (!text) { NIQ.toast('Ne zaman ne gönderileceğini yaz'); return; }
            $('job-text').value = '';
            NIQ.chat.plan(text);
        });
        await NIQ.connect.init();
        if (await NIQ.onboarding.start()) return;
        const health = await NIQ.refreshHealth();
        if (health && health.database_connected && health.profile) NIQ.setConnection(health.profile);
        else if (!(await NIQ.connect.autoConnect())) NIQ.connect.show();
        const target = location.hash.slice(1);
        NIQ.go(VALID_VIEWS.includes(target) ? target : 'chat');
        if (health && !health.ai_service_available) NIQ.toast('AI hesabını Ayarlar\'dan bağla', 'error');
        $('q').focus();
    }

    document.addEventListener('DOMContentLoaded', boot);
})();
