/**
 * Bas-konuş: mikrofonu kaydeder, 16 kHz mono WAV'a çevirir (OpenRouter ses modelleri WAV bekler).
 * Kayıt en fazla 60 saniyedir; metne çevirme sunucuda yapılır (/api/transcribe).
 */
(function () {
    const MAX_SECONDS = 60;
    const TARGET_RATE = 16000;
    let recorder = null;
    let stream = null;
    let chunks = [];
    let stopTimer = null;

    function supported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    }

    function writeString(view, offset, text) {
        for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
    }

    function encodeWav(samples, rate) {
        const buffer = new ArrayBuffer(44 + samples.length * 2);
        const view = new DataView(buffer);
        writeString(view, 0, 'RIFF');
        view.setUint32(4, 36 + samples.length * 2, true);
        writeString(view, 8, 'WAVE');
        writeString(view, 12, 'fmt ');
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);          // PCM
        view.setUint16(22, 1, true);          // mono
        view.setUint32(24, rate, true);
        view.setUint32(28, rate * 2, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        writeString(view, 36, 'data');
        view.setUint32(40, samples.length * 2, true);
        for (let i = 0; i < samples.length; i++) {
            const s = Math.max(-1, Math.min(1, samples[i]));
            view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        }
        return new Blob([buffer], { type: 'audio/wav' });
    }

    /** Kaydedilen blob'u çözüp 16 kHz mono PCM'e indirger. */
    async function toWav(blob) {
        const context = new (window.AudioContext || window.webkitAudioContext)();
        try {
            const decoded = await context.decodeAudioData(await blob.arrayBuffer());
            const frames = Math.ceil(decoded.duration * TARGET_RATE);
            const offline = new OfflineAudioContext(1, frames, TARGET_RATE);
            const source = offline.createBufferSource();
            source.buffer = decoded;
            source.connect(offline.destination);
            source.start();
            const rendered = await offline.startRendering();
            return encodeWav(rendered.getChannelData(0), TARGET_RATE);
        } finally {
            context.close();
        }
    }

    function release() {
        clearTimeout(stopTimer);
        if (stream) stream.getTracks().forEach((t) => t.stop());
        stream = null;
        recorder = null;
    }

    NIQ.voice = {
        supported,
        get recording() { return !!recorder; },

        /** Kaydı başlatır. onAutoStop: 60 sn dolunca otomatik durduğunda çağrılır. */
        async start(onAutoStop) {
            if (!supported()) throw new Error('Bu ortamda mikrofon kaydı desteklenmiyor');
            try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            } catch (error) {
                throw new Error('Mikrofon izni verilmedi veya mikrofon bulunamadı');
            }
            chunks = [];
            recorder = new MediaRecorder(stream);
            recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
            recorder.start();
            stopTimer = setTimeout(() => { if (onAutoStop) onAutoStop(); }, MAX_SECONDS * 1000);
        },

        /** Kaydı bitirir ve metne çevirir. */
        async stop() {
            if (!recorder) return '';
            const active = recorder;
            const finished = new Promise((resolve) => { active.onstop = resolve; });
            active.stop();
            await finished;
            const blob = new Blob(chunks, { type: active.mimeType || 'audio/webm' });
            release();
            if (blob.size < 2000) return '';  // neredeyse boş kayıt
            const wav = await toWav(blob);
            const form = new FormData();
            form.append('audio', wav, 'ses.wav');
            form.append('language', 'tr');
            const response = await fetch('/api/transcribe', { method: 'POST', body: form });
            const result = await response.json();
            if (!response.ok || result.success === false) throw new Error(result.error || 'Ses metne çevrilemedi');
            return (result.data.text || '').trim();
        },

        cancel() {
            if (recorder) { try { recorder.onstop = null; recorder.stop(); } catch (e) { /* yok say */ } }
            release();
        },
    };
})();
