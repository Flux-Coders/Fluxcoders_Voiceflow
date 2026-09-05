/**
 * VoiceFlow Realtime WebSocket Client.
 * 
 * Manages bidirectional connection to backend /ws/session/{session_id},
 * forwarding VAD and STT events and receiving state sync and Rime audio chunks.
 */

export interface WebSocketMessage {
  type: string;
  [key: string]: any;
}

export class VoiceFlowWebSocketClient {
  private ws: WebSocket | null = null;
  private sessionId: string = 'live-session';
  private isConnected: boolean = false;
  private reconnectTimer: number | null = null;

  private messageListeners: Array<(msg: WebSocketMessage) => void> = [];
  private onConnectListeners: Array<() => void> = [];
  private onDisconnectListeners: Array<() => void> = [];

  public connect(sessionId: string = 'live-session'): void {
    this.sessionId = sessionId;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname || 'localhost';
    const port = '8000'; // FastAPI backend port
    const wsUrl = `${protocol}//${host}:${port}/ws/session/${sessionId}`;

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.isConnected = true;
        this.onConnectListeners.forEach((cb) => cb());
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data);
          this.messageListeners.forEach((cb) => cb(msg));
        } catch (err) {
          console.warn('Error parsing WebSocket message:', err);
        }
      };

      this.ws.onclose = () => {
        this.isConnected = false;
        this.onDisconnectListeners.forEach((cb) => cb());
      };

      this.ws.onerror = (err) => {
        console.warn('WebSocket encountered error:', err);
      };
    } catch (err) {
      console.warn('Could not connect to VoiceFlow WebSocket:', err);
    }
  }

  public disconnect(): void {
    if (this.reconnectTimer) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.isConnected = false;
  }

  public send(message: WebSocketMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  public sendSpeechStarted(activeVersion: number): void {
    this.send({
      type: 'SPEECH_STARTED',
      active_version: activeVersion,
      timestamp: performance.now(),
    });
  }

  public sendInterimTranscript(text: string, activeVersion: number): void {
    this.send({
      type: 'INTERIM_TRANSCRIPT',
      text,
      active_version: activeVersion,
    });
  }

  public sendFinalTranscript(text: string, activeVersion: number): void {
    this.send({
      type: 'FINAL_TRANSCRIPT',
      text,
      active_version: activeVersion,
    });
  }

  public sendInterrupt(reason: string = 'User barge-in', activeVersion: number = 0): void {
    this.send({
      type: 'CLIENT_INTERRUPT',
      reason,
      active_version: activeVersion,
    });
  }

  public onMessage(cb: (msg: WebSocketMessage) => void): void {
    this.messageListeners.push(cb);
  }

  public onConnect(cb: () => void): void {
    this.onConnectListeners.push(cb);
  }

  public onDisconnect(cb: () => void): void {
    this.onDisconnectListeners.push(cb);
  }

  public getConnected(): boolean {
    return this.isConnected;
  }

  public getSessionId(): string {
    return this.sessionId;
  }
}

export const wsClient = new VoiceFlowWebSocketClient();

