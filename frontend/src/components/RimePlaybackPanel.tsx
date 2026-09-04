import React from 'react';
import { RimePlaybackState } from '../types';
import { Volume2, VolumeX, Sparkles, Layers, AlertOctagon, CheckCircle2 } from 'lucide-react';

interface RimePlaybackPanelProps {
  rimeState: RimePlaybackState;
  activeVersion: number;
}

export const RimePlaybackPanel: React.FC<RimePlaybackPanelProps> = ({
  rimeState,
}) => {
  const isPlaying = rimeState.status === 'playing';
  const isAborted = rimeState.status === 'aborted_on_interrupt';

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono">
              6. Primary TTS: Rime Engine
            </h2>
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-semibold font-mono text-purple-300">
                Model: {rimeState.model}
              </span>
              <span className="text-xs text-slate-400 font-mono">
                • Speaker: {rimeState.currentSpeaker}
              </span>
            </div>
          </div>
        </div>

        {/* State Badge */}
        <div>
          {isPlaying && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono font-bold bg-purple-500/20 text-purple-300 border border-purple-500/40 animate-pulse">
              <Volume2 className="w-3.5 h-3.5" /> STREAMING AUDIO (v{rimeState.activeVersion})
            </span>
          )}
          {isAborted && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono font-bold bg-rose-500/20 text-rose-300 border border-rose-500/40">
              <VolumeX className="w-3.5 h-3.5" /> ABORTED ON INTERRUPT
            </span>
          )}
          {rimeState.status === 'drained' && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono font-medium bg-slate-800 text-slate-300 border border-slate-700">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> DRAINED / IDLE
            </span>
          )}
          {rimeState.status === 'idle' && (
            <span className="px-2.5 py-1 rounded-full text-xs font-mono text-slate-400 bg-slate-950 border border-slate-800">
              IDLE
            </span>
          )}
        </div>
      </div>

      {/* Visual Audio Output Equalizer when playing */}
      <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-mono text-slate-400 flex items-center gap-1">
            <Layers className="w-3.5 h-3.5 text-purple-400" />
            Rime Audio Output Buffer ({rimeState.bufferedChunks.length} chunks queued)
          </span>
          <span className="text-xs font-mono text-purple-400">
            {isPlaying ? `${(rimeState.audioLevel * 100).toFixed(0)}% Gain Output` : 'Track Muted / Ready'}
          </span>
        </div>

        {/* Chunk visualization */}
        {rimeState.bufferedChunks.length > 0 ? (
          <div className="grid grid-cols-3 gap-2">
            {rimeState.bufferedChunks.map((chunk, idx) => (
              <div 
                key={idx}
                className={`p-2 rounded border text-xs font-mono transition-all ${
                  isPlaying 
                    ? 'bg-purple-950/40 border-purple-500/40 text-purple-200 shadow-sm shadow-purple-500/10'
                    : isAborted
                    ? 'bg-rose-950/20 border-rose-800/40 text-rose-300 line-through opacity-60'
                    : 'bg-slate-900 border-slate-800 text-slate-400'
                }`}
              >
                <div className="flex items-center justify-between font-bold text-[10px]">
                  <span>Chunk #{chunk.chunkIndex}</span>
                  <span>{chunk.sizeBytes} B</span>
                </div>
                <div className="mt-1 text-[11px] truncate text-slate-300">
                  "{chunk.textSnippet}"
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-2 text-center text-xs font-mono text-slate-500">
            No active audio chunks in Rime synthesis buffer.
          </div>
        )}

        {/* Abort Reason Alert if interrupted */}
        {isAborted && rimeState.abortReason && (
          <div className="mt-2.5 p-2 rounded bg-rose-950/40 border border-rose-800/60 text-xs font-mono text-rose-300 flex items-center gap-1.5">
            <AlertOctagon className="w-4 h-4 text-rose-400 shrink-0" />
            <span>Fast-Cut: {rimeState.abortReason}</span>
          </div>
        )}
      </div>
    </div>
  );
};

