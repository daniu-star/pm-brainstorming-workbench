"use client";

import { useRef, useEffect, useMemo, useState } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { MessageBubble } from "./MessageBubble";
import { ROLES, ROLE_MAP } from "@/lib/types";
import { BrainIcon, getRoleAvatar, handleAvatarError } from "@/components/icons";
import type { Message } from "@/lib/types";
import { ChevronDown } from "lucide-react";

type GroupPosition = "first" | "middle" | "last" | "single";

interface GroupedMessage {
  message: Message;
  isGrouped: boolean;
  groupPosition: GroupPosition;
}

function getIdentity(msg: Message): string {
  if (msg.role === "user") return "user";
  return msg.role_name || "assistant";
}

function computeGroups(messages: Message[]): GroupedMessage[] {
  if (messages.length === 0) return [];

  const result: GroupedMessage[] = [];

  let groupStart = 0;
  let currentIdentity = getIdentity(messages[0]);

  const flushGroup = (end: number) => {
    const len = end - groupStart + 1;
    for (let i = groupStart; i <= end; i++) {
      const posInGroup = i - groupStart;
      let groupPosition: GroupPosition;
      if (len === 1) {
        groupPosition = "single";
      } else if (posInGroup === 0) {
        groupPosition = "first";
      } else if (posInGroup === len - 1) {
        groupPosition = "last";
      } else {
        groupPosition = "middle";
      }
      result.push({
        message: messages[i],
        isGrouped: len > 1,
        groupPosition,
      });
    }
  };

  for (let i = 1; i < messages.length; i++) {
    const identity = getIdentity(messages[i]);
    if (identity !== currentIdentity) {
      flushGroup(i - 1);
      groupStart = i;
      currentIdentity = identity;
    }
  }
  flushGroup(messages.length - 1);

  return result;
}

function getSpacingClass(
  current: GroupedMessage,
  prev: GroupedMessage | null
): string {
  if (!prev) return "";

  const prevIdentity = getIdentity(prev.message);
  const curIdentity = getIdentity(current.message);

  if (prevIdentity === curIdentity) {
    return "msg-group-tight mb-1";
  }

  const prevIsUser = prev.message.role === "user";
  const curIsUser = current.message.role === "user";

  if (prevIsUser !== curIsUser) {
    return "msg-group-user-switch mb-4";
  }

  return "msg-group-switch mb-3";
}

function formatRelativeTime(timestamp?: string): string | null {
  if (!timestamp) return null;
  const now = Date.now();
  const then = new Date(timestamp).getTime();
  const diffMs = now - then;
  if (diffMs < 0) return null;

  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "刚刚";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  return `${diffDay} 天前`;
}

function shouldShowTimestamp(
  prev: GroupedMessage | null,
  current: GroupedMessage
): boolean {
  if (!prev) return false;
  const prevIsUser = prev.message.role === "user";
  const curIsUser = current.message.role === "user";
  return prevIsUser !== curIsUser;
}

