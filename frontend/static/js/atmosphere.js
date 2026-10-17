/* Damped springs keep the pixel wordmark and navy currents calm under interaction. */
(function () {
    const canvas = document.getElementById('onboarding-wordmark');
    const context = canvas?.getContext('2d');
    const atmosphere = document.querySelector('.ambient');
    const layers = [...document.querySelectorAll('.ambient i')];
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    const pointer = { x: .5, y: .5, localX: -1000, localY: -1000 };
    const spring = { x: 0, y: 0, vx: 0, vy: 0 };
    // Three 5 × 7 glyphs with square pixels and a readable Q tail.
    const letters = [
        ['10001', '11001', '10101', '10011', '10001', '10001', '10001'].join(''),
        ['11111', '00100', '00100', '00100', '00100', '00100', '11111'].join(''),
        ['01110', '10001', '10001', '10001', '10101', '10010', '01101'].join(''),
    ];
    const pixels = [];
    letters.forEach((letter, index) => [...letter].forEach((bit, cell) => {
        if (bit === '1') pixels.push({ x: 94 + index * 106 + (cell % 5) * 16, y: 20 + Math.floor(cell / 5) * 16, dx: 0, dy: 0, vx: 0, vy: 0 });
    }));
    let frame = 0, previous = 0, elapsed = 0;
    function draw() {
        if (!context) return;
        context.clearRect(0, 0, 480, 160);
        for (const pixel of pixels) {
            context.fillStyle = '#b6d8ff';
            context.fillRect(Math.round(pixel.x + pixel.dx), Math.round(pixel.y + pixel.dy), 12, 12);
        }
    }
    function animate(now) {
        frame = 0;
        if (previous && now - previous < 32) { frame = requestAnimationFrame(animate); return; }
        const dt = Math.min((now - (previous || now)) / 1000, .05);
        previous = now;
        elapsed += dt;
        const targetX = (pointer.x - .5) * 28, targetY = (pointer.y - .5) * 20;
        spring.vx += ((targetX - spring.x) * 9 - spring.vx * 6) * dt;
        spring.vy += ((targetY - spring.y) * 9 - spring.vy * 6) * dt;
        spring.x += spring.vx * dt; spring.y += spring.vy * dt;
        atmosphere?.style.setProperty('--ambient-x', spring.x + 'px');
        atmosphere?.style.setProperty('--ambient-y', spring.y + 'px');
        layers.forEach((layer, i) => {
            const phase = elapsed * .055 + i * 2.1;
            layer.style.transform = `translate3d(${Math.sin(phase) * 9}%, ${Math.cos(phase * .73) * 7}%, 0) rotate(${Math.sin(phase * .6) * 12}deg) scale(${1 + Math.sin(phase * .8) * .08})`;
        });
        if (canvas && canvas.getClientRects().length) {
            for (const pixel of pixels) {
                const x = pixel.x + 6 - pointer.localX, y = pixel.y + 6 - pointer.localY;
                const distance = Math.hypot(x, y), force = Math.max(0, 1 - distance / 90) * 500;
                pixel.vx += (-pixel.dx * 55 - pixel.vx * 10 + x / Math.max(distance, 1) * force) * dt;
                pixel.vy += (-pixel.dy * 55 - pixel.vy * 10 + y / Math.max(distance, 1) * force) * dt;
                pixel.dx += pixel.vx * dt; pixel.dy += pixel.vy * dt;
            }
            draw();
        }
        frame = requestAnimationFrame(animate);
    }
    window.addEventListener('pointermove', (event) => {
        pointer.x = event.clientX / innerWidth; pointer.y = event.clientY / innerHeight;
        if (canvas) {
            const rect = canvas.getBoundingClientRect();
            pointer.localX = (event.clientX - rect.left) * 480 / rect.width;
            pointer.localY = (event.clientY - rect.top) * 160 / rect.height;
        }
    }, { passive: true });
    window.addEventListener('blur', () => { pointer.x = pointer.y = .5; pointer.localX = pointer.localY = -1000; });
    function resume() {
        cancelAnimationFrame(frame); previous = 0;
        if (!document.hidden && !motion.matches) frame = requestAnimationFrame(animate);
        else if (motion.matches) {
            pixels.forEach((p) => { p.dx = p.dy = p.vx = p.vy = 0; });
            layers.forEach((layer) => layer.removeAttribute('style'));
            atmosphere?.removeAttribute('style'); draw();
        }
    }
    document.addEventListener('visibilitychange', resume);
    motion.addEventListener('change', resume);
    draw(); resume();
})();
