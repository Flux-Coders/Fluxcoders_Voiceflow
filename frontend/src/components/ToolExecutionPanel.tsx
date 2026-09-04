import React from 'react';
import { ToolExecution } from '../types';
import { Wrench, PlayCircle, CheckCircle, XCircle, Sliders, AlertTriangle } from 'lucide-react';
import { simulationEngine } from '../engine/simulationEngine';

interface ToolExecutionPanelProps {
  runningTools: ToolExecution[];
  mockToolDelayMs: number;
  activeVersion: number;
}

export const ToolExecutionPanel: React.FC<ToolExecutionPanelProps> = ({
  runningTools,
  mockToolDelayMs,
  activeVersion,
}) => {
  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono flex items-center gap-1.5">
          <Wrench className="w-3.5 h-3.5 text-amber-400" />
          5. Tool Execution Status
        </h2>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono text-slate-400 flex items-center gap-1">
            <Sliders className="w-3 h-3 text-cyan-400" /> Mock Delay: {mockToolDelayMs}ms
          </span>
          <input
            type="range"
            min="500"
            max="5000"
            step="250"
            value={mockToolDelayMs}
            onChange={(e) => simulationEngine.setMockToolDelay(Number(e.target.value))}
            className="w-20 accent-cyan-400 cursor-pointer h-1.5 bg-slate-800 rounded-lg"
          />
        </div>
      </div>

      {/* Tool Execution List */}
      <div className="space-y-2.5">
        {runningTools.length === 0 ? (
          <div className="bg-slate-950/60 border border-slate-800/60 rounded-lg p-4 text-center text-xs font-mono text-slate-500">
            No active or recent tool executions. All workers idle.
          </div>
        ) : (
          runningTools.map((tool, idx) => {
            return (
              <div 
                key={`${tool.toolCallId}-${idx}`}
                className={`p-3 rounded-lg border transition-all ${
                  tool.status === 'executing'
                    ? 'bg-amber-950/20 border-amber-500/40 shadow-sm shadow-amber-500/10'
                    : tool.status === 'completed_stale_discarded'
                    ? 'bg-rose-950/30 border-rose-600/50'
                    : tool.status === 'cancelled'
                    ? 'bg-slate-950/60 border-slate-700/50 opacity-70'
                    : 'bg-slate-950/70 border-emerald-500/40'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-bold text-amber-300">
                        {tool.toolName}()
                      </span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
                        tool.version === activeVersion 
                          ? 'bg-cyan-950 text-cyan-300 border border-cyan-800' 
                          : 'bg-rose-950 text-rose-300 border border-rose-800'
                      }`}>
                        v{tool.version} {tool.version !== activeVersion && '(STALE)'}
                      </span>
                      <span className="text-[10px] font-mono text-slate-400">
                        [{tool.requestId}]
                      </span>
                    </div>

                    {/* Arguments display */}
                    <div className="mt-1.5 font-mono text-xs text-slate-300 bg-slate-900/90 rounded px-2 py-1 border border-slate-800">
                      <code>{JSON.stringify(tool.args)}</code>
                    </div>
                  </div>

                  {/* Status Badges */}
                  <div>
                    {tool.status === 'executing' && (
                      <span className="flex items-center gap-1 text-xs font-mono font-bold text-amber-400 bg-amber-950/40 px-2 py-0.5 rounded border border-amber-700/50 animate-pulse">
                        <PlayCircle className="w-3.5 h-3.5" /> EXECUTING ({tool.durationMs}ms)
                      </span>
                    )}
                    {tool.status === 'completed_valid' && (
                      <span className="flex items-center gap-1 text-xs font-mono font-bold text-emerald-400 bg-emerald-950/40 px-2 py-0.5 rounded border border-emerald-700/50">
                        <CheckCircle className="w-3.5 h-3.5" /> VALID RETURN (v{tool.version})
                      </span>
                    )}
                    {tool.status === 'completed_stale_discarded' && (
                      <span className="flex items-center gap-1 text-xs font-mono font-bold text-rose-400 bg-rose-950/60 px-2 py-0.5 rounded border border-rose-700">
                        <XCircle className="w-3.5 h-3.5" /> DISCARDED (STALE v{tool.version})
                      </span>
                    )}
                    {tool.status === 'cancelled' && (
                      <span className="flex items-center gap-1 text-xs font-mono text-slate-400 bg-slate-800 px-2 py-0.5 rounded border border-slate-700">
                        <AlertTriangle className="w-3.5 h-3.5 text-slate-400" /> CANCELLED
                      </span>
                    )}
                  </div>
                </div>

                {/* Discard Warning or Results */}
                {tool.discardReason && (
                  <div className="mt-2 text-[11px] font-mono text-rose-400 flex items-center gap-1 bg-rose-950/40 p-1.5 rounded border border-rose-900/60">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                    <span>{tool.discardReason}</span>
                  </div>
                )}
                {tool.result && tool.status === 'completed_valid' && (
                  <div className="mt-2 text-[11px] font-mono text-emerald-300 bg-emerald-950/20 p-1.5 rounded border border-emerald-900/40">
                    <span>Result delivered to LLM: {Array.isArray(tool.result) ? `${tool.result.length} items returned` : 'OK'}</span>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

