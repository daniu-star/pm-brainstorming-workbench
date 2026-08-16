"use client";

import { useRef, useEffect } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { MessageBubble } from "./MessageBubble";
import { ROLES, ROLE_MAP } from "@/lib/types";
import { BrainIcon } from "@/components/icons";

const NEAR_BOTTOM_PX = 80;

function roleDisplayName(role: string | null): string | undefined {
  if (!role) return undefined;
  return ROLE_MAP[role]?.name ?? role;
}

export function MessageList() {
  const { messages, isStreaming, streamingContent, streamingRole } = useSessionStore();
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  };

  // Only auto-scroll when the user is already at (or near) the bottom
  useEffect(() => {
    if (isAtBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, streamingContent]);

  if (messages.length === 0 && !isStreaming) {
    return (
      <div className="flex-1 overflow-y-auto flex items-center justify-center">
        <div className="text-center text-zinc-500">
          <div className="mb-4 flex justify-center">
            <BrainIcon size={56} className="text-zinc-500" />
          </div>
          <p className="text-lg mb-2 font-medium text-zinc-400">准备开始脑暴</p>
          <p className="text-sm mb-4">四位专家即将围绕你的产品方向展开讨论</p>
          <div className="flex justify-center gap-3">
            {ROLES.map((r) => (
              <div
                key={r.id}
                className="flex flex-col items-center gap-1"
              >
                <div
                  className="w-10 h-10 rounded-full overflow-hidden"
                  style={{
                    border: `1.5px solid ${r.color}30`,
                  }}
                >
                  <img src={`/avatars/${r.id}.svg`} alt={r.name} loading="lazy" className="w-full h-full object-cover" />
                </div>
                <span className="text-[10px] text-zinc-500">{r.name}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      aria-live="polite"
      aria-busy={isStreaming}
      className="flex-1 overflow-y-auto px-2 py-3"
    >
      <div className="space-y-1 px-1">
        {/* Group chat header when messages exist */}
        {messages.length > 0 && (
          <div className="text-center mb-4 pt-2">
            <span className="text-xs text-zinc-500 bg-dark-800/50 px-3 py-1 rounded-full border border-zinc-700/30">
              讨论开始
            </span>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={`${i}-${msg.role}-${msg.content.slice(0, 12)}`} message={msg} />
        ))}

        {isStreaming && streamingContent && (
          <MessageBubble
            message={{
              role: "assistant",
              content: streamingContent,
              role_name: roleDisplayName(streamingRole),
            }}
            isStreaming
          />
        )}

        {isStreaming && !streamingContent && (
          <div className="flex items-center gap-2 text-zinc-500 text-sm py-2 px-4">
            <span className="flex gap-1">
              <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full typing-dot" style={{ animationDelay: "0ms" }} />
              <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full typing-dot" style={{ animationDelay: "150ms" }} />
              <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full typing-dot" style={{ animationDelay: "300ms" }} />
            </span>
            <span>{streamingRole ? `${roleDisplayName(streamingRole)} 正在输入...` : "思考中..."}</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
