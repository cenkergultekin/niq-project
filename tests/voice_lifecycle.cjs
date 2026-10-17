/* Gerçek voice.js: canlı transkript, WAV, iptal, hata ve mikrofon temizliği. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../frontend/static/js/voice.js'), 'utf8');
function setup(options = {}) {
    let stopped = 0, instance;
    const intervals = [];
    const stream = { getTracks: () => [{ stop: () => stopped++ }] };
    const node = () => ({ connect() {}, disconnect() {} });
    const context = {
        NIQ: {}, Blob, ArrayBuffer, DataView, Float32Array, FormData, AbortController,
        navigator: { mediaDevices: { getUserMedia: options.getUserMedia || (async () => stream) } },
        setTimeout: () => 1, clearTimeout: () => {}, setInterval: (fn) => { intervals.push(fn); return 1; }, clearInterval: () => {},
        AudioContext: class {
            sampleRate = 16000;
            audioWorklet = { addModule: async () => { if (options.failWorklet) throw Error('worklet failed'); } };
            createMediaStreamSource() { return node(); }
            createGain() { return { ...node(), gain: { value: 0 } }; }
            async resume() {}
            async close() {}
        },
        AudioWorkletNode: class {
            constructor() {
                instance = this;
                this.port = { postMessage: () => queueMicrotask(() => this.port.onmessage?.({ data: { flushed: true } })) };
            }
            connect() {}
            disconnect() {}
        },
        fetch: options.fetch || (async (_, { body }) => {
            const data = new DataView(await body.get('audio').arrayBuffer());
            assert.equal(data.getUint32(24, true), 16000);
            assert.equal(data.getUint16(22, true), 1);
            return { ok: true, json: async () => ({ data: { text: 'Kaç sipariş var?' } }) };
        }),
    };
    context.window = context;
    vm.runInNewContext(source, context);
    return { voice: context.NIQ.voice, stream, stopped: () => stopped,
        frames: () => instance.port.onmessage({ data: new Float32Array(32000).buffer }), preview: () => intervals[0]() };
}
(async () => {
    let test = setup(), preview = '';
    await test.voice.start(null, (text) => { preview = text; });
    await assert.rejects(test.voice.start(), /zaten açık/);
    test.frames(); await test.preview();
    assert.equal(preview, 'Kaç sipariş var?');
    assert.equal(test.voice.recording, true);
    assert.equal(await test.voice.stop(), preview);
    assert.equal(test.stopped(), 1);
    assert.equal(test.voice.recording, false);

    test = setup(); await test.voice.start(); test.frames();
    const stopping = test.voice.stop(); test.voice.cancel();
    assert.equal(await stopping, ''); assert.equal(test.stopped(), 1);

    let resolve;
    test = setup({ getUserMedia: () => new Promise(r => { resolve = r; }) });
    const pending = test.voice.start(); test.voice.cancel(); resolve(test.stream);
    await assert.rejects(pending, /iptal/); assert.equal(test.stopped(), 1);

    test = setup({ getUserMedia: async () => { throw Object.assign(Error(), { name: 'NotAllowedError' }); } });
    await assert.rejects(test.voice.start(), /Windows mikrofon erişimini/);

    test = setup({ failWorklet: true });
    await assert.rejects(test.voice.start(), /worklet failed/); assert.equal(test.stopped(), 1);

    test = setup({ fetch: async () => { throw Error('transcription failed'); } });
    await test.voice.start(); test.frames();
    await assert.rejects(test.voice.stop(), /transcription failed/);
    assert.equal(test.stopped(), 1); assert.equal(test.voice.recording, false);
    await test.voice.start(); test.voice.cancel();
    console.log('Voice lifecycle: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
