// AudioWorklet for the live voice: microphone audio to 16 kHz, 16-bit mono PCM, posted in 40 ms frames
// (640 samples), which is what the Gemini Live API takes. Each output sample averages the input samples it
// covers, which filters out what 16 kHz cannot carry before decimating.
class PcmRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.out = new Int16Array(640);
    this.filled = 0;
    this.sum = 0;
    this.count = 0;
    this.acc = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.sum += ch[i];
      this.count += 1;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.sum / this.count));
        this.sum = 0;
        this.count = 0;
        this.out[this.filled++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        if (this.filled === this.out.length) {
          this.port.postMessage(this.out.buffer.slice(0));
          this.filled = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('pcm-recorder', PcmRecorder);
