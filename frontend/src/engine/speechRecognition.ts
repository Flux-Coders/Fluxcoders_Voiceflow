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

  private onInterimCallbacks: Array<(text: string) => void> = [];
  private onFinalCallbacks: Array<(text: string) => void> = [];
  private onStartCallbacks: Array<() => void> = [];
  private onEndCallbacks: Array<() => void> = [];
  private onErrorCallbacks: Array<(error: string) => void> = [];

  constructor(config?: Partial<SpeechRecognitionConfig>) {
    const SpeechRecognitionClass =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (SpeechRecognitionClass) {
      this.recognition = new SpeechRecognitionClass();
      this.recognition.lang = config?.lang || 'en-US';
      this.recognition.continuous = config?.continuous !== undefined ? config.continuous : true;
      this.recognition.interimResults = config?.interimResults !== undefined ? config.interimResults : true;
      this.recognition.maxAlternatives = config?.maxAlternatives || 1;

      this.setupHandlers();
    }
  }

  public isSupported(): boolean {
    return this.recognition !== null;
  }

  private setupHandlers(): void {
    if (!this.recognition) return;

    this.recognition.onstart = () => {
      this.isListening = true;
      this.onStartCallbacks.forEach((cb) => cb());
    };

    this.recognition.onresult = (event: any) => {
      let interimText = '';
      let finalText = '';

      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const result = event.results[i];
        const transcript = result[0]?.transcript || '';
        if (result.isFinal) {
          finalText += transcript;
        } else {
          interimText += transcript;
        }
      }

      if (interimText.trim()) {
        this.onInterimCallbacks.forEach((cb) => cb(interimText.trim()));
      }

      if (finalText.trim()) {
        this.onFinalCallbacks.forEach((cb) => cb(finalText.trim()));
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

