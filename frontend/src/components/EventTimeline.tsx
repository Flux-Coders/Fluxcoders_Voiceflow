import React, { useState } from 'react';
import { TimelineEvent } from '../types';
import { ChevronDown, ChevronRight, Terminal } from 'lucide-react';

interface EventTimelineProps {
  events: TimelineEvent[];
}

export const EventTimeline: React.FC<EventTimelineProps> = ({ events }) => {
  const [filterLevel, setFilterLevel] = useState<string>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const filteredEvents = events.filter(e => {
    if (filterLevel === 'all') return true;
    if (filterLevel === 'critical') return e.level === 'critical' || e.level === 'error';
    if (filterLevel === 'warn') return e.level === 'warn' || e.level === 'critical' || e.level === 'error';
    return e.level === filterLevel;
  });

  const getLevelBadge = (level: TimelineEvent['level']) => {
    switch (level) {
      case 'critical':
      case 'error':
        return 'bg-rose-500/20 text-rose-300 border-rose-500/40';
      case 'warn':
        return 'bg-amber-500/20 text-amber-300 border-amber-500/40';
      case 'success':
        return 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40';
      default:
        return 'bg-slate-800 text-slate-300 border-slate-700';
    }
  };

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm flex flex-col h-[520px]">
      <div className="flex items-center justify-between mb-3 shrink-0">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono flex items-center gap-1.5">
          <Terminal className="w-3.5 h-3.5 text-cyan-400" />
          7. Realtime Event Timeline
        </h2>
        
        {/* Filter buttons */}
        <div className="flex items-center gap-1 text-[11px] font-mono">
          {['all', 'critical', 'warn', 'info'].map((lvl) => (
            <button
              key={lvl}
              onClick={() => setFilterLevel(lvl)}
              className={`px-2 py-0.5 rounded uppercase font-semibold transition-all ${
                filterLevel === lvl
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {lvl}
            </button>
          ))}
        </div>
      </div>

      {/* Events list */}
      <div className="flex-1 overflow-y-auto space-y-2 pr-1 font-mono text-xs">
        {filteredEvents.length === 0 ? (
          <div className="text-center text-slate-500 py-8 text-xs">
            No events recorded matching filter.
          </div>
        ) : (
          filteredEvents.map((evt) => {
            const isExpanded = expandedId === evt.id;

            return (
              <div
                key={evt.id}
                className="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80 hover:border-slate-700 transition-all cursor-pointer"
                onClick={() => setExpandedId(isExpanded ? null : evt.id)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[10px] text-slate-500">
                      {evt.formattedTime}
                    </span>
                    <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold border ${getLevelBadge(evt.level)}`}>
                      {evt.type}
                    </span>
                    <span className="px-1 py-0.2 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                      v{evt.version}
                    </span>
                  </div>

                  {evt.details && (
                    <span className="text-slate-500">
                      {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                    </span>
                  )}
                </div>

                <div className="mt-1.5 text-slate-200 text-xs font-sans">
                  {evt.message}
                </div>

                {/* Expanded Details JSON Payload */}
                {isExpanded && evt.details && (
                  <div className="mt-2 p-2 rounded bg-slate-900 border border-slate-800 text-[11px] text-slate-300 overflow-x-auto">
                    <pre className="text-slate-400">
                      {JSON.stringify(evt.details, null, 2)}
                    </pre>
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

