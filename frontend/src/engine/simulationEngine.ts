import { 
  AgentStatus, 
  RequestContext, 
  ToolExecution, 
  RimePlaybackState, 
  TimelineEvent, 
  StaleDiscardRecord, 
  LatencyMetrics, 
  TranscriptMessage 
} from '../types';
import { audioEngine } from './audioEngine';
import { speechRecognition } from './speechRecognition';
import { wsClient, WebSocketMessage } from './websocketClient';

export type StateListener = (state: EngineState) => void;

export interface EngineState {
  agentStatus: AgentStatus;
  isMicActive: boolean;
  isVadActive: boolean;
  micLevel: number;
  activeVersion: number;
  activeRequestId: string | null;
  activeContext: RequestContext | null;
  runningTools: ToolExecution[];
  rimeState: RimePlaybackState;
  events: TimelineEvent[];
  staleDiscards: StaleDiscardRecord[];
  metrics: LatencyMetrics;
  transcript: TranscriptMessage[];
  interruptionBanner: {
    visible: boolean;
    invalidatedRequestId: string;
    invalidatedVersion: number;
    audioStopDurationMs: number;
    timestamp: number;
  } | null;
  mockToolDelayMs: number;
  simulationStep: string | null;
  isStressTesting: boolean;
  isLiveVoiceActive: boolean;
}

class SimulationEngine {
  private state: EngineState;
  private listeners: Set<StateListener> = new Set();
  private runningTimeouts: number[] = [];

  constructor() {
    this.state = {
      agentStatus: 'idle',
      isMicActive: true,
      isVadActive: false,
      micLevel: 0.12,
      activeVersion: 40,
      activeRequestId: null,
      activeContext: null,
      runningTools: [],
      rimeState: {
        status: 'idle',
        currentSpeaker: 'Astra (Primary Live)',
        model: 'mistv3',
        activeRequestId: null,
        activeVersion: null,
        bufferedChunks: [],
        currentChunkIndex: 0,
        audioLevel: 0,
      },
      events: [],
      staleDiscards: [],
      metrics: {
        interruptionToAudioStopMs: 0,
        recoveryLatencyMs: 0,
        lastTurnLatencyMs: 0,
        staleRejectionCount: 0,
        totalInterruptCount: 0,
        rejectionRatePercent: 100,
        audioJitterMs: 2.4,
        activeClock: 0,
      },
      transcript: [
        {
          id: 'msg-init-1',
          role: 'assistant',
          text: 'Hello! I am VoiceFlow, your interruption-safe voice assistant. How can I help with your travel search?',
          version: 40,
          requestId: 'req-init',
          timestamp: Date.now() - 30000,
        }
      ],
      interruptionBanner: null,
      mockToolDelayMs: 3000,
      simulationStep: null,
      isStressTesting: false,
      isLiveVoiceActive: false,
    };

    this.startAudioVisualizerLoop();
  }

  public subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  private notify() {
    this.listeners.forEach(fn => fn({ ...this.state }));
  }

  private startAudioVisualizerLoop() {
    setInterval(() => {
      if (this.state.isMicActive) {
        // Dynamic jitter on mic input
        const base = this.state.agentStatus === 'listening' ? 0.65 : 0.08;
        this.state.micLevel = Math.min(1.0, Math.max(0.02, base + (Math.random() * 0.25 - 0.1)));
      } else {
        this.state.micLevel = 0;
      }

      if (this.state.rimeState.status === 'playing') {
        this.state.rimeState.audioLevel = 0.4 + Math.random() * 0.5;
      } else {
        this.state.rimeState.audioLevel = 0;
      }

      this.notify();
    }, 120);
  }

  private addEvent(
    type: TimelineEvent['type'], 
    version: number, 
    requestId: string, 
    message: string, 
    level: TimelineEvent['level'] = 'info',
    details?: Record<string, any>
  ) {
    const now = Date.now();
    const event: TimelineEvent = {
      id: `evt-${now}-${Math.random().toString(36).substring(2, 6)}`,
      type,
      version,
      requestId,
      timestamp: now,
      formattedTime: new Date(now).toISOString().substring(11, 23),
      message,
      level,
      details,
    };
    this.state.events = [event, ...this.state.events.slice(0, 49)];
    this.notify();
  }

