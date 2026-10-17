document.getElementById('auth-return').addEventListener('click', async () => {
    try {
        const response = await fetch('/api/desktop/focus', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const result = await response.json();
        if (!result.data?.available) location.assign('/');
        else document.getElementById('auth-return-status').textContent = 'NIQ penceresine dönebilirsin.';
    } catch (_) {
        document.getElementById('auth-return-status').textContent = 'NIQ penceresine dönerek devam edebilirsin.';
    }
});
