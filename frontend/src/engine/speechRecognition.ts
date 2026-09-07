/**
 * VoiceFlow Browser Speech Recognition Adapter.
 * 
 * Uses Web Speech API (webkitSpeechRecognition / SpeechRecognition)
 * to emit interim and final transcripts for turn processing.
 */

export interface SpeechRecognitionConfig {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
}

export class BrowserSpeechRecognition {
  private recognition: any = null;
  private isListening: boolean = false;
  private shouldRestart: boolean = false;

  private accumulatedText: string = '';
  private currentUtteranceText: string = '';
  private endpointTimerId: number | null = null;
  private silenceEndpointMs: number = 650;
  private isVadActiveProvider: (() => boolean) | null = null;

  private onInterimCallbacks: Array<(text: string) => void> = [];
  private onFinalCallbacks: Array<(text: string) => void> = [];
  private onStartCallbacks: Array<() => void> = [];
  private onEndCallbacks: Array<() => void> = [];
  private onErrorCallbacks: Array<(error: string) => void> = [];

  constructor(config?: Partial<SpeechRecognitionConfig> & { silenceEndpointMs?: number }) {
    const SpeechRecognitionClass =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (SpeechRecognitionClass) {
      this.recognition = new SpeechRecognitionClass();
      this.recognition.lang = config?.lang || 'en-US';
      this.recognition.continuous = config?.continuous !== undefined ? config.continuous : true;
      this.recognition.interimResults = config?.interimResults !== undefined ? config.interimResults : true;
      this.recognition.maxAlternatives = config?.maxAlternatives || 1;
      if (config?.silenceEndpointMs) {
        this.silenceEndpointMs = config.silenceEndpointMs;
      }

      this.setupHandlers();
    }
  }

  public isSupported(): boolean {
    return this.recognition !== null;
  }

  public setVadActiveProvider(provider: () => boolean): void {
    this.isVadActiveProvider = provider;
  }

  public isVadActive(): boolean {
    return this.isVadActiveProvider ? this.isVadActiveProvider() : false;
  }

  public setSilenceEndpointMs(ms: number): void {
    this.silenceEndpointMs = ms;
  }

  /**
   * Called when local VAD detects speech onset.
   * Cancels any pending silence endpoint timer so speaking is not prematurely committed.
   */
  public onVadSpeechStart(): void {
    if (this.endpointTimerId) {
      window.clearTimeout(this.endpointTimerId);
      this.endpointTimerId = null;
    }
  }

  /**
   * Called when local VAD detects speech end (hold time elapsed).
   * Starts the silence endpoint timer to commit the accumulated utterance after silence.
   */
  public onVadSpeechEnd(): void {
    const text = (this.currentUtteranceText || this.accumulatedText).trim();
    if (text) {
      this.resetEndpointTimer();
    }
  }

  public clearPendingUtterance(): void {
    if (this.endpointTimerId) {
      window.clearTimeout(this.endpointTimerId);
      this.endpointTimerId = null;
    }
    this.accumulatedText = '';
    this.currentUtteranceText = '';
  }

  public commitPendingUtteranceNow(): void {
    if (this.endpointTimerId) {
      window.clearTimeout(this.endpointTimerId);
      this.endpointTimerId = null;
    }

    // Do NOT commit if user is still actively speaking according to VAD
    if (this.isVadActive()) {
      return;
    }

    const textToCommit = (this.currentUtteranceText || this.accumulatedText).trim();
    if (textToCommit) {
      this.accumulatedText = '';
      this.currentUtteranceText = '';
      this.onFinalCallbacks.forEach((cb) => cb(textToCommit));
    }
  }

  private resetEndpointTimer(): void {
    if (this.endpointTimerId) {
      window.clearTimeout(this.endpointTimerId);
    }
    this.endpointTimerId = window.setTimeout(() => {
      this.commitPendingUtteranceNow();
    }, this.silenceEndpointMs);
  }

  private setupHandlers(): void {
    if (!this.recognition) return;

    this.recognition.onstart = () => {
      this.isListening = true;
      this.onStartCallbacks.forEach((cb) => cb());
    };

    this.recognition.onresult = (event: any) => {
      let interimText = '';
      let segmentFinalText = '';

      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const result = event.results[i];
        const transcript = result[0]?.transcript || '';
        if (result.isFinal) {
          segmentFinalText += (segmentFinalText ? ' ' : '') + transcript;
        } else {
          interimText += (interimText ? ' ' : '') + transcript;
        }
      }

      // Coalesce final recognition segment into accumulated utterance buffer
      if (segmentFinalText.trim()) {
        this.accumulatedText = (this.accumulatedText + ' ' + segmentFinalText).trim();
      }

      // Emit full display interim (accumulated text + current active interim segment)
      const fullDisplayInterim = (this.accumulatedText + ' ' + interimText).trim();
      if (fullDisplayInterim) {
        this.currentUtteranceText = fullDisplayInterim;
        this.onInterimCallbacks.forEach((cb) => cb(fullDisplayInterim));

        if (this.isVadActive()) {
          // User is actively speaking: cancel any pending silence endpoint timer
          if (this.endpointTimerId) {
            window.clearTimeout(this.endpointTimerId);
            this.endpointTimerId = null;
          }
        } else {
          // If VAD is already ended or not active, arm the silence timer
          this.resetEndpointTimer();
        }
      }
    };

    this.recognition.onerror = (event: any) => {
      const errMsg = event.error || 'Unknown speech recognition error';
      // 'no-speech' is normal when user is silent
      if (errMsg !== 'no-speech') {
        this.onErrorCallbacks.forEach((cb) => cb(errMsg));
      }
    };

    this.recognition.onend = () => {
      this.isListening = false;
      this.onEndCallbacks.forEach((cb) => cb());

      // If user is not actively speaking and there is buffered speech, arm endpoint timer
      if (!this.isVadActive()) {
        const textToCommit = (this.currentUtteranceText || this.accumulatedText).trim();
        if (textToCommit && !this.endpointTimerId) {
          this.resetEndpointTimer();
        }
      }

      // Auto-restart if microphone is meant to stay continuous
      if (this.shouldRestart) {
        try {
          this.recognition.start();
        } catch (e) {
          // Restart attempts will retry on next user interaction if throttled
        }
      }
    };
  }

  public start(): void {
    if (!this.recognition) {
      this.onErrorCallbacks.forEach((cb) => cb('Browser Speech Recognition not supported in this browser.'));
      return;
    }

    this.shouldRestart = true;
    if (!this.isListening) {
      try {
        this.recognition.start();
      } catch (err) {
        // Recognition might already be running
      }
    }
  }

  public stop(): void {
    this.shouldRestart = false;
    if (this.recognition && this.isListening) {
      try {
        this.recognition.stop();
      } catch (err) {
        // Safe ignore
      }
    }
    this.isListening = false;
  }

  public onInterim(cb: (text: string) => void): void {
    this.onInterimCallbacks.push(cb);
  }

  public onFinal(cb: (text: string) => void): void {
    this.onFinalCallbacks.push(cb);
  }

  public onStart(cb: () => void): void {
    this.onStartCallbacks.push(cb);
  }

  public onEnd(cb: () => void): void {
    this.onEndCallbacks.push(cb);
  }

  public onError(cb: (error: string) => void): void {
    this.onErrorCallbacks.push(cb);
  }
}

export const speechRecognition = new BrowserSpeechRecognition();