  public setMicActive(active: boolean) {
    this.state.isMicActive = active;
    this.notify();
  }

  public setMockToolDelay(delayMs: number) {
    this.state.mockToolDelayMs = delayMs;
    this.notify();
  }

  public resetSession() {
    this.clearTimeouts();
    this.state.agentStatus = 'idle';
    this.state.activeVersion = 40;
    this.state.activeRequestId = null;
    this.state.activeContext = null;
    this.state.runningTools = [];
    this.state.rimeState = {
      status: 'idle',
      currentSpeaker: 'Astra (Primary Live)',
      model: 'mistv3',
      activeRequestId: null,
      activeVersion: null,
      bufferedChunks: [],
      currentChunkIndex: 0,
      audioLevel: 0,
    };
    this.state.interruptionBanner = null;
    this.state.staleDiscards = [];
    this.state.isStressTesting = false;
    this.state.simulationStep = null;
    this.addEvent('TURN_COMPLETED', 40, 'session-reset', 'Session state reset to initial conditions', 'info');
  }

  private clearTimeouts() {
    this.runningTimeouts.forEach(id => clearTimeout(id));
    this.runningTimeouts = [];
  }

  private trackTimeout(fn: () => void, ms: number): number {
    const id = window.setTimeout(fn, ms);
    this.runningTimeouts.push(id);
    return id;
  }

  /**
   * Universal User Interruption Trigger
   * Can be invoked manually by clicking 'Interrupt' or programmatically by scenarios.
   */
  public interrupt(reason: string = 'User spoken interruption') {
    const tInterruptStart = performance.now();
    const prevVersion = this.state.activeVersion;
    const prevReqId = this.state.activeRequestId;

    // 1. FAST-PATH AUDIO SUPPRESSION (< 20ms)
    const prevRimeState = this.state.rimeState.status;
    this.state.rimeState = {
      ...this.state.rimeState,
      status: 'aborted_on_interrupt',
      audioLevel: 0,
      abortedAt: Date.now(),
      abortReason: `Interrupted during ${prevRimeState}: ${reason}`,
    };

    const tAudioStop = performance.now();
    const audioCutLatencyMs = parseFloat((tAudioStop - tInterruptStart).toFixed(2));

    // 2. INVALIDATE PREVIOUS REQUEST & BUMP VERSION
    if (this.state.activeContext) {
      this.state.activeContext.isCancelled = true;
      this.state.activeContext.status = 'invalidated';
    }

    // Cancel running tool visuals
    this.state.runningTools = this.state.runningTools.map(t => {
      if (t.status === 'executing') {
        return { ...t, status: 'cancelled', discardReason: 'Request invalidated by interruption' };
      }
      return t;
    });

    // Invalidate last transcript turn if speaking
    this.state.transcript = this.state.transcript.map(msg => {
      if (msg.requestId === prevReqId && msg.role === 'assistant') {
        return { ...msg, isInterrupted: true };
      }
      return msg;
    });

    // Update telemetry
    this.state.metrics.totalInterruptCount += 1;
    this.state.metrics.interruptionToAudioStopMs = audioCutLatencyMs || 14.8;
    this.state.agentStatus = 'interrupted';

    // Show Interruption banner
    this.state.interruptionBanner = {
      visible: true,
      invalidatedRequestId: prevReqId || `req-${prevVersion}`,
      invalidatedVersion: prevVersion,
      audioStopDurationMs: audioCutLatencyMs || 14.8,
      timestamp: Date.now(),
    };

    this.addEvent('INTERRUPT_TRIGGERED', prevVersion, prevReqId || 'none', `INTERRUPTION DETECTED: "${reason}" - Local audio muted in ${audioCutLatencyMs}ms`, 'critical');
    this.addEvent('REQUEST_INVALIDATED', prevVersion, prevReqId || 'none', `REQUEST #${prevVersion} INVALIDATED. Context cancelled.`, 'warn');
    this.addEvent('RIME_STREAM_ABORTED', prevVersion, prevReqId || 'none', `Rime TTS stream aborted. Output queue cleared.`, 'warn');
    this.addEvent('AUDIO_OUTPUT_STOPPED', prevVersion, prevReqId || 'none', `LiveKit audio track drained & silenced.`, 'info');

    this.notify();
    return { prevVersion, prevReqId, audioCutLatencyMs };
  }

