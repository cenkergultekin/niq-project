/* Mikrofon hata/iptal yarışları: gerçek voice.js, izole tarayıcı nesneleri. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../frontend/static/js/voice.js'), 'utf8');

function setup({ getUserMedia, failRecorder = false } = {}) {
    let stopped = 0;
    let instance;
    const stream = { getTracks: () => [{ stop: () => stopped++ }] };
    const context = {
        NIQ: {}, Blob, ArrayBuffer, DataView, FormData, AbortController,
        navigator: { mediaDevices: { getUserMedia: getUserMedia || (async () => stream) } },
        setTimeout: () => 1, clearTimeout: () => {},
        AudioContext: class {
            async decodeAudioData() { throw Error('invalid audio'); }
            close() {}
        },
        MediaRecorder: class {
            constructor() { if (failRecorder) throw Error('codec not supported'); instance = this; this.state = 'inactive'; }
            start() { this.state = 'recording'; }
            stop() {
                this.state = 'inactive';
                queueMicrotask(() => {
                    this.ondataavailable?.({ data: new Blob([new Uint8Array(3000)]) });
                    this.onstop?.();
                });
            }
        },
    };
    context.window = context;
    vm.runInNewContext(source, context);
    return { voice: context.NIQ.voice, stream, stopped: () => stopped, recorder: () => instance };
}

(async () => {
    let test = setup();
    await test.voice.start();
    await assert.rejects(test.voice.start(), /zaten açık/);
    test.voice.cancel();
    assert.equal(test.stopped(), 1);
    assert.equal(test.voice.recording, false);

    test = setup();
    await test.voice.start();
    const stopping = test.voice.stop();
    test.voice.cancel();
    assert.equal(await stopping, '');
    assert.equal(test.stopped(), 1);

    let resolve;
    test = setup({ getUserMedia: () => new Promise(r => { resolve = r; }) });
    const pending = test.voice.start();
    test.voice.cancel();
    resolve(test.stream);
    await assert.rejects(pending, /iptal/);
    assert.equal(test.stopped(), 1);
    assert.equal(test.voice.recording, false);

    test = setup({ getUserMedia: async () => { throw Object.assign(Error(), { name: 'NotAllowedError' }); } });
    await assert.rejects(test.voice.start(), /Windows mikrofon erişimini/);
    assert.equal(test.voice.recording, false);

    test = setup({ failRecorder: true });
    await assert.rejects(test.voice.start(), /codec/);
    assert.equal(test.stopped(), 1);

    test = setup();
    await test.voice.start();
    await assert.rejects(test.voice.stop(), /invalid audio/);
    assert.equal(test.stopped(), 1);
    assert.equal(test.voice.recording, false);
    await test.voice.start(() => {});
    test.recorder().state = 'inactive';
    test.recorder().onerror();
    await assert.rejects(test.voice.stop(), /kesildi/);
    assert.equal(test.voice.recording, false);
    console.log('Voice lifecycle: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
