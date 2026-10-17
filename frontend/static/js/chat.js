/**
 * Sohbet: tek komut kutusu, konuşmalı sorgu, sonuç/analiz/plan kartları, grafik, düzenle ve doğrula,
 * komut tahmini (/ ve Ctrl+K), sesli sorgu.
 */
(function () {
    const { $, el, api } = NIQ;
    const stream = $('stream');
    const input = $('q');
    const emptyPrompt = 'Verilerine sor…';
    const sendBtn = $('send');
    const micBtn = $('mic');
    let busy = false;
    let listening = false;
    let transcribing = false;
    let startingVoice = false;
    let connectionGeneration = 0;
    let voicePrefix = '';
    const recent = [];
    let recentIndex = -1;
    let selected = 0;
    let visibleCommands = [];

    // ---- komutlar ----
    const COMMANDS = [
        { name: '/analiz', desc: 'Derin analiz: birkaç sorguyla neden ve karşılaştırma', arg: 'soru', run: (arg) => ask(arg, { mode: 'analyze' }) },
        { name: '/rapor', desc: 'Zamanlanmış rapor ya da uyarı kur', arg: 'ne zaman ne gönderilsin', run: (arg) => plan(arg) },
        { name: '/yeni', desc: 'Yeni konuşma başlat', run: () => newConversation() },
        { name: '/baglan', desc: 'Bağlantı panelini aç', run: () => { NIQ.go('chat'); NIQ.connect.show(); } },
        { name: '/profil', desc: 'Hesap ve tercihler', run: () => NIQ.go('settings') },
        { name: '/temizle', desc: 'Ekranı temizle (konuşma bağlamı kalır)', run: () => { NIQ.clear(stream); refreshEmpty(); } },
    ];

    function hideCommands() { $('cmd-list').hidden = true; visibleCommands = []; }

    function showCommands(filter) {
        const text = filter.toLowerCase();
        visibleCommands = COMMANDS.filter((c) => c.name.startsWith(text));
        const box = NIQ.clear($('cmd-list'));
        if (!visibleCommands.length) { box.hidden = true; return; }
        selected = Math.min(selected, visibleCommands.length - 1);
        visibleCommands.forEach((command, index) => {
            box.appendChild(el('div', {
                class: 'cmd-item' + (index === selected ? ' sel' : ''),
                onmousedown: (event) => { event.preventDefault(); pick(command); },
            }, el('span', { class: 'cmd-name', text: command.name + (command.arg ? ' ' : '') }),
            el('span', { class: 'cmd-desc', text: command.desc })));
        });
        box.hidden = false;
    }

    function pick(command) {
        hideCommands();
        if (command.arg) {
            input.value = command.name + ' ';
            input.focus();
            resize();
        } else {
            input.value = '';
            resize();
            command.run();
        }
    }

    /** Yazılan metin bir komutsa çalıştırır. */
    function runCommand(text) {
        const match = text.match(/^(\/\S+)\s*(.*)$/s);
        if (!match) return false;
        const command = COMMANDS.find((c) => c.name === match[1].toLowerCase());
        if (!command) return false;
        if (command.arg && !match[2].trim()) { NIQ.toast(`${command.name} sonrasına ${command.arg} yaz`); return true; }
        command.run(match[2].trim());
        return true;
    }

    // ---- ortak yardımcılar ----
    function resize() {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    }

    function scrollDown() { window.scrollTo({ top: document.body.scrollHeight }); }

    function setBusy(value) {
        busy = value;
        sendBtn.disabled = value;
        sendBtn.classList.toggle('loading', value);
        sendBtn.setAttribute('aria-busy', String(value));
        sendBtn.setAttribute('aria-label', value ? 'Yanıt hazırlanıyor' : 'Gönder');
    }

    function refreshEmpty() {
        const hasMessages = stream.children.length > 0;
        $('empty').hidden = hasMessages || !NIQ.state.connected || !$('connect').hidden;
        $('greet').textContent = 'Ne öğrenmek istersin?';
    }

    async function loadSuggestions() {
        const box = NIQ.clear($('suggestions'));
        if (!NIQ.state.connected) return;
        try {
            const data = (await api('/suggestions')).data;
            data.questions.forEach((question) => box.appendChild(el('button', { class: 'chip', text: question, onclick: () => ask(question) })));
        } catch (e) { /* hazır sorular zorunlu değil */ }
    }

    async function newConversation() {
        cancelRecording();
        const old = NIQ.state.conversationId;
        NIQ.state.conversationId = NIQ.newId();
        NIQ.clear(stream);
        try { await api('/conversations/' + encodeURIComponent(old), { method: 'DELETE', body: {} }); } catch (e) { /* önemsiz */ }
        refreshEmpty();
        NIQ.go('chat');
        input.focus();
    }

    function onConnectionChange() {
        cancelRecording();
        const connected = NIQ.state.connected;
        input.placeholder = connected ? emptyPrompt : 'Önce bağlan';
        NIQ.clear(stream);
        NIQ.state.conversationId = NIQ.newId();
        if (connected) { $('connect').hidden = true; stream.hidden = false; loadSuggestions(); }
        else if (location.hash === '' || location.hash === '#chat') NIQ.connect.show();
        refreshEmpty();
    }

    // ---- tablo ve grafik ----
    function renderTable(rows, limit) {
        const shown = rows.slice(0, limit || 200);
        const columns = Object.keys(rows[0] || {});
        const head = el('tr', {}, columns.map((c) => el('th', { text: c })));
        const body = shown.map((row) => el('tr', {}, columns.map((c) => {
            const formatted = NIQ.formatValue(row[c]);
            return el('td', { class: formatted.cls, text: formatted.text, title: formatted.text });
        })));
        const wrap = el('div', { class: 'tablewrap' }, el('table', { class: 'grid' }, el('thead', {}, head), el('tbody', {}, body)));
        const parts = [wrap];
        if (rows.length > shown.length) parts.push(el('p', { class: 'note', text: `İlk ${shown.length} satır gösteriliyor. Tamamı için CSV ya da Excel indir.` }));
        return parts;
    }

    /** Grafik anlamlı mı: en az 2 satır ve bir sayısal kolon (etiket kolonu varsa ayrıca). */
    function chartable(rows) {
        if (rows.length < 2) return false;
        const columns = Object.keys(rows[0]);
        return columns.some((c) => rows.every((r) => r[c] === null || typeof r[c] === 'number')) && columns.length >= 1;
    }

    const PALETTE = ['#3b5bdb', '#2f9e44', '#e67700', '#ae3ec9', '#1098ad', '#d6336c'];

    function drawChart(container, rows) {
        NIQ.clear(container);
        const columns = Object.keys(rows[0] || {});
        const numeric = columns.filter((c) => rows.every((r) => r[c] === null || typeof r[c] === 'number'));
        const labelColumn = columns.find((c) => !numeric.includes(c));
        const series = numeric.filter((c) => c !== labelColumn).slice(0, 3);
        if (!series.length || typeof Chart === 'undefined') {
            container.appendChild(el('p', { class: 'note', text: 'Grafik için sayısal kolon bulunamadı.' }));
            return;
        }
        const points = rows.slice(0, 40);
        const labels = points.map((r, i) => (labelColumn ? NIQ.formatValue(r[labelColumn]).text : String(i + 1)));
        const temporal = !!labelColumn && points.every((r) => /^\d{4}-\d{2}/.test(String(r[labelColumn])));
        const canvas = el('canvas');
        container.appendChild(canvas);
        const text = getComputedStyle(document.body).color;
        new Chart(canvas, {
            type: temporal ? 'line' : 'bar',
            data: { labels, datasets: series.map((c, i) => ({
                label: c, data: points.map((r) => r[c]), borderColor: PALETTE[i], backgroundColor: PALETTE[i], tension: 0.25,
            })) },
            options: {
                animation: false, responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: series.length > 1, labels: { color: text } } },
                scales: { x: { ticks: { color: text } }, y: { ticks: { color: text } } },
            },
        });
        container.querySelector('canvas').parentElement.style.height = '300px';
    }

    // ---- dışa aktarma ----
    async function download(format, queryId, sql) {
        try {
            const response = await fetch('/api/export', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(queryId ? { query_id: queryId, format } : { sql, format }),
            });
            if (!response.ok) throw new Error((await response.json()).error || 'İndirilemedi');
            const blob = await response.blob();
            const link = el('a', { href: URL.createObjectURL(blob), download: 'niq-sonuc.' + format });
            document.body.appendChild(link);
            link.click();
            link.remove();
        } catch (error) {
            NIQ.toast(error.message, 'error');
        }
    }

    // ---- sonuç kartı ----
    function resultBlock(data, question) {
        const block = el('div');
        const rows = data.results || [];
        if (data.source) block.appendChild(el('p', { class: 'note', text: data.source }));
        if (data.explanation && data.mode !== 'analysis') block.appendChild(el('p', { class: 'explain', text: data.explanation }));
        if (data.attempts > 1) block.appendChild(el('p', { class: 'note', text: `Sorguyu ${data.attempts} denemede düzelttim.` }));

        const tableHolder = el('div');
        if (rows.length) renderTable(rows).forEach((n) => tableHolder.appendChild(n));
        else tableHolder.appendChild(el('p', { class: 'note', text: 'Sonuç boş.' }));
        block.appendChild(tableHolder);
        if (data.truncated) block.appendChild(el('p', { class: 'note warn', text: 'Satır limitine takıldı; bazı satırlar gösterilmiyor (Ayarlar > Satır limiti).' }));
        if (data.analysis && (data.analysis.summary || data.analysis.analysis)) {
            block.appendChild(el('p', { class: 'answer', text: data.analysis.summary }));
            if (data.analysis.analysis) block.appendChild(el('p', { class: 'explain', text: data.analysis.analysis }));
        }

        const chartHolder = el('div', { class: 'chartbox', hidden: true });
        const sqlHolder = el('div');
        block.appendChild(chartHolder);
        block.appendChild(sqlHolder);

        const actions = el('div', { class: 'actions' });
        let sqlOpen = false;
        let chartOn = false;
        const sqlBtn = el('button', { class: 'link-btn', text: 'SQL' });
        sqlBtn.addEventListener('click', () => {
            sqlOpen = !sqlOpen;
            sqlBtn.classList.toggle('on', sqlOpen);
            NIQ.clear(sqlHolder);
            if (sqlOpen) sqlHolder.appendChild(el('pre', { class: 'sqlbox', text: data.sql }));
        });
        actions.appendChild(sqlBtn);

        if (chartable(rows)) {
            const chartBtn = el('button', { class: 'link-btn', text: 'Grafik' });
            chartBtn.addEventListener('click', () => {
                chartOn = !chartOn;
                chartBtn.classList.toggle('on', chartOn);
                chartHolder.hidden = !chartOn;
                if (chartOn) drawChart(chartHolder, rows);
            });
            actions.appendChild(chartBtn);
        }
        if (data.query_id) {
            const verify = el('button', { class: 'link-btn', text: 'Doğru' });
            verify.addEventListener('click', async () => {
                try {
                    await api(`/queries/${data.query_id}/verify`, { body: { verified: true } });
                    verify.textContent = 'Doğrulandı';
                    verify.classList.add('on');
                } catch (error) { NIQ.toast(error.message, 'error'); }
            });
            actions.appendChild(verify);

            const edit = el('button', { class: 'link-btn', text: 'Düzenle' });
            edit.addEventListener('click', () => {
                NIQ.clear(sqlHolder);
                const area = el('textarea', { class: 'sql-edit' });
                area.value = data.sql;
                const status = el('p', { class: 'error-text' });
                const run = el('button', { class: 'btn primary', text: 'Çalıştır' });
                run.addEventListener('click', async () => {
                    status.textContent = '';
                    try {
                        const fixed = await api(`/queries/${data.query_id}`, { method: 'PUT', body: { sql: area.value } });
                        const next = resultBlock({ ...data, ...fixed, explanation: 'SQL düzeltildi ve doğrulanmış örnek olarak kaydedildi.', attempts: 1 }, question);
                        block.replaceWith(next);
                    } catch (error) {
                        status.textContent = error.message;
                    }
                });
                const cancel = el('button', { class: 'btn', text: 'Vazgeç', onclick: () => NIQ.clear(sqlHolder) });
                sqlHolder.append(area, status, el('div', { class: 'row' }, run, cancel));
            });
            actions.appendChild(edit);
        }
        if (rows.length && data.query_id) {
            actions.appendChild(el('button', { class: 'link-btn', text: 'CSV', onclick: () => download('csv', data.query_id) }));
            actions.appendChild(el('button', { class: 'link-btn', text: 'Excel', onclick: () => download('xlsx', data.query_id) }));
        }
        if (question) {
            actions.appendChild(el('button', {
                class: 'link-btn', text: 'Rapora çevir',
                onclick: () => {
                    input.value = `Her pazartesi 09:00'da şunu gönder: ${question}`;
                    resize();
                    input.focus();
                    NIQ.toast('Zamanı düzenleyip Enter\'a bas');
                },
            }));
        }
        block.appendChild(actions);

        if (data.follow_ups && data.follow_ups.length) {
            block.appendChild(el('div', { class: 'followups' }, data.follow_ups.map((f) => el('button', { class: 'chip', text: f, onclick: () => ask(f) }))));
        }
        return block;
    }

    function analysisBlock(data, question) {
        const block = el('div');
        block.appendChild(el('p', { class: 'answer', text: data.title || 'Analiz' }));
        String(data.answer || '').split(/\n{2,}/).forEach((paragraph) => block.appendChild(el('p', { class: 'explain', text: paragraph })));
        if (data.findings && data.findings.length) {
            block.appendChild(el('ul', { class: 'findings' }, data.findings.map((f) => el('li', { text: f }))));
        }
        if (data.steps && data.steps.length) {
            const details = el('details', {}, el('summary', { text: `Adımlar (${data.steps.length})` }));
            data.steps.forEach((step) => {
                const node = el('div', { class: 'step' }, el('p', { class: 'step-title', text: step.purpose || 'Sorgu' }),
                    el('pre', { class: 'sqlbox', text: step.sql }));
                if (step.ok && step.rows && step.rows.length) renderTable(step.rows, 15).forEach((n) => node.appendChild(n));
                if (!step.ok) node.appendChild(el('p', { class: 'error-text', text: step.error }));
                details.appendChild(node);
            });
            block.appendChild(details);
        }
        if (data.results && data.results.length) {
            block.appendChild(resultBlock({ ...data, explanation: '', mode: 'analysis' }, question));
        }
        return block;
    }

    function errorBlock(error, question) {
        const payload = error.payload || {};
        const block = el('div', {}, el('p', { class: 'error-text', text: error.message }));
        const planLimit = NIQ.state.provider === 'chatgpt' && error.message.includes('chatgpt.com/settings/usage');
        if (planLimit) block.appendChild(el('div', { class: 'actions' },
            el('a', { class: 'btn primary', href: 'https://chatgpt.com/settings/usage', rel: 'noreferrer', text: 'Kullanımı yönet' })));
        if (payload.sql) block.appendChild(el('pre', { class: 'sqlbox', text: payload.sql }));
        if (payload.error_code === 'AI_UNAVAILABLE') {
            block.appendChild(el('div', { class: 'actions' }, el('button', { class: 'link-btn', text: 'Profil', onclick: () => NIQ.go('settings') })));
        }
        if (question && error.status !== 503 && !planLimit) {
            block.appendChild(el('div', { class: 'actions' }, el('button', { class: 'link-btn', text: 'Yeniden dene', onclick: () => ask(question) })));
        }
        return block;
    }

    // ---- soru sorma ----
    function addMessage(question) {
        const body = el('div', {}, el('p', { class: 'note', text: 'Düşünüyorum...' }));
        const message = el('div', { class: 'msg' }, el('p', { class: 'msg-q', text: question }), body);
        stream.appendChild(message);
        $('empty').hidden = true;
        scrollDown();
        return body;
    }

    async function ask(question, options) {
        const text = (question || '').trim();
        if (!text || busy) return;
        if (!NIQ.state.connected) { NIQ.toast('Önce bir veritabanına bağlan', 'error'); NIQ.connect.show(); return; }
        NIQ.go('chat');
        setBusy(true);
        const body = addMessage(text);
        const started = Date.now();
        const pendingText = body.querySelector('p');
        pendingText.setAttribute('role', 'status');
        const waitTimer = setInterval(() => {
            const seconds = Math.floor((Date.now() - started) / 1000);
            pendingText.textContent = `Yanıt hazırlanıyor · ${seconds} sn`;
        }, 1000);
        try {
            const data = (await api('/query', { body: { question: text, conversation_id: NIQ.state.conversationId, mode: (options && options.mode) || 'auto' } }));
            NIQ.clear(body).appendChild(data.mode === 'analysis' ? analysisBlock(data, text) : resultBlock(data, text));
        } catch (error) {
            NIQ.clear(body).appendChild(error.cancelled ? el('p', { class: 'note', text: 'İptal edildi.' }) : errorBlock(error, text));
        } finally {
            clearInterval(waitTimer);
            setBusy(false);
            scrollDown();
        }
    }

    /** Geçmişteki bir kaydı akışta yeniden açar (kayıtlı sonuçlarla, sorgu çalıştırmadan). */
    function showStored(query) {
        const body = addMessage(query.question);
        const node = query.is_successful
            ? resultBlock({ success: true, sql: query.sql_query, results: query.query_results || [], explanation: query.result_message || '',
                query_id: query.id, attempts: 1, source: query.source }, query.question)
            : errorBlock(new Error(query.error_message || 'Sorgu hata verdi'), query.question);
        NIQ.clear(body).appendChild(node);
        scrollDown();
    }

    // ---- zamanlama planı ----
    const SCHEDULE_WORDS = /(\bher\s+(gün|sabah|akşam|pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar|saat|hafta|ay|\d+\s*(dakika|dk|saat))|haftalık|aylık|günlük|saatlik)/i;
    const ACTION_WORDS = /(gönder|bildir|haber ver|uyar|rapor|mail|telegram|at\b|ilet)/i;
    const ALERT_WORDS = /((haber ver|bildir|uyar|alarm).*(geçerse|aşarsa|düşerse|olursa|olunca|ise|varsa))|((geçerse|aşarsa|düşerse|olursa|varsa).*(haber ver|bildir|uyar))/i;

    function looksLikeSchedule(text) {
        return (SCHEDULE_WORDS.test(text) && ACTION_WORDS.test(text)) || ALERT_WORDS.test(text);
    }

    function planBlock(planData, text) {
        const block = el('div');
        const plan = planData;
        const label = plan.kind === 'alert' ? 'Uyarı' : 'Rapor';
        block.appendChild(el('p', { class: 'answer', text: `${label}: ${plan.name}` }));
        block.appendChild(el('p', { class: 'explain', text: [plan.schedule_text, plan.condition_text, 'kanal: ' + plan.channels.join(', ')].filter(Boolean).join(' · ') }));
        block.appendChild(el('pre', { class: 'sqlbox', text: plan.sql }));
        if (plan.preview && plan.preview.length) renderTable(plan.preview, 10).forEach((n) => block.appendChild(n));
        const status = el('p', { class: 'error-text' });
        const create = el('button', { class: 'btn primary', text: 'Kur' });
        const cancel = el('button', { class: 'btn', text: 'Vazgeç', onclick: () => block.replaceChildren(el('p', { class: 'note', text: 'Vazgeçildi.' })) });

        async function submit() {
            status.textContent = '';
            try {
                await api('/jobs', { body: { name: plan.name, kind: plan.kind, question: plan.question, sql: plan.sql,
                    schedule: plan.schedule, condition: plan.condition, channels: plan.channels } });
                block.replaceChildren(el('p', { class: 'answer', text: `Kuruldu: ${plan.name}` }),
                    el('p', { class: 'explain', text: `${plan.schedule_text}. Raporlar sayfasından yönetebilirsin.` }));
            } catch (error) {
                if (error.cancelled) return;
                status.textContent = error.message;
                if (/kaydedin/i.test(error.message) && !block.querySelector('[data-save-current]')) {
                    block.appendChild(el('button', {
                        class: 'btn', text: 'Bu bağlantıyı kaydet ve kur', 'data-save-current': '1',
                        onclick: async () => {
                            try { await api('/connections/current', { body: {} }); await submit(); }
                            catch (e) { if (!e.cancelled) status.textContent = e.message; }
                        },
                    }));
                }
            }
        }
        create.addEventListener('click', submit);
        block.append(status, el('div', { class: 'row' }, create, cancel));
        return block;
    }

    async function plan(text) {
        const request = (text || '').trim();
        if (!request || busy) return;
        if (!NIQ.state.connected) { NIQ.toast('Önce bir veritabanına bağlan', 'error'); NIQ.connect.show(); return; }
        NIQ.go('chat');
        setBusy(true);
        const body = addMessage(request);
        try {
            const data = (await api('/jobs/plan', { body: { text: request } })).data;
            NIQ.clear(body).appendChild(planBlock(data, request));
        } catch (error) {
            NIQ.clear(body).appendChild(errorBlock(error, null));
        } finally {
            setBusy(false);
            scrollDown();
        }
    }

    // ---- sesli sorgu ----
    async function finishRecording() {
        if (transcribing) return;
        listening = false;
        micBtn.classList.remove('rec');
        micBtn.setAttribute('aria-pressed', 'false');
        transcribing = true;
        micBtn.disabled = true;
        input.placeholder = 'Metne çeviriyorum...';
        const connection = connectionGeneration;
        try {
            const text = await NIQ.voice.stop();
            if (connection !== connectionGeneration) return;
            if (!text) { NIQ.toast('Ses anlaşılamadı, tekrar dene'); return; }
            input.value = voicePrefix + text;
            resize();
        } catch (error) {
            if (connection === connectionGeneration) NIQ.toast(error.message, 'error');
        } finally {
            transcribing = false;
            micBtn.disabled = false;
            input.classList.remove('voice-draft');
            input.readOnly = false;
            input.focus();
            input.placeholder = NIQ.state.connected ? emptyPrompt : 'Önce bağlan';
        }
    }

    function cancelRecording() {
        connectionGeneration++;
        NIQ.voice.cancel();
        listening = false;
        micBtn.classList.remove('rec');
        micBtn.setAttribute('aria-pressed', 'false');
        input.classList.remove('voice-draft');
        input.readOnly = false;
        input.placeholder = NIQ.state.connected ? emptyPrompt : 'Önce bağlan';
    }

    async function toggleMic() {
        if (transcribing || startingVoice) return;
        if (listening) { await finishRecording(); return; }
        if (!NIQ.state.connected) { NIQ.toast('Önce bir veritabanına bağlan', 'error'); return; }
        try {
            startingVoice = true;
            micBtn.disabled = true;
            voicePrefix = input.value.trimEnd();
            if (voicePrefix) voicePrefix += ' ';
            input.classList.add('voice-draft');
            input.readOnly = true;
            const connection = connectionGeneration;
            await NIQ.voice.start(() => finishRecording(), (text) => {
                if (connection !== connectionGeneration) return;
                input.value = voicePrefix + text;
                resize();
            });
            listening = true;
            micBtn.classList.add('rec');
            micBtn.setAttribute('aria-pressed', 'true');
            input.placeholder = 'Dinliyorum. Bitirince mikrofona tekrar bas';
        } catch (error) {
            input.classList.remove('voice-draft');
            input.readOnly = false;
            NIQ.toast(error.message, 'error');
        } finally {
            startingVoice = false;
            micBtn.disabled = false;
        }
    }

    // ---- gönderme ----
    function submit() {
        if (listening || startingVoice || transcribing) return;
        const text = input.value.trim();
        if (!text) return;
        hideCommands();
        input.value = '';
        resize();
        recent.unshift(text);
        recentIndex = -1;
        if (text.startsWith('/')) { if (runCommand(text)) return; }
        if (looksLikeSchedule(text)) plan(text);
        else ask(text);
    }

    function bind() {
        sendBtn.addEventListener('click', submit);
        micBtn.addEventListener('click', toggleMic);
        $('brand').addEventListener('click', newConversation);
        input.addEventListener('input', () => {
            resize();
            const value = input.value;
            if (value.startsWith('/') && !/\s/.test(value)) { selected = 0; showCommands(value); } else hideCommands();
        });
        input.addEventListener('keydown', (event) => {
            const open = !$('cmd-list').hidden;
            if (open && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
                event.preventDefault();
                selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + visibleCommands.length) % visibleCommands.length;
                showCommands(input.value);
                return;
            }
            if (open && (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey))) {
                event.preventDefault();
                const command = visibleCommands[selected];
                if (command) { pick(command); return; }
            }
            if (event.key === 'Escape') { hideCommands(); return; }
            if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(); return; }
            if (event.key === 'ArrowUp' && !input.value && recent.length) {
                event.preventDefault();
                recentIndex = Math.min(recentIndex + 1, recent.length - 1);
                input.value = recent[recentIndex];
                resize();
            }
        });
        input.addEventListener('blur', () => setTimeout(hideCommands, 120));
        document.addEventListener('keydown', (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
                event.preventDefault();
                NIQ.go('chat');
                input.focus();
                if (!input.value) { input.value = '/'; showCommands('/'); }
            } else if (event.key === 'Escape' && document.querySelector('.view.active') !== $('view-chat')) {
                NIQ.go('chat');
            }
        });
        if (!NIQ.voice.supported()) micBtn.hidden = true;
    }

    function editQuestion(text) {
        NIQ.go('chat');
        cancelRecording();
        input.value = text;
        resize(); input.focus();
    }
    NIQ.chat = { ask, plan, bind, refreshEmpty, onConnectionChange, newConversation, renderTable, showStored, cancelRecording, editQuestion };
})();