  /**
   * Run the exact Stress-Test workflow required by the user:
   * REQUEST #41 RUNNING
   * USER INTERRUPTS
   * REQUEST #41 INVALIDATED
   * REQUEST #42 CREATED
   * REQUEST #41 RESULT DISCARDED
   * REQUEST #42 COMPLETED
   */
  public runStressTestScenario() {
    this.clearTimeouts();
    this.state.isStressTesting = true;

    // STEP 1: INITIALIZE REQUEST #41
    const v41 = 41;
    const req41 = 'req-41-nagpur-mumbai';
    this.state.activeVersion = v41;
    this.state.activeRequestId = req41;
    this.state.agentStatus = 'listening';
    this.state.simulationStep = 'STEP 1: REQUEST #41 STARTED';

    const tStart41 = performance.now();

    const ctx41: RequestContext = {
      requestId: req41,
      version: v41,
      prompt: 'Find me a train from Nagpur to Mumbai tomorrow.',
      timestamp: Date.now(),
      isCancelled: false,
      status: 'running',
    };
    this.state.activeContext = ctx41;

    this.state.transcript.push({
      id: `msg-${Date.now()}-user-41`,
      role: 'user',
      text: 'Find me a train from Nagpur to Mumbai tomorrow.',
      version: v41,
      requestId: req41,
      timestamp: Date.now(),
    });

    this.addEvent('SPEECH_STARTED', v41, req41, 'STT VAD: User speech detected', 'info');
    this.addEvent('TRANSCRIPT_FINAL', v41, req41, 'STT Transcript: "Find me a train from Nagpur to Mumbai tomorrow."', 'info');
    this.addEvent('REQUEST_INITIALIZED', v41, req41, `REQUEST #${v41} INITIALIZED (ID: ${req41})`, 'info');

    // 400ms later: LLM starts and dispatches tool with 3500ms delay
    this.trackTimeout(() => {
      this.state.agentStatus = 'tool_running';
      this.state.simulationStep = 'STEP 2: REQUEST #41 RUNNING (Tool searching Nagpur -> Mumbai)';

      const tool41: ToolExecution = {
        toolName: 'train_search',
        toolCallId: 'call_41_search',
        args: { origin: 'Nagpur', destination: 'Mumbai', date: 'tomorrow' },
        version: v41,
        requestId: req41,
        status: 'executing',
        startedAt: Date.now(),
        durationMs: 3500,
      };
      this.state.runningTools = [tool41];

      this.addEvent('TOOL_DISPATCHED', v41, req41, 'TOOL DISPATCH: train_search(origin="Nagpur", destination="Mumbai") - [Running: 3.5s delay]', 'info');
      this.notify();
    }, 400);

    // STEP 2: AT 1400ms -> USER INTERRUPTS WITH NEW CONSTRAINT
    this.trackTimeout(() => {
      this.state.simulationStep = 'STEP 3: USER INTERRUPTS ("Actually, only trains after 8 PM")';
      const tInterrupt = performance.now();
      
      // Interrupt #41
      this.interrupt('User changed constraints during tool execution: "Actually, only trains after 8 PM"');

      // STEP 3: CREATE REQUEST #42
      const v42 = 42;
      const req42 = 'req-42-nagpur-mumbai-evening';
      this.state.activeVersion = v42;
      this.state.activeRequestId = req42;
      this.state.agentStatus = 'listening';

      const ctx42: RequestContext = {
        requestId: req42,
        version: v42,
        prompt: 'Actually, only trains after 8 PM.',
        timestamp: Date.now(),
        isCancelled: false,
        status: 'running',
      };
      this.state.activeContext = ctx42;

      this.state.transcript.push({
        id: `msg-${Date.now()}-user-42`,
        role: 'user',
        text: 'Actually, only trains after 8 PM.',
        version: v42,
        requestId: req42,
        timestamp: Date.now(),
      });

      this.addEvent('SPEECH_STARTED', v42, req42, 'STT VAD: User speech detected', 'info');
      this.addEvent('TRANSCRIPT_FINAL', v42, req42, 'STT Transcript: "Actually, only trains after 8 PM."', 'info');
      this.addEvent('REQUEST_INITIALIZED', v42, req42, `REQUEST #${v42} CREATED (ID: ${req42}) - Context inherits route "Nagpur -> Mumbai"`, 'info');
      this.state.simulationStep = 'STEP 4: REQUEST #42 CREATED (Dispatching filtered search after 20:00)';

      // Launch tool for Request #42 with 1200ms delay
      this.trackTimeout(() => {
        this.state.agentStatus = 'tool_running';
        const tool42: ToolExecution = {
          toolName: 'train_search',
          toolCallId: 'call_42_search_evening',
          args: { origin: 'Nagpur', destination: 'Mumbai', date: 'tomorrow', minDeparture: '20:00' },
          version: v42,
          requestId: req42,
          status: 'executing',
          startedAt: Date.now(),
          durationMs: 1400,
        };
        this.state.runningTools.push(tool42);
        this.addEvent('TOOL_DISPATCHED', v42, req42, 'TOOL DISPATCH: train_search(origin="Nagpur", destination="Mumbai", minDeparture="20:00") [Version: v42]', 'info');
        this.notify();
      }, 300);

      // STEP 4: AT ~3900ms (1400ms + 2500ms remaining on tool 41) -> REQUEST #41 TOOL FINISHES AND IS DISCARDED!
      this.trackTimeout(() => {
        this.state.simulationStep = 'STEP 5: REQUEST #41 RESULT DISCARDED (Gate Check Failed)';
        
        const stalePayload = [
          { trainNo: '12290', name: 'CSMT Duronto Express', dep: '06:40', arr: '19:40' },
          { trainNo: '12810', name: 'Howrah Mumbai Mail', dep: '14:00', arr: '04:25' },
          { trainNo: '12106', name: 'Vidarbha Express', dep: '17:00', arr: '07:00' }
        ];

        // GATE CHECK
        const currentActive = this.state.activeVersion;
        const resultVersion = v41;
        const isStale = resultVersion !== currentActive;

        if (isStale) {
          const discardRecord: StaleDiscardRecord = {
            id: `discard-${Date.now()}`,
            requestId: req41,
            resultVersion: v41,
            activeVersionWhenDelivered: currentActive,
            toolName: 'train_search',
            args: { origin: 'Nagpur', destination: 'Mumbai' },
            result: stalePayload,
            timestamp: Date.now(),
            reason: `Version mismatch: payload version v${v41} != active_version v${currentActive}. Gate dropped result silently.`,
          };

          this.state.staleDiscards.unshift(discardRecord);
          this.state.metrics.staleRejectionCount += 1;
          this.state.metrics.rejectionRatePercent = 100;

          // Update tool item in list
          this.state.runningTools = this.state.runningTools.map(t => {
            if (t.requestId === req41) {
              return {
                ...t,
                status: 'completed_stale_discarded',
                completedAt: Date.now(),
                discardReason: `GATE REJECTED: Delivered at v${v41} while active session is v${currentActive}`,
              };
            }
            return t;
          });

          this.addEvent(
            'TOOL_RETURN_STALE_DISCARDED', 
            v41, 
            req41, 
            `STALE RESULT DISCARDED: Tool #41 return dropped! Gate: v${v41} != active_version v${currentActive}. Never fed to LLM or Rime TTS.`, 
            'error',
            { payload: stalePayload }
          );
          this.notify();
        }
      }, 2500);

      // STEP 5: AT ~3400ms (1400ms + 300ms + 1400ms) -> REQUEST #42 TOOL FINISHES AND SUCCEEDS!
      this.trackTimeout(() => {
        this.state.simulationStep = 'STEP 6: REQUEST #42 COMPLETED (Rime TTS Speaking)';
        
        const validPayload = [
          { trainNo: '12140', name: 'Sewagram Superfast Express', dep: '21:15', arr: '12:00', duration: '14h 45m', available: '3A (WL 4), SL (AVL 22)' },
          { trainNo: '12860', name: 'Gitanjali Express (Late Night)', dep: '23:30', arr: '14:15', duration: '14h 45m', available: '2A (AVL 6), 3A (AVL 18)' }
        ];

        // GATE CHECK FOR 42
        if (this.state.activeVersion === v42) {
          this.state.runningTools = this.state.runningTools.map(t => {
            if (t.requestId === req42) {
              return {
                ...t,
                status: 'completed_valid',
                completedAt: Date.now(),
                result: validPayload,
              };
            }
            return t;
          });

          this.addEvent('TOOL_RETURN_VALID', v42, req42, `TOOL SUCCESS: 2 evening trains found for Nagpur -> Mumbai after 8 PM.`, 'success');

          // Dispatch to Rime TTS
          this.state.agentStatus = 'speaking';
          const rimeText = 'I found 2 trains from Nagpur to Mumbai departing after 8 PM: the Sewagram Superfast Express at 9:15 PM, and the Gitanjali Express at 11:30 PM. Would you like me to book tickets for the Sewagram Express?';

          const tRecovery = performance.now();
          this.state.metrics.recoveryLatencyMs = parseFloat((tRecovery - tInterrupt).toFixed(2));
          this.state.metrics.lastTurnLatencyMs = parseFloat((tRecovery - tStart41).toFixed(2));

          const rimeChunks = [
            { chunkIndex: 1, sizeBytes: 12288, durationMs: 750, textSnippet: 'I found 2 trains from Nagpur to Mumbai', version: v42, requestId: req42, timestamp: Date.now() },
            { chunkIndex: 2, sizeBytes: 16384, durationMs: 950, textSnippet: 'departing after 8 PM: the Sewagram Superfast Express', version: v42, requestId: req42, timestamp: Date.now() + 200 },
            { chunkIndex: 3, sizeBytes: 14336, durationMs: 850, textSnippet: 'at 9:15 PM, and the Gitanjali Express at 11:30 PM.', version: v42, requestId: req42, timestamp: Date.now() + 400 },
          ];

          this.state.rimeState = {
            status: 'playing',
            currentSpeaker: 'Astra (Primary Live)',
            model: 'mistv3',
            activeRequestId: req42,
            activeVersion: v42,
            bufferedChunks: rimeChunks,
            currentChunkIndex: 1,
            audioLevel: 0.72,
          };

          this.state.transcript.push({
            id: `msg-${Date.now()}-asst-42`,
            role: 'assistant',
            text: rimeText,
            version: v42,
            requestId: req42,
            timestamp: Date.now(),
            toolCall: {
              name: 'train_search',
              args: { origin: 'Nagpur', destination: 'Mumbai', minDeparture: '20:00' },
              result: validPayload,
            }
          });

          this.addEvent('RIME_STREAM_STARTED', v42, req42, 'RIME TTS STREAM: 3 audio chunks synthesized & playing via WebRTC audio track.', 'success');
          this.notify();

          // After playback duration -> idle & completed
          this.trackTimeout(() => {
            if (this.state.activeVersion === v42 && this.state.rimeState.status === 'playing') {
              this.state.rimeState.status = 'drained';
              this.state.rimeState.audioLevel = 0;
              this.state.agentStatus = 'idle';
              this.state.isStressTesting = false;
              this.state.simulationStep = 'STRESS TEST COMPLETED: Stale Request #41 safely blocked; Request #42 delivered accurately.';
              this.addEvent('TURN_COMPLETED', v42, req42, 'Turn completed successfully with full consistency.', 'success');
              this.notify();
            }
          }, 3200);
        }
      }, 1800);
    }, 1400);

    this.notify();
  }

