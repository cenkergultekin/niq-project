/**
 * Bağlantı paneli: tür seçimi, yapıştırarak doldurma, adım adım test, kayıtlı profiller, yerel keşif.
 * Kritik adımlarda (profil kaydet/sil, SSH anahtarına güven) NIQ.api onay penceresini kendisi gösterir.
 */
(function () {
    const { $, el, api } = NIQ;
    const state = { types: [], type: 'mssql', pasted: '', profiles: [], lastProfile: '' };

    function status(message, kind) {
        const box = $('db-status');
        box.textContent = message || '';
        box.className = 'status' + (kind ? ' ' + kind : '');
    }

    function currentType() {
        return state.types.find((t) => t.id === state.type) || { id: state.type, default_port: null, host_suggestions: [] };
    }

    function value(id) { return $(id).value.trim(); }

    function getPayload() {
        const fileBased = !!currentType().file_based;
        const payload = {
            type: state.type,
            host: fileBased ? '' : value('db_server'),
            port: !fileBased && $('db_port').value ? Number($('db_port').value) : null,
            database: value('db_database'),
            user: value('db_username'),
            password: $('db_password').value,
            name: value('db_profile_name'),
            driver: value('db_driver'),
            auth: state.type === 'mssql' ? $('db_auth').value : '',
            ssl: $('db_ssl').value,
            ssl_ca: value('db_ssl_ca'),
            ssh_host: value('db_ssh_host'),
            ssh_port: $('db_ssh_port').value ? Number($('db_ssh_port').value) : '',
            ssh_user: value('db_ssh_user'),
            ssh_key: value('db_ssh_key'),
            ssh_password: $('db_ssh_password').value,
        };
        if (state.pasted) payload.connection_input = state.pasted;
        return payload;
    }

    function validate(payload) {
        const fileBased = !!currentType().file_based;
        const windows = payload.auth === 'windows';
        if (!payload.database && !state.pasted) return fileBased ? 'Dosya yolunu gir' : 'Veritabanı adını gir';
        if (!fileBased && !windows && !payload.user && !state.pasted) return 'Kullanıcı adını gir';
        if (!fileBased && !windows && !payload.password && !state.pasted) return 'Parolayı gir';
        if (payload.ssh_host && !payload.ssh_user) return 'SSH kullanıcısını gir';
        if (payload.ssl === 'verify' && state.type !== 'mssql' && !payload.ssl_ca) return 'Sertifikayı doğrulamak için CA dosyasını gir';
        return null;
    }

    function applyAuth() {
        const windows = state.type === 'mssql' && $('db_auth').value === 'windows';
        const fileBased = !!currentType().file_based;
        document.querySelectorAll('[data-field="user"],[data-field="password"]').forEach((node) => {
            node.hidden = windows || fileBased;
        });
    }

    function applyType(typeId, keepValues) {
        if (!keepValues && state.type !== typeId) {
            state.pasted = '';
            ['db_paste', 'db_server', 'db_port', 'db_database', 'db_username', 'db_password',
                'db_driver', 'db_profile_name', 'db_ssl', 'db_ssl_ca', 'db_ssh_host', 'db_ssh_port',
                'db_ssh_user', 'db_ssh_key', 'db_ssh_password'].forEach((id) => { $(id).value = ''; });
            NIQ.clear($('db-diagnostics'));
            $('db-list-select').hidden = true;
            status('');
        }
        state.type = typeId;
        const type = currentType();
        const fileBased = !!type.file_based;
        document.querySelectorAll('#db-types .chip').forEach((chip) => chip.classList.toggle('active', chip.dataset.type === typeId));
        document.querySelectorAll('[data-field="host"],[data-field="port"]').forEach((node) => { node.hidden = fileBased; });
        $('auth-field').hidden = typeId !== 'mssql';
        $('driver-field').hidden = typeId !== 'mssql';
        $('ssl-field').hidden = fileBased;
        if (typeId !== 'mssql') $('db_auth').value = 'sql';
        $('ssl-ca-field').hidden = fileBased || $('db_ssl').value !== 'verify';
        $('db_database_label').textContent = fileBased ? 'Dosya yolu' : 'Veritabanı';
        $('db_database').placeholder = fileBased ? 'C:\\veri\\sirket.db' : 'Veritabanı adı';
        $('db-list-btn').hidden = fileBased;
        $('db_port').placeholder = type.default_port || '';
        if (!keepValues) $('db_port').value = '';
        $('db_server').placeholder = (type.host_suggestions && type.host_suggestions[0]) || 'localhost';
        const list = NIQ.clear($('db-host-list'));
        (type.host_suggestions || []).forEach((host) => list.appendChild(el('option', { value: host })));
        $('db-type-hint').textContent = type.driver_available === false
            ? 'Sürücü eksik: ' + (type.install_hint || '')
            : (type.install_hint || '');
        $('db-permission-hint').textContent = type.permission_hint ? 'Öneri: ' + type.permission_hint : '';
        applyAuth();
    }

    function fill(values) {
        if (values.type && state.types.some((t) => t.id === values.type)) applyType(values.type, true);
        if (values.host) $('db_server').value = values.host;
        if (values.port) $('db_port').value = values.port;
        if (values.database) $('db_database').value = values.database;
        if (values.user) $('db_username').value = values.user;
        const options = values.options || {};
        if (options.driver) $('db_driver').value = options.driver;
        if (options.auth) { $('db_auth').value = options.auth; applyAuth(); }
        if (options.ssl) $('db_ssl').value = options.ssl;
        if (options.ssl_ca) $('db_ssl_ca').value = options.ssl_ca;
        if (options.ssh_host) $('db_ssh_host').value = options.ssh_host;
        if (options.ssh_port) $('db_ssh_port').value = options.ssh_port;
        if (options.ssh_user) $('db_ssh_user').value = options.ssh_user;
        if (options.ssh_key) $('db_ssh_key').value = options.ssh_key;
        $('ssl-ca-field').hidden = $('db_ssl').value !== 'verify';
    }

    async function onPaste() {
        const text = value('db_paste');
        state.pasted = '';
        if (!text) return;
        try {
            const parsed = (await api('/db/parse', { body: { input: text } })).data;
            if (!parsed.recognized) { status('Bağlantı metni tanınmadı. URL, ODBC dizesi veya dosya yolu gir.', 'error'); return; }
            ['db_server', 'db_port', 'db_database', 'db_username', 'db_password', 'db_driver',
                'db_profile_name', 'db_ssl', 'db_ssl_ca', 'db_ssh_host', 'db_ssh_port', 'db_ssh_user',
                'db_ssh_key', 'db_ssh_password'].forEach((id) => { $(id).value = ''; });
            $('db_auth').value = 'sql';
            fill(parsed);
            state.pasted = text;
            const needsPassword = !parsed.has_password && !currentType().file_based && $('db_auth').value !== 'windows';
            status('Alanlar dolduruldu' + (needsPassword ? '. Parolayı gir.' : '.'), 'ok');
        } catch (error) {
            status(error.message, 'error');
        }
    }

    function renderDiagnostics(result) {
        const labels = { ok: 'Tamam', fail: 'Hata', warn: 'Uyarı', skip: 'Atlandı' };
        const list = NIQ.clear($('db-diagnostics'));
        result.steps.forEach((step) => {
            list.appendChild(el('li', { class: step.status },
                el('span', { class: 't', text: `${step.label}: ${labels[step.status] || step.status}` }),
                step.detail ? ' ' + step.detail : '',
                step.hint ? el('span', { class: 'h', text: step.hint }) : null));
        });
    }

    async function runTest() {
        const payload = getPayload();
        const problem = validate(payload);
        if (problem) { status(problem, 'error'); return; }
        status('Test ediliyor...');
        try {
            const result = (await api('/db/diagnose', { body: payload })).data;
            renderDiagnostics(result);
            status(result.ok ? 'Test başarılı. Bağlanabilirsin.' : 'Test başarısız. İpuçlarına bak.', result.ok ? 'ok' : 'error');
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error');
        }
    }

    async function listDatabases() {
        const payload = getPayload();
        if (!payload.user && !state.pasted && payload.auth !== 'windows') { status('Önce sunucu, kullanıcı ve parolayı gir', 'error'); return; }
        status('Veritabanları getiriliyor...');
        try {
            const names = (await api('/db/databases', { body: payload })).data;
            const select = NIQ.clear($('db-list-select'));
            select.appendChild(el('option', { value: '', text: 'Listeden seç' }));
            names.forEach((n) => select.appendChild(el('option', { value: n, text: n })));
            select.hidden = false;
            status(names.length + ' veritabanı bulundu.', 'ok');
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error');
        }
    }

    async function connectNew() {
        const payload = getPayload();
        const problem = validate(payload);
        if (problem) { status(problem, 'error'); return; }
        status('Bağlanılıyor...');
        $('db-connect-btn').disabled = true;
        try {
            const profile = (await api('/set_db', { body: payload })).data;
            $('db_password').value = '';
            $('db_ssh_password').value = '';
            $('db_paste').value = '';
            state.pasted = '';
            await afterConnect(profile);
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error');
        } finally {
            $('db-connect-btn').disabled = false;
        }
    }

    async function connectSaved(profile) {
        status(profile.name + ' bağlanılıyor...');
        try {
            const connected = (await api(`/connections/${encodeURIComponent(profile.id)}/connect`, { method: 'POST', body: {} })).data;
            await afterConnect(connected);
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error');
        }
    }

    async function afterConnect(profile) {
        status('');
        NIQ.setConnection(profile);
        NIQ.toast('Bağlandı: ' + (profile.name || profile.database));
        hide();
    }

    async function saveProfile() {
        const payload = getPayload();
        const active = NIQ.state.profile;
        const sameConnection = active && active.type === payload.type && active.database === payload.database
            && active.user === payload.user && (active.host || '') === (payload.host || (currentType().file_based ? '' : 'localhost'))
            && (active.port || null) === (payload.port || currentType().default_port || null);
        const problem = sameConnection ? null : validate(payload);
        if (problem) { status(problem, 'error'); return; }
        try {
            const saved = (await api(sameConnection ? '/connections/current' : '/connections', {
                body: sameConnection ? { name: payload.name || active.name } : payload,
            })).data;
            status('Profil kaydedildi. Parola bu bilgisayarın güvenli deposunda.', 'ok');
            await loadProfiles();
            if (NIQ.state.connected) {
                // Zamanlama için bağlı profilin kayıtlı olması gerekir: kayıtlı kopyaya geç
                if (sameConnection) NIQ.state.profile = saved;
            }
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error');
        }
    }

    async function closeConnection() {
        try {
            await api('/clear_db', { body: {} });
            NIQ.setConnection(null);
            status('Bağlantı kapatıldı.', 'ok');
        } catch (error) {
            status(error.message, 'error');
        }
    }

    async function loadProfiles() {
        const box = $('saved-connections');
        try {
            const response = await api('/connections');
            state.profiles = response.data;
            state.lastProfile = response.last_connection_id || '';
        } catch (error) {
            return;
        }
        NIQ.clear(box);
        box.hidden = !state.profiles.length;
        state.profiles.forEach((profile) => {
            box.appendChild(el('button', { class: 'chip', title: 'Bağlan', text: profile.name, onclick: () => connectSaved(profile) }));
            box.appendChild(el('button', {
                class: 'link-btn', title: profile.name + ' profilini sil', text: 'sil',
                onclick: async () => {
                    try { await api(`/connections/${encodeURIComponent(profile.id)}`, { method: 'DELETE', body: {} }); await loadProfiles(); }
                    catch (error) { if (!error.cancelled) status(error.message, 'error'); }
                },
            }));
        });
    }

    async function loadDiscovery() {
        try {
            const found = (await api('/db/discover')).data;
            const box = NIQ.clear($('discovered'));
            found.local_servers.forEach((server) => {
                box.appendChild(el('button', {
                    class: 'chip', text: `Bulundu: ${server.label} (localhost:${server.port})`,
                    onclick: () => { fill({ type: server.type, host: server.host, port: server.port }); $('db_database').focus(); },
                }));
            });
            box.hidden = !found.local_servers.length;
        } catch (e) { /* keşif zorunlu değil */ }
    }

    function show() {
        $('connect').hidden = false;
        $('empty').hidden = true;
        $('stream').hidden = true;
        loadProfiles();
        loadDiscovery();
    }

    function hide() {
        $('connect').hidden = true;
        $('stream').hidden = false;
        NIQ.chat.refreshEmpty();
    }

    /** Açılışta son kullanılan kayıtlı profile sessizce bağlanır. */
    async function autoConnect() {
        try {
            await loadProfiles();
            const profile = state.profiles.find((p) => p.id === state.lastProfile);
            if (!profile) return false;
            const connected = (await api(`/connections/${encodeURIComponent(profile.id)}/connect`, { method: 'POST', body: {} })).data;
            NIQ.setConnection(connected);
            return true;
        } catch (error) {
            return false;  // ssh onayı gibi etkileşim gerekiyorsa panel açılır
        }
    }

    NIQ.connect = { show, hide, autoConnect, loadProfiles, saveProfile };

    NIQ.connect.init = async function () {
        try {
            state.types = (await api('/db/types')).data;
        } catch (e) {
            state.types = [{ id: 'mssql', label: 'SQL Server', default_port: 1433, host_suggestions: ['localhost'] }];
        }
        const box = $('db-types');
        state.types.forEach((type) => {
            box.appendChild(el('button', {
                class: 'chip' + (type.driver_available === false ? ' missing' : ''), 'data-type': type.id, text: type.label,
                onclick: () => applyType(type.id),
            }));
        });
        applyType(state.type);
        $('db_auth').addEventListener('change', applyAuth);
        $('db_ssl').addEventListener('change', () => { $('ssl-ca-field').hidden = $('db_ssl').value !== 'verify'; });
        $('db_paste').addEventListener('change', onPaste);
        $('db_paste').addEventListener('paste', () => setTimeout(onPaste, 0));
        $('db-test-btn').addEventListener('click', runTest);
        $('db-connect-btn').addEventListener('click', connectNew);
        $('db-save-btn').addEventListener('click', saveProfile);
        $('db-close-btn').addEventListener('click', closeConnection);
        $('db-list-btn').addEventListener('click', listDatabases);
        $('db-list-select').addEventListener('change', (e) => { if (e.target.value) $('db_database').value = e.target.value; });
        $('conn-pill').addEventListener('click', () => { NIQ.go('chat'); show(); });
    };
})();
