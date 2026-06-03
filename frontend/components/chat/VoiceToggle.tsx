"use client";

interface VoiceToggleProps {
  mode: "voice" | "text";
  onChange: (mode: "voice" | "text") => void;
}

export function VoiceToggle({ mode, onChange }: VoiceToggleProps) {
  return (
    <div className="flex items-center bg-warm-100 border border-warm-200 rounded-lg p-0.5">
      <button
        onClick={() => onChange("text")}
        className={`px-3 py-1.5 text-xs rounded-md transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center ${
          mode === "text"
            ? "bg-amber-500 text-white"
            : "text-warm-500 hover:text-warm-700"
        }`}
        aria-label="文字输入模式"
        aria-pressed={mode === "text"}
      >
        文字
      </button>
      <button
        onClick={() => onChange("voice")}
        className={`px-3 py-1.5 text-xs rounded-md transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center ${
          mode === "voice"
            ? "bg-amber-500 text-white"
            : "text-warm-500 hover:text-warm-700"
        }`}
        aria-label="语音输入模式"
        aria-pressed={mode === "voice"}
      >
        语音
      </button>
    </div>
  );
}