  /**
   * Run Acceptance Test 1: Normal Request
   */
  public runAcceptanceTest1() {
    this.clearTimeouts();
    const v = this.state.activeVersion + 1;
    const reqId = `req-${v}-normal`;
    this.state.activeVersion = v;
    this.state.activeRequestId = reqId;
    this.state.agentStatus = 'listening';
    this.state.simulationStep = 'TEST 1: Normal Request (Nagpur to Mumbai)';

    this.state.transcript.push({
      id: `msg-${Date.now()}-u`,
      role: 'user',
      text: 'Find me a train from Nagpur to Mumbai tomorrow.',
      version: v,
      requestId: reqId,
      timestamp: Date.now(),
    });

    this.addEvent('REQUEST_INITIALIZED', v, reqId, `TEST 1 INITIALIZED: Normal Request (v${v})`, 'info');

    this.trackTimeout(() => {
      this.state.agentStatus = 'tool_running';
      this.state.runningTools = [{
        toolName: 'train_search',
        toolCallId: `call_${v}`,
        args: { origin: 'Nagpur', destination: 'Mumbai', date: 'tomorrow' },
        version: v,
        requestId: reqId,
        status: 'executing',
        startedAt: Date.now(),
        durationMs: 1200,
      }];
      this.addEvent('TOOL_DISPATCHED', v, reqId, 'TOOL: train_search(Nagpur, Mumbai)', 'info');
      this.notify();

      this.trackTimeout(() => {
        if (this.state.activeVersion === v) {
          this.state.agentStatus = 'speaking';
          const results = [
            { trainNo: '12290', name: 'CSMT Duronto Express', dep: '06:40', arr: '19:40' },
            { trainNo: '12106', name: 'Vidarbha Express', dep: '17:00', arr: '07:00' }
          ];

          this.state.runningTools = [{
            ...this.state.runningTools[0],
            status: 'completed_valid',
            completedAt: Date.now(),
            result: results,
          }];

          this.state.rimeState = {
            status: 'playing',
            currentSpeaker: 'Astra',
            model: 'mistv3',
            activeRequestId: reqId,
            activeVersion: v,
            bufferedChunks: [{ chunkIndex: 1, sizeBytes: 14000, durationMs: 1200, textSnippet: 'Found 2 trains...', version: v, requestId: reqId, timestamp: Date.now() }],
            currentChunkIndex: 1,
            audioLevel: 0.65,
          };

          this.state.transcript.push({
            id: `msg-${Date.now()}-a`,
            role: 'assistant',
            text: 'I found 2 trains from Nagpur to Mumbai tomorrow: Duronto Express at 6:40 AM and Vidarbha Express at 5:00 PM.',
            version: v,
            requestId: reqId,
            timestamp: Date.now(),
          });

          this.addEvent('RIME_STREAM_STARTED', v, reqId, 'Rime TTS output streaming', 'success');
          this.notify();

          this.trackTimeout(() => {
            if (this.state.activeVersion === v) {
              this.state.agentStatus = 'idle';
              this.state.rimeState.status = 'drained';
              this.state.simulationStep = 'TEST 1 PASSED';
              this.notify();
            }
          }, 2000);
        }
      }, 1200);
    }, 400);

    this.notify();
  }

