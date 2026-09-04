export type AgentStatus = 
  | 'idle' 
  | 'listening' 
  | 'thinking' 
  | 'tool_running' 
  | 'speaking' 
  | 'interrupted';

export type ToolStatus = 
  | 'idle' 
  | 'executing' 
  | 'completed_valid' 
  | 'completed_stale_discarded' 
  | 'cancelled';

export type RimeStatus = 
  | 'idle' 
  | 'streaming_chunks' 
  | 'playing' 
  | 'aborted_on_interrupt' 
  | 'drained';

export interface RequestContext {
  requestId: string;
  version: number;
  prompt: string;
  timestamp: number;
  isCancelled: boolean;
  status: 'running' | 'completed' | 'invalidated' | 'discarded';
}

export interface ToolExecution {
  toolName: string;
  toolCallId: string;
  args: Record<string, any>;
  version: number;
  requestId: string;
  status: ToolStatus;
  startedAt: number;
  completedAt?: number;
  durationMs: number;
  result?: any;
  discardReason?: string;
}

export interface RimeChunk {
  chunkIndex: number;
  sizeBytes: number;
  durationMs: number;
  textSnippet: string;
  version: number;
  requestId: string;
  timestamp: number;
}

export interface RimePlaybackState {
  status: RimeStatus;
  currentSpeaker: string;
  model: string;
  activeRequestId: string | null;
  activeVersion: number | null;
  bufferedChunks: RimeChunk[];
  currentChunkIndex: number;
  audioLevel: number; // 0.0 to 1.0
  abortedAt?: number;
  abortReason?: string;
}

export interface TimelineEvent {
  id: string;
  type: 
    | 'SPEECH_STARTED' 
    | 'TRANSCRIPT_FINAL' 
    | 'REQUEST_INITIALIZED' 
    | 'LLM_PROMPT_STARTED' 
    | 'TOOL_DISPATCHED' 
    | 'INTERRUPT_TRIGGERED' 
    | 'REQUEST_INVALIDATED' 
    | 'TOOL_RETURN_VALID' 
    | 'TOOL_RETURN_STALE_DISCARDED' 
    | 'RIME_STREAM_STARTED' 
    | 'RIME_STREAM_ABORTED' 
    | 'AUDIO_OUTPUT_STOPPED' 
    | 'TURN_COMPLETED';
  version: number;
  requestId: string;
  timestamp: number;
  formattedTime: string;
  message: string;
  details?: Record<string, any>;
  level: 'info' | 'success' | 'warn' | 'error' | 'critical';
}

export interface StaleDiscardRecord {
  id: string;
  requestId: string;
  resultVersion: number;
  activeVersionWhenDelivered: number;
  toolName: string;
  args: Record<string, any>;
  result: any;
  timestamp: number;
  reason: string;
}

export interface LatencyMetrics {
  interruptionToAudioStopMs: number;
  recoveryLatencyMs: number;
  lastTurnLatencyMs: number;
  staleRejectionCount: number;
  totalInterruptCount: number;
  rejectionRatePercent: number;
  audioJitterMs: number;
  activeClock: number;
}

export interface TranscriptMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  text: string;
  version: number;
  requestId: string;
  timestamp: number;
  isInterrupted?: boolean;
  isInvalidated?: boolean;
  toolCall?: {
    name: string;
    args: any;
    result?: any;
    isStaleDiscarded?: boolean;
  };
}

