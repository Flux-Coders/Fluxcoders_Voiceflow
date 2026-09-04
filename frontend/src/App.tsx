import React, { useState, useEffect } from 'react';
import { EngineState, simulationEngine } from './engine/simulationEngine';
import { Header } from './components/Header';
import { MicrophoneStatus } from './components/MicrophoneStatus';
import { ActiveRequestBadge } from './components/ActiveRequestBadge';
import { ToolExecutionPanel } from './components/ToolExecutionPanel';
import { RimePlaybackPanel } from './components/RimePlaybackPanel';
import { InterruptionBanner } from './components/InterruptionBanner';
import { StaleResultGuardPanel } from './components/StaleResultGuardPanel';
import { LatencyMetricsPanel } from './components/LatencyMetricsPanel';
import { TranscriptView } from './components/TranscriptView';
import { EventTimeline } from './components/EventTimeline';
import { StressTestController } from './components/StressTestController';

export const App: React.FC = () => {
  const [state, setState] = useState<EngineState>(() => {
    let initial: any;
    simulationEngine.subscribe((s) => (initial = s));
    return initial;
  });

  useEffect(() => {
    const unsubscribe = simulationEngine.subscribe((newState) => {
      setState(newState);
    });
    return () => unsubscribe();
  }, []);

  return (
    <div className="min-h-screen bg-[#090d16] text-slate-100 flex flex-col font-sans">
      <Header state={state} />

      <main className="flex-1 max-w-7xl w-full mx-auto p-4 lg:p-6 space-y-4">
        {/* Interruption Notification Banner (Req 8) */}
        <InterruptionBanner 
          banner={state.interruptionBanner} 
          activeVersion={state.activeVersion} 
        />

        {/* Interactive Scenario & Stress Test Controller */}
        <StressTestController state={state} />

        {/* 2-Column Responsive Developer Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          {/* Left Column: Voice Engine, State, Tools, TTS, Guards & Metrics (7 cols) */}
          <div className="lg:col-span-7 space-y-4">
            {/* Req 1: Microphone Status */}
            <MicrophoneStatus
              isMicActive={state.isMicActive}
              micLevel={state.micLevel}
              isVadActive={state.isVadActive}
              agentStatus={state.agentStatus}
            />

            {/* Req 3 & 4: Active Request & Version */}
            <ActiveRequestBadge
              activeVersion={state.activeVersion}
              activeRequestId={state.activeRequestId}
              activeContext={state.activeContext}
              agentStatus={state.agentStatus}
            />

            {/* Req 5: Tool Execution Status */}
            <ToolExecutionPanel
              runningTools={state.runningTools}
              mockToolDelayMs={state.mockToolDelayMs}
              activeVersion={state.activeVersion}
            />

            {/* Req 6: Rime Playback Status */}
            <RimePlaybackPanel
              rimeState={state.rimeState}
              activeVersion={state.activeVersion}
            />

            {/* Req 9: Stale-Result Indicator */}
            <StaleResultGuardPanel
              staleDiscards={state.staleDiscards}
              activeVersion={state.activeVersion}
            />

            {/* Req 10: Latency Metrics Panel */}
            <LatencyMetricsPanel
              metrics={state.metrics}
            />
          </div>

          {/* Right Column: Transcript & Event Timeline (5 cols) */}
          <div className="lg:col-span-5 space-y-4">
            {/* Req 2: Conversation Transcript */}
            <TranscriptView
              transcript={state.transcript}
              activeVersion={state.activeVersion}
            />

            {/* Req 7: Event Timeline */}
            <EventTimeline
              events={state.events}
            />
          </div>
        </div>
      </main>

      <footer className="border-t border-slate-900 bg-slate-950/80 px-6 py-3 text-center text-xs font-mono text-slate-500">
        VoiceFlow v0.1.0 • Interruption-Safe Architecture • Primary TTS: Rime • High-Performance WebRTC Pipeline
      </footer>
    </div>
  );
};

export default App;

