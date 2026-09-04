import React, { useRef, useEffect } from 'react';
import { TranscriptMessage } from '../types';
import { Bot, Wrench, VolumeX, CheckCircle2 } from 'lucide-react';

interface TranscriptViewProps {
  transcript: TranscriptMessage[];
  activeVersion: number;
}

export const TranscriptView: React.FC<TranscriptViewProps> = ({ transcript, activeVersion }) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcript]);

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm flex flex-col h-[520px]">
      <div className="flex items-center justify-between mb-3 shrink-0">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 font-mono flex items-center gap-1.5">
          <Bot className="w-3.5 h-3.5 text-cyan-400" />
          2. Conversation Transcript
        </h2>
        <span className="text-[11px] font-mono text-slate-500">
          {transcript.length} turns recorded
        </span>
      </div>

      {/* Transcript message stream */}
      <div className="flex-1 overflow-y-auto space-y-3.5 pr-2">
        {transcript.map((msg) => {
          const isUser = msg.role === 'user';

          return (
            <div
              key={msg.id}
              className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}
            >
              {/* Message Header */}
              <div className="flex items-center gap-1.5 mb-1 text-[10px] font-mono text-slate-400">
                <span className="font-semibold text-slate-300">
                  {isUser ? 'USER' : 'VOICEFLOW (Rime TTS)'}
                </span>
                <span>•</span>
                <span className={`px-1.5 py-0.2 rounded font-bold ${
                  msg.version === activeVersion 
                    ? 'bg-cyan-950 text-cyan-300 border border-cyan-800' 
                    : 'bg-slate-800 text-slate-400'
                }`}>
                  v{msg.version}
                </span>
                <span>•</span>
                <span className="text-slate-500">{new Date(msg.timestamp).toISOString().substring(14, 19)}</span>
              </div>

              {/* Message Bubble */}
              <div
                className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm font-sans relative ${
                  isUser
                    ? 'bg-gradient-to-r from-cyan-600 to-blue-600 text-white rounded-br-none shadow-md shadow-cyan-900/20'
                    : msg.isInterrupted
                    ? 'bg-rose-950/40 border border-rose-600/50 text-rose-200 rounded-bl-none'
                    : 'bg-slate-950/80 border border-slate-800 text-slate-100 rounded-bl-none shadow-sm'
                }`}
              >
                <div className="flex items-start gap-2">
                  <div className="flex-1 leading-relaxed">
                    {msg.text}
                  </div>
                  {msg.isInterrupted && (
                    <span className="shrink-0 flex items-center gap-1 text-[10px] font-mono bg-rose-500 text-white font-bold px-2 py-0.5 rounded-full">
                      <VolumeX className="w-3 h-3" /> CUT ON INTERRUPT
                    </span>
                  )}
                </div>

                {/* Attached Tool Result Card */}
                {msg.toolCall && (
                  <div className="mt-2.5 pt-2 border-t border-slate-800 text-xs font-mono">
                    <div className="flex items-center justify-between text-[11px] text-amber-300 font-semibold mb-1">
                      <span className="flex items-center gap-1">
                        <Wrench className="w-3 h-3 text-amber-400" />
                        Tool Invoked: {msg.toolCall.name}()
                      </span>
                      <span className="text-emerald-400 flex items-center gap-1 text-[10px]">
                        <CheckCircle2 className="w-3 h-3" /> Version Verified
                      </span>
                    </div>

                    {msg.toolCall.result && (
                      <div className="bg-slate-900/90 rounded p-2 border border-slate-800 text-[11px] text-slate-300 space-y-1">
                        {Array.isArray(msg.toolCall.result) && msg.toolCall.result.map((train: any, idx: number) => (
                          <div key={idx} className="flex items-center justify-between py-0.5 border-b border-slate-800/60 last:border-0">
                            <span className="font-semibold text-cyan-300">{train.name} ({train.trainNo})</span>
                            <span className="text-slate-400">{train.dep} ➔ {train.arr}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};

