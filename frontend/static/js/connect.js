/**
 * Bağlantı paneli: tür seçimi, yapıştırarak doldurma, adım adım test, kayıtlı profiller, yerel keşif.
 * Kritik adımlarda (profil kaydet/sil, SSH anahtarına güven) NIQ.api onay penceresini kendisi gösterir.
 */
(function () {
    const { $, el, api } = NIQ;
    const state = { types: [], type: 'mssql', pasted: '', profiles: [], lastProfile: '', step: 'source', desktop: null,
        credentialToken: '', needsPassword: true, catalog: [], selected: new Set(), scopeMode: 'recommended' };

    function goStep(step) {
        state.step = step;
        ['source', 'access', 'check'].forEach((name) => { $('db-' + name + '-panel').hidden = name !== step; });
        document.querySelectorAll('[data-connect-step]').forEach((button) => {
            if (button.dataset.connectStep === step) button.setAttribute('aria-current', 'step');
            else button.removeAttribute('aria-current');
        });
        const fileBased = !!currentType().file_based;
        $('db-step-hint').textContent = step === 'source' ? 'Erişilebilir veritabanlarını bul, yalnızca kullanmak istediklerini seç.'
            : step === 'check' ? 'Bağlantıyı kontrol et, ardından bağlan.'
            : fileBased ? 'Veritabanı dosyasını seç.'
            : state.type === 'mssql' && $('db_auth').value === 'windows'
                ? 'Windows hesabının erişebildiği veritabanlarını getir.'
                : 'Kullanıcı adı ve parolayı gir, ardından veritabanlarını getir.';
        const payload = getPayload();
        $('db-summary').textContent = fileBased ? payload.database || 'Dosya seçilmedi'
            : `${currentType().label || state.type} · ${payload.host || 'localhost'}:${payload.port || currentType().default_port} · ${payload.database || 'Veritabanı seçilmedi'}`;
        $('db-save-btn').hidden = (NIQ.state.profile?.scope_count || 1) > 1;
    }

    function clearForm() {
        state.pasted = '';
        state.credentialToken = '';
        state.needsPassword = true;
        ['db_paste', 'db_server', 'db_port', 'db_database', 'db_username', 'db_password',
            'db_driver', 'db_profile_name', 'db_ssl', 'db_ssl_ca', 'db_ssh_host', 'db_ssh_port',
            'db_ssh_user', 'db_ssh_key', 'db_ssh_password'].forEach((id) => { $(id).value = ''; });
        $('db_auth').value = 'sql';
        $('db-list-select').hidden = true;
        NIQ.clear($('db-diagnostics'));
        status('');
        $('db-access-summary').textContent = '';
    }

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
        if (state.credentialToken) payload.credential_token = state.credentialToken;
        return payload;
    }

    function validate(payload) {
        const fileBased = !!currentType().file_based;
        const windows = payload.auth === 'windows';
        if (!payload.database && !state.pasted) return fileBased ? 'Dosya yolunu gir' : 'Veritabanı adını gir';
        if (!fileBased && !windows && !payload.user && !state.pasted) return 'Kullanıcı adını gir';
        if (!fileBased && !windows && !payload.password && !state.pasted && (!state.credentialToken || state.needsPassword)) return 'Parolayı gir';
        if (payload.ssh_host && !payload.ssh_user) return 'SSH kullanıcısını gir';
        if (payload.ssl === 'verify' && state.type !== 'mssql' && !payload.ssl_ca) return 'Sertifikayı doğrulamak için CA dosyasını gir';
        return null;
    }

    function applyAuth() {
        const windows = state.type === 'mssql' && $('db_auth').value === 'windows';
        const fileBased = !!currentType().file_based;
        document.querySelectorAll('[data-field="user"],[data-field="password"]').forEach((node) => {
            node.hidden = windows || fileBased || (!!state.credentialToken && (node.dataset.field === 'user' || !state.needsPassword));
        });
        if (state.step === 'access') goStep('access');
    }

    function applyType(typeId, keepValues) {
        if (!keepValues && state.type !== typeId) {
            clearForm();
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
        $('db-file-btn').hidden = !fileBased || !state.desktop;
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
            // Daha eski bir ayrıştırma yanıtı, yeni yapıştırılan adresi ezmesin.
            if (text !== value('db_paste')) return;
            clearForm();
            fill(parsed);
            state.pasted = text;
            $('db_paste').value = text;
            goStep('access');
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
        if (problem) { goStep('access'); status(problem, 'error'); return false; }
        goStep('check');
        status('Test ediliyor...');
        try {
            const result = (await api('/db/diagnose', { body: payload })).data;
            renderDiagnostics(result);
            status(result.ok ? 'Test başarılı. Bağlanabilirsin.' : 'Test başarısız. İpuçlarına bak.', result.ok ? 'ok' : 'error');
            $('db-step-hint').textContent = result.ok ? 'Erişim doğrulandı. Bağlanabilirsin.' : 'Bilgileri düzenleyip yeniden test et.';
            return result.ok;
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error');
            return false;
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
            if (names.length === 1) { select.value = names[0]; $('db_database').value = names[0]; }
            status(names.length + ' veritabanı bulundu.', 'ok');
            select.focus();
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
            state.credentialToken = '';
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
        fill(profile);
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
            && (active.port || null) === (payload.port || currentType().default_port || null)
            && ['driver', 'ssl', 'ssl_ca', 'ssh_host', 'ssh_port', 'ssh_user', 'ssh_key'].every((key) =>
                String(active.options?.[key] || '') === String(payload[key] || ''))
            && (active.options?.auth || 'sql') === (payload.auth || 'sql');
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

    async function loadDiscovery(force = false) {
        if ($('db-discover-btn').disabled || (state.discoveryLoaded && !force)) return;
        $('db-discover-btn').disabled = true;
        $('db-discovery-status').textContent = 'Yerel sunucular aranıyor...';
        try {
            const found = (await api('/db/discover')).data;
            const box = NIQ.clear($('discovered'));
            found.local_servers.forEach((server) => {
                box.appendChild(el('button', { class: 'chip',
                    text: `${server.name || server.label} · ${server.host}:${server.port}`,
                    title: 'Sunucu bilgilerini doldur; erişim bilgileri sonraki adımda',
                    onclick: () => {
                        clearForm();
                        fill(server);
                        goStep('access');
                        if (server.source === 'docker') { prepareLocal(server); return; }
                        status(server.options?.auth === 'windows' ? 'Windows hesabın kullanılacak. Veritabanlarını getir ile devam et.'
                            : 'Sunucu ve port dolduruldu. Erişim bilgilerini gir.');
                        (server.options?.auth === 'windows' ? $('db-list-btn') : $('db_username')).focus();
                    } }));
            });
            box.hidden = !found.local_servers.length;
            state.discoveryLoaded = true;
            $('db-discovery-status').textContent = found.local_servers.length
                ? 'Bulunan sunucular. Bağlantı için hesabının erişimi gerekir.'
                : 'Yerel sunucu bulunamadı. Bir tür seçebilir, bağlantı metni yapıştırabilir veya örneği deneyebilirsin.';
        } catch (e) {
            $('db-discovery-status').textContent = 'Yerel keşif yapılamadı. Bağlantıyı elle girebilirsin.';
        } finally {
            $('db-discover-btn').disabled = false;
        }
    }

    async function prepareLocal(server) {
        try {
            const prepared = (await api('/db/prepare', { body: { source_id: server.source_id } })).data;
            fill(prepared.profile);
            state.credentialToken = prepared.credential_token;
            state.needsPassword = prepared.needs_password;
            applyAuth();
            $('db-access-summary').textContent = `${server.name}: bağlantı bilgileri dolduruldu.`;
            $('db-step-hint').textContent = prepared.needs_password ? 'Yalnızca sunucu parolasını gir.' : 'Veritabanını seç, ardından devam et.';
            if (!prepared.needs_password) await listDatabases();
            else $('db_password').focus();
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error');
        }
    }

    function updateScopeCount() {
        $('db-scope-count').textContent = `${state.selected.size} veritabanı seçili`;
        $('db-scope-connect-btn').disabled = !state.selected.size;
        document.querySelectorAll('[data-scope-mode]').forEach((button) => {
            button.setAttribute('aria-pressed', String(button.dataset.scopeMode === state.scopeMode));
        });
    }

    function renderCatalog() {
        const box = NIQ.clear($('db-catalog-list'));
        const groups = [...new Set(state.catalog.map((item) => item.platform))];
        groups.forEach((platform) => {
            const databases = state.catalog.filter((item) => item.platform === platform);
            const group = el('fieldset', { class: 'db-platform' }, el('legend', {
                text: `${databases[0].platform_label} · ${databases.length} veritabanı`,
            }));
            databases.forEach((item) => {
                const check = el('input', { type: 'checkbox', 'aria-label': item.source + ' / ' + item.name });
                check.checked = state.selected.has(item.id);
                check.addEventListener('change', () => {
                    state.scopeMode = 'custom';
                    if (check.checked) state.selected.add(item.id); else state.selected.delete(item.id);
                    updateScopeCount();
                });
                group.appendChild(el('label', { class: 'check db-catalog-item', 'data-search': `${item.source} ${item.name}`.toLocaleLowerCase('tr') },
                    check, el('span', {}, el('span', { text: item.name }),
                        el('span', { class: 'item-meta', text: `${item.source} · ${item.table_count} tablo` }))));
            });
            box.appendChild(group);
        });
        updateScopeCount();
    }

    async function findCatalog() {
        $('db-catalog-btn').disabled = true;
        status('Veritabanları ve erişimler kontrol ediliyor...');
        try {
            const found = (await api('/db/catalog', { body: {} })).data;
            state.catalog = found.databases;
            state.scopeMode = 'recommended';
            state.selected = new Set(state.catalog.filter((item) => item.recommended).map((item) => item.id));
            $('db-catalog-panel').hidden = !state.catalog.length;
            $('db-catalog-count').textContent = `${found.platform_count} platformda ${found.database_count} veritabanına erişilebiliyor.`;
            renderCatalog();
            const missing = NIQ.clear($('db-unavailable-list'));
            found.unavailable.forEach((item) => missing.appendChild(el('li', { text: `${item.name}: ${item.reason}` })));
            $('db-unavailable').hidden = !found.unavailable.length;
            status(found.unavailable.length ? `${found.unavailable.length} kaynak için kayıtlı veya geçerli erişim bilgisi bulunamadı. Diğer bağlantılardan ekleyebilirsin.`
                : state.catalog.length ? 'Erişim kapsamını seç.' : 'Erişilebilir veritabanı bulunamadı. Dosya seçebilir veya örneği deneyebilirsin.');
        } catch (error) {
            if (!error.cancelled) status(error.message, 'error'); else status('');
        } finally { $('db-catalog-btn').disabled = false; }
    }

    function show() {
        $('connect').hidden = false;
        $('empty').hidden = true;
        $('stream').hidden = true;
        if (NIQ.state.profile) fill(NIQ.state.profile);
        goStep('source');
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
        if (window.qt?.webChannelTransport && window.QWebChannel) {
            new QWebChannel(qt.webChannelTransport, (channel) => {
                state.desktop = channel.objects.desktop;
                $('db-file-btn').hidden = !currentType().file_based;
                $('db-source-file-btn').hidden = false;
            });
        }
        try {
            state.types = (await api('/db/types')).data;
        } catch (e) {
            state.types = [{ id: 'mssql', label: 'SQL Server', default_port: 1433, host_suggestions: ['localhost'] }];
        }
        const box = $('db-types');
        state.types.forEach((type) => {
            box.appendChild(el('button', {
                class: 'chip' + (type.driver_available === false ? ' missing' : ''), 'data-type': type.id, text: type.label,
                onclick: () => { applyType(type.id); goStep('access'); },
            }));
        });
        applyType(state.type);
        document.querySelectorAll('[data-connect-step]').forEach((button) => button.addEventListener('click', () => goStep(button.dataset.connectStep)));
        $('db-next-btn').addEventListener('click', () => goStep('access'));
        $('db-back-btn').addEventListener('click', () => goStep('source'));
        $('db-edit-btn').addEventListener('click', () => goStep('access'));
        $('db-access-next-btn').addEventListener('click', runTest);
        $('db-discover-btn').addEventListener('click', () => loadDiscovery(true));
        $('db-catalog-btn').addEventListener('click', findCatalog);
        document.querySelectorAll('[data-scope-mode]').forEach((button) => button.addEventListener('click', () => {
            state.scopeMode = button.dataset.scopeMode;
            if (state.scopeMode !== 'custom') state.selected = new Set(state.catalog.filter((item) =>
                state.scopeMode === 'all' || item.recommended).map((item) => item.id));
            renderCatalog();
        }));
        $('db-catalog-search').addEventListener('input', (event) => {
            const query = event.target.value.toLocaleLowerCase('tr');
            document.querySelectorAll('.db-catalog-item').forEach((item) => { item.hidden = !item.dataset.search.includes(query); });
        });
        $('db-scope-connect-btn').addEventListener('click', async () => {
            $('db-scope-connect-btn').disabled = true;
            try {
                const profile = (await api('/db/scope', { body: { databases: [...state.selected] } })).data;
                clearForm(); await afterConnect(profile);
            } catch (error) { status(error.message, 'error'); }
            finally { updateScopeCount(); }
        });
        const chooseFile = () => {
            if (!state.desktop) return;
            state.desktop.chooseSqliteFile((path) => {
                if (!path) return;
                clearForm();
                fill({ type: 'sqlite', database: path });
                goStep('access');
                status('Dosya seçildi. Test et ve devam et.');
            });
        };
        $('db-file-btn').addEventListener('click', chooseFile);
        $('db-source-file-btn').addEventListener('click', chooseFile);
        ['db_server', 'db_port', 'db_username'].forEach((id) => $(id).addEventListener('input', () => {
            state.credentialToken = '';
            state.needsPassword = true;
            applyAuth();
        }));
        $('db-demo-btn').addEventListener('click', async () => {
            $('db-demo-btn').disabled = true;
            try {
                clearForm();
                const profile = (await api('/db/demo', { body: {} })).data;
                await afterConnect(profile);
            } catch (error) { status(error.message, 'error'); }
            finally { $('db-demo-btn').disabled = false; }
        });
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
