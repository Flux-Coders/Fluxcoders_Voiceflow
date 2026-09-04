import React from 'react';
import { RequestContext } from '../types';
import { Hash, GitBranch, Clock, Flame } from 'lucide-react';

interface ActiveRequestBadgeProps {
  activeVersion: number;
  activeRequestId: string | null;
  activeContext: RequestContext | null;
  agentStatus: string;
}

export const ActiveRequestBadge: React.FC<ActiveRequestBadgeProps> = ({
  activeVersion,
  activeRequestId,
  activeContext,
  agentStatus,
}) => {
  const getStatusColor = () => {
    switch (agentStatus) {
      case 'listening':
        return 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30';
      case 'thinking':
      case 'tool_running':
        return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
      case 'speaking':
        return 'bg-purple-500/10 text-purple-400 border-purple-500/30';
      case 'interrupted':
        return 'bg-rose-500/10 text-rose-400 border-rose-500/30';
      default:
        return 'bg-slate-800 text-slate-400 border-slate-700';
    }
  };

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono flex items-center gap-1.5">
          <Hash className="w-3.5 h-3.5 text-cyan-400" />
          3 & 4. Active Request & Version
        </h2>
        <span className={`px-2 py-0.5 rounded-full text-xs font-mono font-bold uppercase border ${getStatusColor()}`}>
          {agentStatus.replace('_', ' ')}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {/* Version Card */}
        <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-3 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400 font-mono flex items-center gap-1">
              <GitBranch className="w-3.5 h-3.5 text-cyan-400" /> Conversation Version
            </span>
            <span className="text-[10px] font-mono text-cyan-400/80 bg-cyan-950/40 px-1.5 py-0.5 rounded border border-cyan-800/40">
              MONOTONIC
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-3xl font-bold font-mono text-cyan-300 tracking-tight">
              v{activeVersion}
            </span>
            <span className="text-xs text-slate-500 font-mono">
              (epoch {activeVersion})
            </span>
          </div>
          <div className="mt-1 text-[11px] text-slate-400 font-mono truncate">
            Gate lock: Strict equality check
          </div>
        </div>

        {/* Request ID Card */}
        <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-3 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400 font-mono flex items-center gap-1">
              <Hash className="w-3.5 h-3.5 text-amber-400" /> Active Request ID
            </span>
            <span className="text-[10px] font-mono text-amber-400/80 bg-amber-950/40 px-1.5 py-0.5 rounded border border-amber-800/40">
              CONTEXT UUID
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-base font-bold font-mono text-amber-300 truncate max-w-full">
              {activeRequestId || 'None (Idle)'}
            </span>
          </div>
          <div className="mt-1 text-[11px] text-slate-400 font-mono flex items-center gap-1">
            <Clock className="w-3 h-3 text-slate-500" />
            {activeContext ? `Started ${new Date(activeContext.timestamp).toISOString().substring(14, 19)}` : 'Awaiting turn'}
          </div>
        </div>
      </div>

      {/* Active Context Invariant Banner */}
      {activeContext && (
        <div className="mt-3 p-2.5 rounded-lg bg-slate-950/90 border border-slate-800 text-xs font-mono text-slate-300 flex items-start gap-2">
          <Flame className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 truncate">
            <span className="text-slate-400 font-semibold">Active Context Prompt:</span> "{activeContext.prompt}"
          </div>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${activeContext.isCancelled ? 'bg-rose-950 text-rose-300 border border-rose-800' : 'bg-emerald-950 text-emerald-300 border border-emerald-800'}`}>
            {activeContext.isCancelled ? 'CANCELLED' : 'VALID'}
          </span>
        </div>
      )}
    </div>
  );
};

