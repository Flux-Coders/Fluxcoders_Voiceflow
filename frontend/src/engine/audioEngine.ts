/**
 * VoiceFlow WebAudio Engine.
 * 
 * Manages full-duplex microphone capture, local VAD energy detection,
 * fast-path GainNode hardware muting, and version-gated Rime audio chunk playback.
 */

export interface VADConfig {
  energyThreshold: number; // default: 0.015
  holdTimeMs: number;      // default: 150ms
}

export class AudioEngine {
  private audioCtx: AudioContext | null = null;
  private micStream: MediaStream | null = null;
  private micSource: MediaStreamAudioSourceNode | null = null;
  private analyser: AnalyserNode | null = null;
  private outputGain: GainNode | null = null;
  
  private vadIntervalId: number | null = null;
  private isVadSpeaking: boolean = false;
  private vadSpeechStartTime: number = 0;
  
  private onSpeechStartCallbacks: Array<() => void> = [];
  private onSpeechEndCallbacks: Array<() => void> = [];
  private onMicLevelCallbacks: Array<(level: number) => void> = [];

  private isMuted: boolean = false;

  private vadConfig: VADConfig = {
    energyThreshold: 0.015,
    holdTimeMs: 150,
  };

  /**
   * Initializes the WebAudio Context and output routing.
   */
  public async init(): Promise<void> {
    if (!this.audioCtx) {
      const AudioCtxClass = window.AudioContext || (window as any).webkitAudioContext;
      this.audioCtx = new AudioCtxClass();
      
      // Master output gain node for instant fast-path hardware muting
      this.outputGain = this.audioCtx.createGain();
      this.outputGain.connect(this.audioCtx.destination);
      this.outputGain.gain.setValueAtTime(1.0, this.audioCtx.currentTime);
    }

    if (this.audioCtx.state === 'suspended') {
      await this.audioCtx.resume();
    }
  }

  /**
   * Starts full-duplex microphone capture with acoustic echo cancellation.
   */
  public async startMicrophone(): Promise<MediaStream> {
    await this.init();

    if (this.micStream) {
      return this.micStream;
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.micStream = stream;
    if (this.audioCtx) {
      this.micSource = this.audioCtx.createMediaStreamSource(stream);
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 512;
      this.micSource.connect(this.analyser);

      this.startVADLoop();
    }

    return stream;
  }

  /**
   * Stops microphone capture and clears VAD loop.
   */
  public stopMicrophone(): void {
    if (this.vadIntervalId) {
      window.clearInterval(this.vadIntervalId);
      this.vadIntervalId = null;
    }

    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => t.stop());
      this.micStream = null;
    }

    if (this.micSource) {
      this.micSource.disconnect();
      this.micSource = null;
    }

    this.isVadSpeaking = false;
  }

  /**
   * Continuous VAD loop computing RMS audio energy.
   */
  private startVADLoop(): void {
    if (this.vadIntervalId) return;

    const dataArray = new Uint8Array(this.analyser?.frequencyBinCount || 256);

    this.vadIntervalId = window.setInterval(() => {
      if (!this.analyser) return;

      this.analyser.getByteTimeDomainData(dataArray);

      // Compute Root Mean Square (RMS) energy
      let sumSquares = 0.0;
      for (let i = 0; i < dataArray.length; i++) {
        const norm = (dataArray[i] - 128) / 128.0;
        sumSquares += norm * norm;
      }
      const rms = Math.sqrt(sumSquares / dataArray.length);

      // Notify mic level listeners
      const normalizedLevel = Math.min(1.0, rms * 5.0);
      this.onMicLevelCallbacks.forEach((cb) => cb(normalizedLevel));

      const now = performance.now();
      if (rms > this.vadConfig.energyThreshold) {
        if (!this.isVadSpeaking) {
          this.isVadSpeaking = true;
          this.vadSpeechStartTime = now;
          // Trigger Fast-Path Mute & Speech Start
          this.fastMuteOutput();
          this.onSpeechStartCallbacks.forEach((cb) => cb());
        }
      } else {
        if (this.isVadSpeaking && now - this.vadSpeechStartTime > this.vadConfig.holdTimeMs) {
          this.isVadSpeaking = false;
          this.onSpeechEndCallbacks.forEach((cb) => cb());
        }
      }
    }, 25);
  }

  /**
   * Fast-path hardware muting: immediately sets GainNode to 0.
   */
  public fastMuteOutput(): void {
    if (this.outputGain && this.audioCtx) {
      this.outputGain.gain.setValueAtTime(0, this.audioCtx.currentTime);
      this.isMuted = true;
    }
  }

  /**
   * Unmutes output gain node for subsequent audio playback.
   */
  public unmuteOutput(): void {
    if (this.outputGain && this.audioCtx) {
      this.outputGain.gain.setValueAtTime(1.0, this.audioCtx.currentTime);
      this.isMuted = false;
    }
  }

  /**
   * Plays a decoded Rime audio chunk if its version matches the active version.
   */
  public async playAudioChunk(audioBase64: string, chunkVersion: number, activeVersion: number): Promise<void> {
    if (chunkVersion !== activeVersion || this.isMuted) {
      // Stale audio chunk dropped before playback (Level 3 Gate)
      return;
    }

    await this.init();
    if (!this.audioCtx || !this.outputGain) return;

    try {
      const binaryString = window.atob(audioBase64);
      const len = binaryString.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }

      const audioBuffer = await this.audioCtx.decodeAudioData(bytes.buffer);

      // Check version again after asynchronous decode
      if (chunkVersion !== activeVersion || this.isMuted) {
        return;
      }

      this.unmuteOutput();
      const source = this.audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this.outputGain);
      source.start();
    } catch (err) {
      console.warn('Error decoding/playing Rime audio chunk:', err);
    }
  }

  public onSpeechStart(cb: () => void): void {
    this.onSpeechStartCallbacks.push(cb);
  }

  public onSpeechEnd(cb: () => void): void {
    this.onSpeechEndCallbacks.push(cb);
  }

  public onMicLevel(cb: (level: number) => void): void {
    this.onMicLevelCallbacks.push(cb);
  }

  public setVADConfig(config: Partial<VADConfig>): void {
    this.vadConfig = { ...this.vadConfig, ...config };
  }
}

export const audioEngine = new AudioEngine();