export function MessageList() {
  const messages = useSessionStore((s) => s.messages);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const streamingContent = useSessionStore((s) => s.streamingContent);
  const streamingRole = useSessionStore((s) => s.streamingRole);
  const bottomRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [isNearBottom, setIsNearBottom] = useState(true);

  useEffect(() => {
    const scroller = listRef.current?.parentElement;
    if (!scroller) return;
    const updatePosition = () => {
      const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      setIsNearBottom(distance <= 120);
    };
    updatePosition();
    scroller.addEventListener("scroll", updatePosition, { passive: true });
    return () => scroller.removeEventListener("scroll", updatePosition);
  }, [messages.length]);

  useEffect(() => {
    if (!isNearBottom) return;
    const raf = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    });
    return () => cancelAnimationFrame(raf);
  }, [messages, streamingContent, isNearBottom]);

  const grouped = useMemo(() => computeGroups(messages), [messages]);

  const streamingRoleInfo = streamingRole ? ROLE_MAP[streamingRole] : null;
  const streamingDotColor = streamingRoleInfo?.color || "#8b6f47";

  if (messages.length === 0 && !isStreaming) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center text-warm-500">
          <div className="mb-4 flex justify-center">
            <BrainIcon size={56} className="text-warm-500" />
          </div>
          <p className="text-lg mb-2 font-medium text-warm-600">准备开始脑暴</p>
          <p className="text-sm mb-4">四位专家即将围绕你的产品方向展开讨论</p>
          <div className="flex justify-center gap-3">
            {ROLES.map((r) => (
              <div
                key={r.id}
                className="flex flex-col items-center gap-1"
              >
                <div
                  className="w-10 h-10 rounded-full overflow-hidden transition-transform duration-200 hover:scale-110"
                  style={{
                    border: `1.5px solid ${r.color}30`,
                    boxShadow: `0 0 12px ${r.color}20`,
                  }}
                >
                  <img src={`/avatars/${r.id}.svg`} onError={handleAvatarError} alt={r.name} className="w-full h-full object-cover" />
                </div>
                <span className="text-[10px] text-warm-500">{r.name}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={listRef} className="relative space-y-1 px-1">
      {messages.length > 0 && (
        <div className="text-center mb-4 pt-2">
          <span className="text-xs text-warm-500 bg-warm-100 px-3 py-1 rounded-full border border-warm-200">
            讨论开始
          </span>
        </div>
      )}

      {grouped.map((gm, idx) => {
        const prev = idx > 0 ? grouped[idx - 1] : null;
        const spacing = getSpacingClass(gm, prev);
        const showTs = shouldShowTimestamp(prev, gm);
        const tsText = showTs ? formatRelativeTime(gm.message.timestamp) : null;

        return (
          <div key={gm.message.id || gm.message.timestamp || idx}>
            {showTs && tsText && (
              <div className="text-[10px] text-warm-500 text-center mb-1">
                {tsText}
              </div>
            )}
            <div className={spacing}>
              <MessageBubble
                message={gm.message}
                isGrouped={gm.isGrouped}
                groupPosition={gm.groupPosition}
              />
            </div>
          </div>
        );
      })}

      {isStreaming && streamingContent && (
        <MessageBubble
          message={{
            role: "assistant",
            content: streamingContent,
            role_name: streamingRole || undefined,
          }}
          isStreaming
        />
      )}

      {isStreaming && !streamingContent && (
        <div className="flex items-center gap-3 py-2 px-4">
          <div className="shrink-0">
            <div
              className="w-9 h-9 rounded-full overflow-hidden"
              style={{
                border: `1.5px solid ${streamingDotColor}`,
                boxShadow: `0 0 8px ${streamingDotColor}33`,
              }}
            >
              <img
                src={getRoleAvatar(streamingRole || undefined)}
                alt={streamingRoleInfo?.name || streamingRole || ""}
                className="w-full h-full object-cover"
              />
            </div>
          </div>
          <div className="flex flex-col gap-0.5">
            {streamingRole && (
              <span
                className="text-[10px] font-semibold"
                style={{ color: streamingDotColor }}
              >
                {streamingRoleInfo?.name || streamingRole}
              </span>
            )}
            <div className="flex items-center gap-1.5">
              <span className="flex gap-1">
                <span
                  className="w-1.5 h-1.5 rounded-full typing-dot"
                  style={{ backgroundColor: streamingDotColor, opacity: 0.6, animationDelay: "0ms" }}
                />
                <span
                  className="w-1.5 h-1.5 rounded-full typing-dot"
                  style={{ backgroundColor: streamingDotColor, opacity: 0.6, animationDelay: "150ms" }}
                />
                <span
                  className="w-1.5 h-1.5 rounded-full typing-dot"
                  style={{ backgroundColor: streamingDotColor, opacity: 0.6, animationDelay: "300ms" }}
                />
              </span>
              <span className="text-warm-500 text-sm">
                {streamingRole ? `${streamingRoleInfo?.name || streamingRole} 正在思考...` : "思考中..."}
              </span>
            </div>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
      {!isNearBottom && (
        <button
          type="button"
          onClick={() => {
            bottomRef.current?.scrollIntoView({ behavior: "smooth" });
            setIsNearBottom(true);
          }}
          className="sticky bottom-2 left-1/2 z-20 mx-auto flex min-h-9 -translate-x-1/2 items-center gap-1.5 rounded-full border border-cyan-200/20 bg-slate-950/90 px-3 py-1.5 text-xs text-cyan-100 shadow-lg backdrop-blur-xl"
        >
          <ChevronDown className="h-3.5 w-3.5" />
          回到最新
        </button>
      )}
    </div>
  );
}