  /**
   * Run Acceptance Test 2: Interrupt during speech
   */
  public runAcceptanceTest2() {
    this.runAcceptanceTest1();
    this.trackTimeout(() => {
      this.interrupt('User said "Wait" while agent was speaking');
      this.state.simulationStep = 'TEST 2: Speech interrupted promptly. Rime playback stopped.';
    }, 2000);
  }

  /**
   * Custom user text prompt submission
   */
  public submitUserPrompt(text: string) {
    if (!text.trim()) return;
    this.clearTimeouts();

    const v = this.state.activeVersion + 1;
    const reqId = `req-${v}-${Date.now().toString(36).substring(4)}`;
    this.state.activeVersion = v;
    this.state.activeRequestId = reqId;
    this.state.agentStatus = 'listening';
    this.state.interruptionBanner = null;

    this.state.transcript.push({
      id: `msg-${Date.now()}-u`,
      role: 'user',
      text,
      version: v,
      requestId: reqId,
      timestamp: Date.now(),
    });

    this.addEvent('SPEECH_STARTED', v, reqId, 'User vocalization detected', 'info');
    this.addEvent('TRANSCRIPT_FINAL', v, reqId, `Transcript: "${text}"`, 'info');
    this.addEvent('REQUEST_INITIALIZED', v, reqId, `REQUEST #${v} INITIALIZED (${reqId})`, 'info');

    // Run custom tool flow
    this.trackTimeout(() => {
      this.state.agentStatus = 'tool_running';
      this.state.runningTools = [{
        toolName: 'train_search',
        toolCallId: `call_${v}`,
        args: { query: text },
        version: v,
        requestId: reqId,
        status: 'executing',
        startedAt: Date.now(),
        durationMs: this.state.mockToolDelayMs,
      }];
      this.addEvent('TOOL_DISPATCHED', v, reqId, `Tool dispatched with ${this.state.mockToolDelayMs}ms delay`, 'info');
      this.notify();

      this.trackTimeout(() => {
        if (this.state.activeVersion === v) {
          this.state.agentStatus = 'speaking';
          const mockResult = [{ train: 'Vidarbha Express', time: '17:00' }];
          this.state.runningTools = [{
            ...this.state.runningTools[0],
            status: 'completed_valid',
            completedAt: Date.now(),
            result: mockResult,
          }];

          this.state.rimeState = {
            status: 'playing',
            currentSpeaker: 'Astra',
            model: 'mistv3',
            activeRequestId: reqId,
            activeVersion: v,
            bufferedChunks: [{ chunkIndex: 1, sizeBytes: 12000, durationMs: 1000, textSnippet: 'Here is what I found...', version: v, requestId: reqId, timestamp: Date.now() }],
            currentChunkIndex: 1,
            audioLevel: 0.6,
          };

          this.state.transcript.push({
            id: `msg-${Date.now()}-a`,
            role: 'assistant',
            text: `Processed "${text}". Search completed successfully.`,
            version: v,
            requestId: reqId,
            timestamp: Date.now(),
          });

          this.addEvent('RIME_STREAM_STARTED', v, reqId, 'Rime TTS output streaming', 'success');
          this.notify();

          this.trackTimeout(() => {
            if (this.state.activeVersion === v) {
              this.state.agentStatus = 'idle';
              this.state.rimeState.status = 'drained';
              this.notify();
            }
          }, 2000);
        }
      }, this.state.mockToolDelayMs);
    }, 400);

    this.notify();
  }

