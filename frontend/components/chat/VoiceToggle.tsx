"use client";

interface VoiceToggleProps {
  mode: "voice" | "text";
  onChange: (mode: "voice" | "text") => void;
}

export function VoiceToggle({ mode, onChange }: VoiceToggleProps) {
  return (
    <div className="flex items-center rounded-lg border border-white/10 bg-black/20 p-0.5">
      <button
        onClick={() => onChange("text")}
        className={`flex min-h-[44px] min-w-[44px] items-center rounded-md px-3 py-1.5 text-xs transition-colors duration-150 ${
          mode === "text"
            ? "bg-cyan-300 text-[#031014]"
            : "text-zinc-400 hover:text-zinc-200"
        }`}
        aria-label="文字输入模式"
        aria-pressed={mode === "text"}
      >
        文字
      </button>
      <button
        onClick={() => onChange("voice")}
        className={`flex min-h-[44px] min-w-[44px] items-center rounded-md px-3 py-1.5 text-xs transition-colors duration-150 ${
          mode === "voice"
            ? "bg-cyan-300 text-[#031014]"
            : "text-zinc-400 hover:text-zinc-200"
        }`}
        aria-label="语音输入模式"
        aria-pressed={mode === "voice"}
      >
        语音
      </button>
    </div>
  );
}
