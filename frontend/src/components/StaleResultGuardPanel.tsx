import React from 'react';
import { StaleDiscardRecord } from '../types';
import { ShieldAlert, ShieldCheck, XCircle, ArrowRight, Check } from 'lucide-react';

interface StaleResultGuardPanelProps {
  staleDiscards: StaleDiscardRecord[];
  activeVersion: number;
}

export const StaleResultGuardPanel: React.FC<StaleResultGuardPanelProps> = ({
  staleDiscards,
}) => {
  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded-lg ${staleDiscards.length > 0 ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20' : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'}`}>
            {staleDiscards.length > 0 ? <ShieldAlert className="w-4 h-4 text-rose-400" /> : <ShieldCheck className="w-4 h-4 text-emerald-400" />}
          </div>
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono">
              9. Stale-Result Protection Guard
            </h2>
            <p className="text-xs text-slate-400 font-mono">
              Gate Invariant: <code className="text-cyan-300">result.version === active_version</code>
            </p>
          </div>
        </div>

        <span className={`px-2 py-0.5 rounded text-xs font-mono font-bold border ${
          staleDiscards.length > 0
            ? 'bg-rose-950/60 text-rose-300 border-rose-700'
            : 'bg-emerald-950/40 text-emerald-300 border-emerald-800'
        }`}>
          {staleDiscards.length} STALE PAYLOADS BLOCKED
        </span>
      </div>

      {staleDiscards.length === 0 ? (
        <div className="bg-slate-950/60 border border-slate-800/60 rounded-lg p-4 text-center text-xs font-mono text-slate-500 flex items-center justify-center gap-2">
          <ShieldCheck className="w-4 h-4 text-emerald-500/60" />
          No obsolete or stale results detected. State machine is synchronized.
        </div>
      ) : (
        <div className="space-y-2.5">
          {staleDiscards.map((record) => (
            <div 
              key={record.id}
              className="p-3 rounded-lg bg-rose-950/20 border border-rose-600/40 text-xs font-mono shadow-sm"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-rose-400 flex items-center gap-1">
                    <XCircle className="w-3.5 h-3.5 text-rose-400" />
                    STALE RESULT DISCARDED
                  </span>
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800">
                    Payload: v{record.resultVersion}
                  </span>
                  <ArrowRight className="w-3 h-3 text-slate-500" />
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-cyan-950 text-cyan-300 border border-cyan-800">
                    Active Session: v{record.activeVersionWhenDelivered}
                  </span>
                </div>

                <span className="text-[10px] text-slate-500">
                  {new Date(record.timestamp).toISOString().substring(14, 21)}
                </span>
              </div>

              {/* Reason */}
              <div className="mt-2 text-slate-300 bg-slate-950/80 p-2 rounded border border-slate-800">
                <div className="text-rose-300 font-semibold mb-1">
                  Reason: {record.reason}
                </div>
                <div className="text-[11px] text-slate-400">
                  Tool: <code className="text-amber-300">{record.toolName}({JSON.stringify(record.args)})</code>
                </div>
                <div className="text-[10px] text-slate-500 mt-1">
                  Blocked Payload: {JSON.stringify(record.result).substring(0, 80)}...
                </div>
              </div>

              <div className="mt-1.5 flex items-center justify-between text-[10px] text-emerald-400">
                <span className="flex items-center gap-1">
                  <Check className="w-3 h-3" /> Conversation state untouched
                </span>
                <span className="flex items-center gap-1">
                  <Check className="w-3 h-3" /> Dropped before LLM & Rime TTS
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