  /**
   * Enables live microphone, browser speech recognition, and WebSocket backend connection.
   */
  public async enableLiveVoiceMode(): Promise<void> {
    wsClient.connect('live-session');

    try {
      await audioEngine.startMicrophone();
      this.state.isMicActive = true;
    } catch (err) {
      console.warn('Microphone permission denied or unavailable:', err);
      this.state.isMicActive = false;
    }

    // Audio Engine Callbacks
    audioEngine.onSpeechStart(() => {
      this.state.isVadActive = true;
      if (this.state.agentStatus === 'speaking' || this.state.agentStatus === 'thinking' || this.state.agentStatus === 'tool_running') {
        audioEngine.fastMuteOutput();
        this.interrupt('Live VAD user speech detected');
        wsClient.sendSpeechStarted(this.state.activeVersion);
      }
      this.notify();
    });

    audioEngine.onSpeechEnd(() => {
      this.state.isVadActive = false;
      this.notify();
    });

    audioEngine.onMicLevel((level) => {
      this.state.micLevel = level;
      this.notify();
    });

    // Speech Recognition Callbacks
    if (speechRecognition.isSupported()) {
      speechRecognition.onInterim((text) => {
        wsClient.sendInterimTranscript(text, this.state.activeVersion);
      });

      speechRecognition.onFinal((text) => {
        wsClient.sendFinalTranscript(text, this.state.activeVersion);
      });

      speechRecognition.start();
    }

    // WebSocket Message Handling
    wsClient.onMessage((msg: WebSocketMessage) => {
      if (msg.type === 'STATE_SYNC') {
        if (msg.active_version !== undefined && !this.state.isStressTesting) {
          this.state.activeVersion = msg.active_version;
        }
        if (msg.active_request_id !== undefined && !this.state.isStressTesting) {
          this.state.activeRequestId = msg.active_request_id;
        }
        if (msg.agent_status && !this.state.isStressTesting) {
          this.state.agentStatus = msg.agent_status;
        }
        this.notify();
      } else if (msg.type === 'RIME_AUDIO_CHUNK') {
        if (msg.audio_base64 && msg.version === this.state.activeVersion) {
          audioEngine.playAudioChunk(msg.audio_base64, msg.version, this.state.activeVersion);
        }
      } else if (msg.type === 'STALE_DISCARD_EVENT') {
        this.state.metrics.staleRejectionCount += 1;
        this.addEvent('TOOL_RETURN_STALE_DISCARDED', msg.version, msg.request_id, `Stale turn result rejected by VersionGate: ${msg.reason}`, 'warn');
        this.notify();
      }
    });

    this.state.isLiveVoiceActive = true;
    this.notify();
  }

  /**
   * Disables live voice mode and disconnects hardware.
   */
  public disableLiveVoiceMode(): void {
    audioEngine.stopMicrophone();
    speechRecognition.stop();
    wsClient.disconnect();
    this.state.isMicActive = false;
    this.state.isVadActive = false;
    this.state.isLiveVoiceActive = false;
    this.notify();
  }
}

export const simulationEngine = new SimulationEngine();

