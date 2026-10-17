/* The user authorizes discovery and access. Nothing is auto-approved at boot. */
(function () {
    const { $, api } = NIQ;
    let accountsParent, catalogParent;
    let welcomeRequired = false;
    let advancing = false;
    const say = (message) => { $('onboarding-status').textContent = message || ''; };

    NIQ.onboarding = {
        async start() {
            const info = (await api('/onboarding')).data;
            if (!info.required) return false;
            NIQ.state.onboarding = true;
            document.body.classList.add('onboarding');
            accountsParent = $('ai-accounts').parentNode;
            catalogParent = $('db-catalog-panel').parentNode;
            $('onboarding-ai').appendChild($('ai-accounts'));
            $('onboarding-results').appendChild($('db-catalog-panel'));
            document.querySelector('.top').hidden = true;
            NIQ.go('onboarding');
            await NIQ.accounts.load();
            const advance = async (userInitiated = false) => {
                if (advancing || !NIQ.state.onboarding || $('onboarding-account').hidden) return;
                advancing = true;
                say('Hesap doğrulanıyor…');
                try {
                    welcomeRequired = !!(await NIQ.accounts.activate())?.welcome_required;
                    await api('/onboarding/account', { body: {} });
                    say('Bağlantı başarılı');
                    await new Promise((resolve) => setTimeout(resolve, 1000));
                    $('onboarding-account').hidden = true;
                    $('onboarding-databases').hidden = false;
                    say('');
                } catch (error) {
                    say('');
                    $('ai-login-status').textContent = '';
                    $('ai-account-status').textContent = userInitiated && !error.cancelled
                        ? 'Bu hesapla bağlantı kurulamadı.' : '';
                } finally { advancing = false; }
            };
            window.addEventListener('niq-account-connected', () => advance(true));
            if (NIQ.accounts.isConnected()) advance();
            $('onboarding-discover').onclick = async () => {
                $('onboarding-discover').disabled = true;
                say('Veritabanı adları bulunuyor…');
                try {
                    // This button IS the user's names-only consent; no generic second dialog.
                    const found = (await api('/onboarding/discover', { body: { approved: true } })).data;
                    NIQ.connect.useCatalog(found);
                    $('onboarding-permission').hidden = !!found.database_count;
                    $('db-scope-connect-btn').textContent = 'Seçilenlere erişim ver';
                    say(found.database_count ? '' : 'Mevcut bilgilerle erişilebilen yerel veritabanı bulunamadı.');
                } catch (error) { if (!error.cancelled) say(error.message); }
                finally { $('onboarding-discover').disabled = false; }
            };
            return true;
        },
        async connect(databases) {
            say('Seçilen veritabanlarına bağlanılıyor…');
            try {
                // This explicit access button is separate from discovery consent.
                const profile = (await api('/onboarding/connect', { body: { databases, approved: true } })).data;
                accountsParent.insertBefore($('ai-accounts'), accountsParent.querySelector('details'));
                catalogParent.appendChild($('db-catalog-panel'));
                NIQ.state.onboarding = false;
                document.body.classList.remove('onboarding');
                document.querySelector('.top').hidden = false;
                $('db-scope-connect-btn').textContent = 'Seçilenlere bağlan';
                NIQ.go('chat');
                NIQ.connect.hide();
                NIQ.setConnection(profile);
                await NIQ.refreshHealth();
                if (welcomeRequired) $('ai-welcome').showModal();
            } catch (error) { say(error.message); throw error; }
        },
    };
})();
