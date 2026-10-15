/**
 * Bağlantı sihirbazı: tür seçimi, yapıştırarak doldurma, adım adım test, kayıtlı profiller,
 * kritik adımlarda onay ve soru yazarken tablo tahmini.
 */
(function () {
    const $ = (id) => document.getElementById(id);
    const state = { types: [], type: 'mssql', pasted: '', profiles: [] };

    function status(message, kind) {
        const el = $('db-status');
        if (!el) return;
        el.textContent = message;
        el.className = 'status-message ' + (kind || 'info');
    }

    /** İstek atar; 409 onay istenirse özeti gösterip kullanıcı kabul ederse tekrar dener. */
    async function call(path, method, body) {
        const send = async (payload) => {
            const response = await fetch('/api' + path, {
                method: method || 'GET',
                headers: { 'Content-Type': 'application/json' },
                body: payload ? JSON.stringify(payload) : undefined,
            });
            return { response, result: await response.json() };
        };
        let { response, result } = await send(body);
        if (response.status === 409 && result.needs_approval) {
            if (!window.confirm(result.summary + '\n\nOnaylıyor musunuz?')) throw new Error('İşlem iptal edildi');
            ({ response, result } = await send({ ...(body || {}), approved: true }));
        }
        if (!response.ok || result.success === false) throw new Error(result.error || 'İstek başarısız');
        return result;
    }

    function currentType() {
        return state.types.find((t) => t.id === state.type) || { id: state.type, default_port: null, host_suggestions: [] };
    }

    function getPayload() {
        const type = currentType();
        const payload = {
            type: state.type,
            host: $('db_server').value.trim(),
            port: $('db_port').value ? Number($('db_port').value) : null,
            database: $('db_database').value.trim(),
            user: $('db_username').value.trim(),
            password: $('db_password').value,
            name: ($('db_profile_name') && $('db_profile_name').value.trim()) || '',
            driver: ($('db_driver') && $('db_driver').value.trim()) || '',
        };
        if (state.pasted) payload.connection_input = state.pasted;
        if (type.file_based) {
            payload.host = '';
            payload.port = null;
        }
        return payload;
    }

    function validate(payload) {
        const type = currentType();
        if (!payload.database && !state.pasted) return type.file_based ? 'Dosya yolunu girin' : 'Veritabanı adını girin';
        if (!type.file_based && !payload.user && !state.pasted) return 'Kullanıcı adını girin';
        if (!type.file_based && !payload.password && !state.pasted) return 'Parolayı girin';
        return null;
    }

    function applyType(typeId, keepValues) {
        state.type = typeId;
        const type = currentType();
        document.querySelectorAll('#db-types .chip').forEach((chip) => chip.classList.toggle('active', chip.dataset.type === typeId));
        const fileBased = !!type.file_based;
        document.querySelectorAll('[data-field="host"],[data-field="port"],[data-field="user"],[data-field="password"]')
            .forEach((el) => { el.style.display = fileBased ? 'none' : ''; });
        $('db_driver_group').style.display = typeId === 'mssql' ? '' : 'none';
        $('db_database_label').textContent = fileBased ? 'Dosya yolu' : 'Veritabanı';
        $('db_database').placeholder = fileBased ? 'C:\\veri\\sirket.db' : 'Veritabanı adı';
        $('db-list-btn').style.display = fileBased ? 'none' : '';
        $('db_port').placeholder = type.default_port || '';
        if (!keepValues || !$('db_port').value) $('db_port').value = '';
        $('db_server').placeholder = (type.host_suggestions && type.host_suggestions[0]) || 'localhost';
        const list = $('db-host-list');
        list.innerHTML = '';
        (type.host_suggestions || []).forEach((h) => {
            const option = document.createElement('option');
            option.value = h;
            list.appendChild(option);
        });
        $('db-type-hint').textContent = type.driver_available === false ? ('Sürücü eksik: ' + (type.install_hint || '')) : (type.install_hint || '');
        $('db-permission-hint').textContent = type.permission_hint ? ('Öneri: ' + type.permission_hint) : '';
    }

    function renderTypes() {
        const box = $('db-types');
        box.innerHTML = '';
        state.types.forEach((t) => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'chip' + (t.driver_available === false ? ' missing' : '');
            chip.dataset.type = t.id;
            chip.textContent = t.label;
            chip.addEventListener('click', () => applyType(t.id));
            box.appendChild(chip);
        });
        applyType(state.type);
    }

    function fill(values) {
        if (values.type && state.types.some((t) => t.id === values.type)) applyType(values.type, true);
        if (values.host) $('db_server').value = values.host;
        if (values.port) $('db_port').value = values.port;
        if (values.database) $('db_database').value = values.database;
        if (values.user) $('db_username').value = values.user;
        if (values.options && values.options.driver) $('db_driver').value = values.options.driver;
    }

    async function onPaste() {
        const text = $('db_paste').value.trim();
        state.pasted = '';
        if (!text) return;
        try {
            const parsed = (await call('/db/parse', 'POST', { input: text })).data;
            if (!parsed.recognized) {
                status('Bağlantı metni tanınmadı. URL, ODBC dizesi veya dosya yolu girin.', 'error');
                return;
            }
            fill(parsed);
            state.pasted = text;
            status('Alanlar otomatik dolduruldu' + (parsed.has_password ? ' (parola metinden alınacak)' : (currentType().file_based ? '' : '; parolayı girin')) + '.', 'success');
        } catch (error) {
            status(error.message, 'error');
        }
    }

    function renderDiagnostics(result) {
        const labels = { ok: 'Tamam', fail: 'Hata', warn: 'Uyarı', skip: 'Atlandı' };
        const list = $('db-diagnostics');
        list.innerHTML = '';
        result.steps.forEach((step) => {
            const li = document.createElement('li');
            li.className = step.status;
            const title = document.createElement('strong');
            title.textContent = `${step.label}: ${labels[step.status] || step.status}`;
            li.appendChild(title);
            if (step.detail) li.appendChild(document.createTextNode('  ' + step.detail));
            if (step.hint) {
                const hint = document.createElement('span');
                hint.className = 'diag-hint';
                hint.textContent = step.hint;
                li.appendChild(hint);
            }
            list.appendChild(li);
        });
    }

    async function runTest() {
        const payload = getPayload();
        const problem = validate(payload);
        if (problem) { status(problem, 'error'); return; }
        status('Test ediliyor...', 'info');
        try {
            const result = (await call('/db/diagnose', 'POST', payload)).data;
            renderDiagnostics(result);
            status(result.ok ? 'Bağlantı testi başarılı. Bağlan düğmesini kullanabilirsiniz.' : 'Test başarısız. Yukarıdaki ipuçlarına bakın.', result.ok ? 'success' : 'error');
        } catch (error) {
            status(error.message, 'error');
        }
    }

    async function listDatabases() {
        const payload = getPayload();
        if (!payload.user && !state.pasted) { status('Önce sunucu, kullanıcı ve parolayı girin', 'error'); return; }
        status('Veritabanları getiriliyor...', 'info');
        try {
            const names = (await call('/db/databases', 'POST', payload)).data;
            const select = $('db-list-select');
            select.innerHTML = '<option value="">Listeden seçin</option>';
            names.forEach((n) => {
                const option = document.createElement('option');
                option.value = n;
                option.textContent = n;
                select.appendChild(option);
            });
            select.style.display = '';
            status(`${names.length} veritabanı bulundu.`, 'success');
        } catch (error) {
            status(error.message, 'error');
        }
    }

    async function saveProfile() {
        const payload = getPayload();
        const problem = validate(payload);
        if (problem) { status(problem, 'error'); return; }
        try {
            await call('/connections', 'POST', payload);
            status('Profil kaydedildi.', 'success');
            await loadProfiles();
        } catch (error) {
            status(error.message, 'error');
        }
    }

    async function connectSaved(profile) {
        status(`${profile.name} bağlanılıyor...`, 'info');
        try {
            await call(`/connections/${encodeURIComponent(profile.id)}/connect`, 'POST', {});
            fill(profile);
            if (window.app) await window.app.onConnected(profile.database);
        } catch (error) {
            status(error.message, 'error');
        }
    }

    async function deleteProfile(profile) {
        try {
            await call(`/connections/${encodeURIComponent(profile.id)}`, 'DELETE', {});
            await loadProfiles();
        } catch (error) {
            status(error.message, 'error');
        }
    }

    async function loadProfiles() {
        const box = $('saved-connections');
        try {
            state.profiles = (await call('/connections')).data;
        } catch (error) {
            return;
        }
        box.innerHTML = '';
        box.style.display = state.profiles.length ? '' : 'none';
        state.profiles.forEach((profile) => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'chip';
            chip.textContent = profile.name;
            chip.title = 'Bağlan';
            chip.addEventListener('click', () => connectSaved(profile));
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'chip';
            remove.textContent = 'Sil';
            remove.title = `${profile.name} profilini sil`;
            remove.addEventListener('click', () => deleteProfile(profile));
            box.appendChild(chip);
            box.appendChild(remove);
        });
    }

    async function loadDiscovery() {
        try {
            const found = (await call('/db/discover')).data;
            const box = $('discovered-servers');
            box.innerHTML = '';
            found.local_servers.forEach((server) => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'chip';
                chip.textContent = `Bulundu: ${server.label} (localhost:${server.port})`;
                chip.addEventListener('click', () => {
                    fill({ type: server.type, host: server.host, port: server.port });
                    $('db_database').focus();
                });
                box.appendChild(chip);
            });
            box.style.display = found.local_servers.length ? '' : 'none';
        } catch (error) { /* keşif zorunlu değil */ }
    }

    /** Soru yazılırken ilgili tabloları tahmin edip (henüz seçim yoksa) işaretler. */
    function setupTableSuggestion() {
        const question = $('question');
        const select = $('table_name');
        if (!question || !select) return;
        let timer = null;
        question.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(async () => {
                if (!window.app || !window.app.state.isConnected) return;
                if (question.value.trim().length < 6) return;
                if ([...select.options].some((o) => o.selected)) return;
                try {
                    const names = (await call('/suggest_tables', 'POST', { question: question.value })).data;
                    if (!names.length) return;
                    [...select.options].forEach((o) => { o.selected = names.includes(o.value); });
                    select.dispatchEvent(new Event('change'));
                    window.app.showStatus('Önerilen tablolar seçildi: ' + names.join(', '), 'info');
                } catch (error) { /* öneri zorunlu değil */ }
            }, 700);
        });
    }

    window.NIQConnections = { getPayload, validate, call };

    document.addEventListener('DOMContentLoaded', async () => {
        if (!$('db-section')) return;
        try {
            state.types = (await call('/db/types')).data;
        } catch (error) {
            state.types = [{ id: 'mssql', label: 'SQL Server', default_port: 1433, host_suggestions: ['localhost'] }];
        }
        renderTypes();
        $('db_paste').addEventListener('change', onPaste);
        $('db_paste').addEventListener('paste', () => setTimeout(onPaste, 0));
        $('db-test-btn').addEventListener('click', runTest);
        $('db-list-btn').addEventListener('click', listDatabases);
        $('db-save-btn').addEventListener('click', saveProfile);
        $('db-list-select').addEventListener('change', (e) => { if (e.target.value) $('db_database').value = e.target.value; });
        ['db_server', 'db_port', 'db_database', 'db_username'].forEach((id) =>
            $(id).addEventListener('input', () => { state.pasted = ''; }));
        loadProfiles();
        loadDiscovery();
        setupTableSuggestion();
    });
})();
