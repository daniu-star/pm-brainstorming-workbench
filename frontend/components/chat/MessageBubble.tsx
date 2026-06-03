"use client";

import { memo } from "react";
import type { Message } from "@/lib/types";
import { ROLE_MAP } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import { getRoleAvatar } from "@/components/icons";

interface Props {
  message: Message;
  isStreaming?: boolean;
  isGrouped?: boolean;
  groupPosition?: "first" | "middle" | "last" | "single";
}

export const MessageBubble = memo(function MessageBubble({
  message,
  isStreaming,
  isGrouped = false,
  groupPosition = "single",
}: Props) {
  const isUser = message.role === "user";
  const roleName = message.role_name;
  const roleInfo = roleName ? ROLE_MAP[roleName] : null;
  const roleColor = roleInfo?.color || "#8b6f47";
  const isInterviewer = roleName === "interviewer" || roleName === "AI面试官";

  const showMeta =
    !isGrouped || groupPosition === "first" || groupPosition === "single";

  const mb = isGrouped
    ? groupPosition === "last" || groupPosition === "single"
      ? "mb-4"
      : "mb-1"
    : "mb-4";

  if (isUser) {
    return (
      <div className={`msg-enter flex justify-end ${mb}`}>
        <div className="max-w-[70%]">
          <div
            className={`relative border border-amber-200/60 px-4 py-2.5 shadow-sm bubble-user-bg ${
              showMeta
                ? "rounded-2xl rounded-br-[4px]"
                : "rounded-xl"
            } ${isStreaming ? "streaming-cursor" : ""}`}
          >
            <div className="absolute right-0 top-2 bottom-2 w-[3px] rounded-full bg-amber-500" />
            <p className="text-warm-600 text-sm leading-relaxed whitespace-pre-wrap">
              {message.content}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isInterviewer) {
    return (
      <div className={`msg-enter flex gap-3 ${mb}`}>
        {showMeta ? (
          <div className="avatar-pulse-ring shrink-0 mt-0.5">
            <div
              className="w-9 h-9 rounded-full overflow-hidden relative z-10 interviewer-avatar-ring"
            >
              <img
                src={getRoleAvatar(roleName)}
                alt="AI 面试官"
                className="w-full h-full object-cover"
              />
            </div>
          </div>
        ) : (
          <div className="w-9 shrink-0" />
        )}
        <div className="flex-1 min-w-0 max-w-[80%]">
          <div
            className={`relative border border-red-200/60 px-4 py-2.5 shadow-sm transition-shadow duration-200 interviewer-bubble-bg ${
              showMeta
                ? "rounded-2xl rounded-tl-[4px]"
                : "rounded-xl"
            } ${isStreaming ? "streaming-cursor" : ""}`}
          >
            <div className="absolute left-0 top-2 bottom-2 w-[4px] rounded-full bg-red-500" />
            {showMeta && (
              <div className="absolute top-1.5 right-3 z-10">
                <span className="rounded-full bg-red-500 px-2 py-0.5 text-[11px] font-semibold text-white">
                  AI 面试官
                </span>
              </div>
            )}
            <div className={`pl-3 ${showMeta ? "pr-16" : ""}`}>
              <div className="prose prose-sm max-w-none text-warm-600 leading-relaxed">
                {isStreaming ? (
                  <p className="whitespace-pre-wrap">{message.content}</p>
                ) : (
                  <ReactMarkdown>{message.content}</ReactMarkdown>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`msg-enter flex gap-3 ${mb}`}>
      {showMeta ? (
        <div className="shrink-0 mt-0.5">
          <div
            className="w-9 h-9 rounded-full overflow-hidden"
            style={{
              border: `1.5px solid ${roleColor}`,
              boxShadow: `0 0 8px ${roleColor}33`,
            }}
          >
            {roleName ? (
              <img
                src={getRoleAvatar(roleName)}
                alt={roleInfo?.name || roleName}
                className="w-full h-full object-cover"
              />
            ) : (
              <span className="text-warm-500">?</span>
            )}
          </div>
        </div>
      ) : (
        <div className="w-9 shrink-0" />
      )}
      <div className="flex-1 min-w-0 max-w-[80%]">
        <div
          className={`relative border px-4 py-2.5 shadow-sm transition-shadow duration-200 ${
            showMeta
              ? "rounded-2xl rounded-tl-[4px]"
              : "rounded-xl"
          } ${isStreaming ? "streaming-cursor" : ""}`}
          style={{
            background: `linear-gradient(135deg, ${roleColor}14, ${roleColor}05)`,
            borderColor: `${roleColor}20`,
          }}
        >
          <div
            className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full"
            style={{ backgroundColor: roleColor }}
          />
          {showMeta && (
            <span
              className="absolute top-1.5 right-3 z-10 rounded-full px-2 py-0.5 text-[11px] font-semibold text-white"
              style={{ backgroundColor: roleColor }}
            >
              {roleInfo?.name || roleName || "未知角色"}
            </span>
          )}
          <div className={`pl-2 ${showMeta ? "pr-16" : ""}`}>
            <div className="prose prose-sm max-w-none text-warm-600 leading-relaxed">
              {isStreaming ? (
                <p className="whitespace-pre-wrap">{message.content}</p>
              ) : (
                <ReactMarkdown>{message.content}</ReactMarkdown>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});
