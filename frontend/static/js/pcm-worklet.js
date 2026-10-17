/** Mono PCM parçaları; ses çıkışı üretilmez. */
class NiqPcm extends AudioWorkletProcessor {
    constructor() {
        super();
        this.buffer = new Float32Array(2048);
        this.length = 0;
        this.port.onmessage = ({ data }) => {
            if (data.flush) { this.flush(); this.port.postMessage({ flushed: true }); }
        };
    }
    flush() {
        if (!this.length) return;
        const frame = this.buffer.slice(0, this.length);
        this.port.postMessage(frame.buffer, [frame.buffer]);
        this.length = 0;
    }
    process(inputs) {
        const channels = inputs[0];
        if (channels?.length) {
            for (let i = 0; i < channels[0].length; i++) {
                let value = 0;
                for (const channel of channels) value += channel[i] / channels.length;
                this.buffer[this.length++] = value;
                if (this.length === this.buffer.length) this.flush();
            }
        }
        return true;
    }
}
registerProcessor('niq-pcm', NiqPcm);
