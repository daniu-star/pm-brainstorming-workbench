"use client";

import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { VoiceToggle } from "@/components/chat/VoiceToggle";

interface InterviewHeaderProps {
  phoneMode?: boolean;
  onTogglePhoneMode?: () => void;
}

export function InterviewHeader({ phoneMode, onTogglePhoneMode }: InterviewHeaderProps) {
  const router = useRouter();
  const { sessionId, interviewMode, setInterviewMode } = useSessionStore();

  return (
    <header className="h-14 bg-white/90 backdrop-blur border-b border-red-200 flex items-center justify-between px-4 shrink-0 shadow-md">
      <div className="flex items-center gap-3">
        <button
          onClick={() => {
            if (sessionId) {
              router.push(`/session/${sessionId}`);
            } else {
              router.push("/");
            }
          }}
          className="min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg text-warm-500 hover:text-warm-700 hover:bg-warm-100 transition-all duration-200"
          aria-label="返回主会话"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
        </button>

        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full overflow-hidden border border-red-300">
            <img
              src="/avatars/interviewer-business.svg"
              alt="AI 面试官"
              className="w-full h-full object-cover"
            />
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-bold text-warm-800 leading-tight">AI 压力面试官</span>
            <span className="flex items-center gap-1 text-[11px] text-red-400 leading-tight">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
              面试进行中
            </span>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <VoiceToggle mode={interviewMode} onChange={setInterviewMode} />

        <button
          onClick={onTogglePhoneMode}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center ${
            phoneMode
              ? "bg-red-600 text-white"
              : "bg-warm-100 text-warm-600 hover:text-warm-800 hover:bg-warm-200"
          }`}
          aria-label="电话模式"
          aria-pressed={phoneMode}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
          </svg>
          <span className="hidden sm:inline">电话模式</span>
        </button>
      </div>
    </header>
  );
}
