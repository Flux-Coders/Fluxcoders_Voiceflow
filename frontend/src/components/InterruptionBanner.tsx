import React from 'react';
import { Zap, ArrowRight } from 'lucide-react';

interface InterruptionBannerProps {
  banner: {
    visible: boolean;
    invalidatedRequestId: string;
    invalidatedVersion: number;
    audioStopDurationMs: number;
    timestamp: number;
  } | null;
  activeVersion: number;
}

export const InterruptionBanner: React.FC<InterruptionBannerProps> = ({ banner, activeVersion }) => {
  if (!banner || !banner.visible) return null;

  return (
    <div className="bg-gradient-to-r from-rose-950/90 via-rose-900/60 to-slate-900/90 border border-rose-500/70 rounded-xl p-4 shadow-lg shadow-rose-950/50 animate-glow-rose mb-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-rose-600/30 text-rose-300 border border-rose-500/50 flex items-center justify-center shrink-0 animate-bounce">
            <Zap className="w-5 h-5 fill-rose-400 text-rose-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-rose-300 font-mono flex items-center gap-1">
                8. USER INTERRUPTION DETECTED • FAST-PATH ACTIVE
              </span>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-500 text-white shadow-sm">
                AUDIO CUT: {banner.audioStopDurationMs}ms
              </span>
            </div>
            <div className="text-sm font-mono text-white mt-0.5 flex items-center gap-2 flex-wrap">
              <span className="text-rose-200 line-through">
                Request #{banner.invalidatedVersion} ({banner.invalidatedRequestId})
              </span>
              <ArrowRight className="w-3.5 h-3.5 text-rose-400 shrink-0" />
              <span className="text-emerald-300 font-bold">
                Transitioned to Request #{activeVersion} (Active)
              </span>
            </div>
          </div>
        </div>

        <div className="text-right font-mono text-xs text-rose-300 shrink-0 bg-rose-950/70 px-3 py-1.5 rounded-lg border border-rose-800/60">
          <div className="font-bold text-emerald-400 flex items-center gap-1 justify-end">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
            Audio Suppressed Promptly
          </div>
          <div className="text-[10px] text-slate-400 mt-0.5">
            Rime Stream Terminated
          </div>
        </div>
      </div>
    </div>
  );
};

