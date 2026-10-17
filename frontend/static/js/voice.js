/** Canlı dikte: PCM yakalama, aralıklı taslak transkript, kayıt sonunda kesin metin. */
(function () {
    const TARGET_RATE = 16000;
    let capture = null;
    let starting = false;
    let generation = 0;
    const requests = new Set();

    function supported() {
        return !!(navigator.mediaDevices?.getUserMedia && window.AudioContext && window.AudioWorkletNode);
    }

    function wav(frames, length, rate) {
        const source = new Float32Array(length);
        let offset = 0;
        frames.forEach((frame) => { source.set(frame, offset); offset += frame.length; });
        const count = Math.floor(length * TARGET_RATE / rate);
        const buffer = new ArrayBuffer(44 + count * 2);
        const view = new DataView(buffer);
        const text = (at, value) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
        text(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); text(8, 'WAVE'); text(12, 'fmt ');
        view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
        view.setUint32(24, TARGET_RATE, true); view.setUint32(28, TARGET_RATE * 2, true);
        view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, count * 2, true);
        for (let i = 0; i < count; i++) {
            const from = Math.floor(i * rate / TARGET_RATE);
            const to = Math.min(length, Math.max(from + 1, Math.floor((i + 1) * rate / TARGET_RATE)));
            let sum = 0;
            for (let j = from; j < to; j++) sum += source[j];
            const sample = Math.max(-1, Math.min(1, sum / (to - from)));
            view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        }
        return new Blob([buffer], { type: 'audio/wav' });
    }

    function release(current) {
        if (!current || current.released) return;
        current.released = true;
        clearInterval(current.timer); clearTimeout(current.limit);
        current.stream.getTracks().forEach((track) => track.stop());
        if (current.node) { current.node.port.onmessage = null; current.node.disconnect(); }
        current.source?.disconnect(); current.silence?.disconnect();
        current.context?.close().catch(() => {});
    }

    async function transcribe(current) {
        if (current.length < current.rate / 4) return '';
        const form = new FormData();
        form.append('audio', wav(current.frames, current.length, current.rate), 'ses.wav');
        form.append('language', 'tr');
        const controller = new AbortController();
        current.request = controller;
        requests.add(controller);
        try {
            const response = await fetch('/api/transcribe', { method: 'POST', body: form, signal: controller.signal });
            const result = await response.json();
            if (!response.ok || result.success === false) throw new Error(result.error || 'Ses metne çevrilemedi');
            return current.generation === generation ? (result.data?.text || '').trim() : '';
        } finally {
            requests.delete(controller);
            if (current.request === controller) current.request = null;
        }
    }

    async function preview(current) {
        if (capture !== current || current.previewBusy || current.stopping || current.length - current.previewLength < current.rate) return;
        current.previewBusy = true;
        current.previewLength = current.length;
        try {
            const text = await transcribe(current);
            if (text && capture === current && !current.stopping) {
                current.lastText = text;
                current.onTranscript?.(text);
            }
        } catch (error) {
            current.previewError = error;
        } finally { current.previewBusy = false; }
    }

    NIQ.voice = {
        supported,
        get recording() { return !!capture && !capture.stopping; },
        async start(onAutoStop, onTranscript) {
            if (!supported()) throw new Error('Bu ortamda mikrofon kaydı desteklenmiyor');
            if (starting || capture) throw new Error('Mikrofon zaten açık');
            starting = true;
            const request = ++generation;
            let current;
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
                current = { stream, generation: request, frames: [], length: 0, previewLength: 0, lastText: '', onTranscript };
                if (request !== generation) throw new Error('Kayıt iptal edildi');
                capture = current;
                current.context = new AudioContext();
                current.rate = current.context.sampleRate;
                await current.context.audioWorklet.addModule('/static/js/pcm-worklet.js');
                if (request !== generation) throw new Error('Kayıt iptal edildi');
                current.node = new AudioWorkletNode(current.context, 'niq-pcm');
                current.node.port.onmessage = ({ data }) => {
                    if (data.flushed) { current.flushed?.(); return; }
                    const frame = new Float32Array(data);
                    current.frames.push(frame); current.length += frame.length;
                };
                current.source = current.context.createMediaStreamSource(stream);
                current.silence = current.context.createGain(); current.silence.gain.value = 0;
                current.source.connect(current.node); current.node.connect(current.silence); current.silence.connect(current.context.destination);
                await current.context.resume();
                if (request !== generation) throw new Error('Kayıt iptal edildi');
                current.timer = setInterval(() => preview(current), 2000);
                current.limit = setTimeout(() => onAutoStop?.(), 60000);
            } catch (error) {
                release(current);
                if (capture === current) capture = null;
                const messages = {
                    NotAllowedError: 'Mikrofon izni verilmedi. Windows mikrofon erişimini kontrol et',
                    NotFoundError: 'Mikrofon bulunamadı. Bir mikrofon bağla',
                    NotReadableError: 'Mikrofon açılamadı. Başka bir uygulama kullanıyor olabilir',
                };
                throw new Error(messages[error.name] || error.message || 'Mikrofon kaydı başlatılamadı');
            } finally { starting = false; }
        },
        async stop() {
            const current = capture;
            if (!current || current.stopping) return '';
            current.stopping = true;
            clearInterval(current.timer); clearTimeout(current.limit);
            current.request?.abort();
            try {
                await new Promise((resolve) => {
                    const timeout = setTimeout(resolve, 150);
                    current.flushed = () => { clearTimeout(timeout); resolve(); };
                    current.node.port.postMessage({ flush: true });
                });
            } finally {
                release(current);
                if (capture === current) capture = null;
            }
            if (current.generation !== generation) return '';
            return (await transcribe(current)) || current.lastText;
        },
        cancel() {
            generation++;
            requests.forEach((controller) => controller.abort()); requests.clear();
            if (capture) { capture.flushed?.(); release(capture); }
            capture = null;
        },
    };
})();
