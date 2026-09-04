import React, { useState } from 'react';
import { EngineState, simulationEngine } from '../engine/simulationEngine';
import { 
  Zap, 
  Send, 
  Play, 
  AlertTriangle 
} from 'lucide-react';

interface StressTestControllerProps {
  state: EngineState;
}

export const StressTestController: React.FC<StressTestControllerProps> = ({ state }) => {
  const [inputText, setInputText] = useState('');

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputText.trim()) {
      simulationEngine.submitUserPrompt(inputText.trim());
      setInputText('');
    }
  };

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono flex items-center gap-1.5">
          <Zap className="w-3.5 h-3.5 text-amber-400" />
          Interactive Scenarios & Stress Test Mode
        </h2>
        {state.simulationStep && (
          <span className="text-xs font-mono font-bold text-cyan-300 bg-cyan-950/60 px-2.5 py-0.5 rounded border border-cyan-800/60 animate-pulse">
            {state.simulationStep}
          </span>
        )}
      </div>

      {/* Stress-Test Visual Step Progression */}
      <div className="bg-slate-950/80 border border-slate-800/80 rounded-lg p-3 mb-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-mono font-bold text-rose-300 flex items-center gap-1.5">
            <Zap className="w-4 h-4 fill-rose-400 text-rose-400" />
            Core Test Suite: Interruption & Stale Discard (#41 ➔ #42)
          </span>
          <button
            onClick={() => simulationEngine.runStressTestScenario()}
            disabled={state.isStressTesting}
            className={`px-3 py-1 rounded text-xs font-mono font-bold transition-all shadow-sm ${
              state.isStressTesting
                ? 'bg-rose-950 text-rose-400 border border-rose-800 cursor-not-allowed'
                : 'bg-gradient-to-r from-rose-600 to-amber-600 hover:from-rose-500 hover:to-amber-500 text-white shadow-rose-900/30'
            }`}
          >
            {state.isStressTesting ? 'SIMULATION IN PROGRESS...' : 'START STRESS TEST'}
          </button>
        </div>

        {/* 6 Step Visual Indicators */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 mt-3 font-mono text-[11px]">
          <div className={`p-2 rounded border transition-all ${
            state.activeVersion === 41 && state.runningTools.some(t => t.status === 'executing')
              ? 'bg-amber-950/40 border-amber-500/50 text-amber-300 shadow-sm shadow-amber-500/20'
              : 'bg-slate-900/60 border-slate-800 text-slate-500'
          }`}>
            <div className="font-bold">1. REQ #41</div>
            <div className="text-[10px]">Tool Running (3.5s)</div>
          </div>

          <div className={`p-2 rounded border transition-all ${
            state.interruptionBanner && state.interruptionBanner.invalidatedVersion === 41
              ? 'bg-rose-950/50 border-rose-500 text-rose-300 shadow-sm shadow-rose-500/30'
              : 'bg-slate-900/60 border-slate-800 text-slate-500'
          }`}>
            <div className="font-bold">2. INTERRUPT</div>
            <div className="text-[10px]">"Trains after 8 PM"</div>
          </div>

          <div className={`p-2 rounded border transition-all ${
            state.interruptionBanner && state.activeVersion >= 42
              ? 'bg-purple-950/40 border-purple-500 text-purple-300'
              : 'bg-slate-900/60 border-slate-800 text-slate-500'
          }`}>
            <div className="font-bold">3. INVALIDATE</div>
            <div className="text-[10px]">v41 marked dead</div>
          </div>

          <div className={`p-2 rounded border transition-all ${
            state.activeVersion === 42 && state.runningTools.some(t => t.version === 42)
              ? 'bg-cyan-950/40 border-cyan-500 text-cyan-300 shadow-sm shadow-cyan-500/20'
              : 'bg-slate-900/60 border-slate-800 text-slate-500'
          }`}>
            <div className="font-bold">4. REQ #42</div>
            <div className="text-[10px]">Filtered Search</div>
          </div>

          <div className={`p-2 rounded border transition-all ${
            state.staleDiscards.some(d => d.resultVersion === 41)
              ? 'bg-rose-950/60 border-rose-500 text-rose-300'
              : 'bg-slate-900/60 border-slate-800 text-slate-500'
          }`}>
            <div className="font-bold">5. DISCARD #41</div>
            <div className="text-[10px]">Gate check: v41 != v42</div>
          </div>

          <div className={`p-2 rounded border transition-all ${
            state.activeVersion === 42 && state.transcript.some(m => m.version === 42 && m.role === 'assistant' && !m.isInterrupted)
              ? 'bg-emerald-950/50 border-emerald-500 text-emerald-300'
              : 'bg-slate-900/60 border-slate-800 text-slate-500'
          }`}>
            <div className="font-bold">6. COMPLETE #42</div>
            <div className="text-[10px]">Rime Audio Played</div>
          </div>
        </div>
      </div>

      {/* Preset Action Buttons & Custom Prompt Bar */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Preset Buttons */}
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => simulationEngine.runAcceptanceTest1()}
            className="px-3 py-1.5 rounded-lg text-xs font-mono font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-all flex items-center gap-1.5"
          >
            <Play className="w-3.5 h-3.5 text-cyan-400" />
            Acceptance Test 1 (Normal)
          </button>

          <button
            onClick={() => simulationEngine.runAcceptanceTest2()}
            className="px-3 py-1.5 rounded-lg text-xs font-mono font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-all flex items-center gap-1.5"
          >
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
            Acceptance Test 2 (Speech Cut)
          </button>
        </div>

        {/* Custom Prompt Box */}
        <form onSubmit={handleSend} className="flex items-center gap-2">
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="Simulate user voice input (e.g. 'Trains from Nagpur to Mumbai')"
            className="flex-1 bg-slate-950/80 border border-slate-800 rounded-lg px-3 py-1.5 text-xs font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500/60 transition-all"
          />
          <button
            type="submit"
            className="px-3 py-1.5 rounded-lg text-xs font-mono font-bold bg-cyan-600 hover:bg-cyan-500 text-white transition-all flex items-center gap-1 shadow-sm"
          >
            <Send className="w-3.5 h-3.5" />
            SEND
          </button>
        </form>
      </div>
    </div>
  );
};

