import React from 'react';
import { EngineState, simulationEngine } from '../engine/simulationEngine';
import { 
  Radio, 
  Sparkles, 
  RotateCcw, 
  Zap, 
  AlertTriangle 
} from 'lucide-react';

interface HeaderProps {
  state: EngineState;
}

export const Header: React.FC<HeaderProps> = ({ state }) => {
  return (
    <header className="border-b border-slate-800/80 bg-slate-950/70 backdrop-blur-md sticky top-0 z-40 px-4 lg:px-6 py-3">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-3">
        {/* Brand & Subtitle */}
        <div className="flex items-center gap-3 w-full md:w-auto">
          <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-cyan-600 to-blue-500 flex items-center justify-center shadow-lg shadow-cyan-500/20 ring-1 ring-cyan-400/30">
            <Radio className="w-5 h-5 text-white animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-100 to-slate-400 bg-clip-text text-transparent font-mono">
                VoiceFlow
              </h1>
              <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 font-mono">
                REALTIME DEV
              </span>
              <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-purple-500/10 text-purple-300 border border-purple-500/30 flex items-center gap-1 font-mono">
                <Sparkles className="w-3 h-3 text-purple-400" />
                Rime TTS (Primary)
              </span>
            </div>
            <p className="text-xs text-slate-400 font-mono">
              Interruption-Safe Realtime Voice Agent • Version-Gated State Machine
            </p>
          </div>
        </div>

        {/* Global Action Bar */}
        <div className="flex items-center gap-2.5 w-full md:w-auto justify-end flex-wrap">
          {/* Quick Scenario Preset: STRESS TEST */}
          <button
            onClick={() => simulationEngine.runStressTestScenario()}
            disabled={state.isStressTesting}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold font-mono transition-all flex items-center gap-1.5 shadow-md ${
              state.isStressTesting
                ? 'bg-rose-500/20 text-rose-300 border border-rose-500/50 cursor-wait animate-pulse'
                : 'bg-gradient-to-r from-rose-600 to-amber-600 hover:from-rose-500 hover:to-amber-500 text-white shadow-rose-900/30 hover:scale-105 active:scale-95'
            }`}
          >
            <Zap className="w-3.5 h-3.5 fill-current" />
            {state.isStressTesting ? 'RUNNING STRESS TEST...' : 'RUN STRESS TEST (#41 ➔ #42)'}
          </button>

          {/* Quick Emergency Interrupt Button */}
          <button
            onClick={() => simulationEngine.interrupt('Manual Operator Click')}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold font-mono bg-rose-950/60 hover:bg-rose-900/80 text-rose-300 border border-rose-600/40 hover:border-rose-500 transition-all flex items-center gap-1 active:scale-95"
          >
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
            INTERRUPT
          </button>

          {/* Reset Session */}
          <button
            onClick={() => simulationEngine.resetSession()}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold font-mono bg-slate-800/80 hover:bg-slate-700/80 text-slate-300 border border-slate-700 transition-all flex items-center gap-1 active:scale-95"
            title="Reset active version to 40 and clear logs"
          >
            <RotateCcw className="w-3.5 h-3.5 text-slate-400" />
            RESET
          </button>
        </div>
      </div>
    </header>
  );
};

