import React from 'react';
import { Mic, MicOff, Activity } from 'lucide-react';
import { simulationEngine } from '../engine/simulationEngine';

interface MicrophoneStatusProps {
  isMicActive: boolean;
  micLevel: number;
  isVadActive: boolean;
  agentStatus: string;
}

export const MicrophoneStatus: React.FC<MicrophoneStatusProps> = ({
  isMicActive,
  micLevel,
  agentStatus,
}) => {
  // Generate visual bars for the equalizer
  const barCount = 18;
  const bars = Array.from({ length: barCount }, (_, i) => {
    const centerFactor = 1 - Math.abs(i - barCount / 2) / (barCount / 2);
    const heightPercent = isMicActive
      ? Math.max(8, Math.min(100, micLevel * 100 * centerFactor + (Math.sin(i + Date.now() / 200) * 15)))
      : 5;
    return heightPercent;
  });

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded-lg ${isMicActive ? 'bg-cyan-500/10 text-cyan-400' : 'bg-rose-500/10 text-rose-400'}`}>
            {isMicActive ? <Mic className="w-4 h-4" /> : <MicOff className="w-4 h-4" />}
          </div>
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono">
              1. Microphone & VAD
            </h2>
            <div className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${isMicActive ? 'bg-emerald-400 animate-ping' : 'bg-slate-500'}`} />
              <span className="text-sm font-semibold font-mono text-slate-200">
                {isMicActive ? (agentStatus === 'listening' ? 'Vocalizing (VAD Active)' : 'Active / Monitoring') : 'Muted'}
              </span>
            </div>
          </div>
        </div>

        <button
          onClick={() => simulationEngine.setMicActive(!isMicActive)}
          className={`px-2.5 py-1 text-xs font-mono font-medium rounded-md border transition-all ${
            isMicActive
              ? 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
              : 'bg-rose-950/40 text-rose-300 border-rose-800/60'
          }`}
        >
          {isMicActive ? 'MUTE MIC' : 'UNMUTE MIC'}
        </button>
      </div>

      {/* Live Audio Level Meter & Waveform */}
      <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-2.5 flex items-center gap-3">
        <div className="flex-1 flex items-center justify-between gap-1 h-8 px-1">
          {bars.map((h, idx) => (
            <div
              key={idx}
              className={`w-1 rounded-full transition-all duration-75 ${
                !isMicActive
                  ? 'bg-slate-800'
                  : agentStatus === 'listening'
                  ? 'bg-cyan-400 shadow-sm shadow-cyan-400/50'
                  : 'bg-slate-600'
              }`}
              style={{ height: `${h}%` }}
            />
          ))}
        </div>
        <div className="w-16 text-right font-mono text-xs text-slate-400">
          {(micLevel * 100).toFixed(0)} dBFS
        </div>
      </div>

      {/* VAD Trigger Threshold Indicator */}
      <div className="mt-2.5 flex items-center justify-between text-[11px] font-mono text-slate-400 px-1">
        <span className="flex items-center gap-1">
          <Activity className="w-3 h-3 text-cyan-400" /> VAD Mode: Fast-Interrupt (Silero VAD WebWorker)
        </span>
        <span className="text-emerald-400 font-semibold">Threshold: 0.45</span>
      </div>
    </div>
  );
};

