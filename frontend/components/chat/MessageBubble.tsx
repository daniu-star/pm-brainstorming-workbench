"use client";

import type { Message } from "@/lib/types";
import { ROLE_MAP } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import { getRoleAvatar } from "@/components/icons";

interface Props {
  message: Message;
  isStreaming?: boolean;
}

export function MessageBubble({ message, isStreaming }: Props) {
  const isUser = message.role === "user";
  const roleName = message.role_name;
  const roleInfo = roleName ? ROLE_MAP[roleName] : null;

  if (isUser) {
    // User message - right aligned, green-ish (like WeChat)
    return (
      <div className="msg-enter flex justify-end mb-4">
        <div className="max-w-[75%]">
          <div className="bg-brand-600/25 border border-brand-500/20 rounded-2xl rounded-br-md px-4 py-2.5">
            <p className="text-zinc-200 text-sm leading-relaxed whitespace-pre-wrap">
              {message.content}
            </p>
          </div>
        </div>
      </div>
    );
  }

  // Interviewer special styling
  if (roleName === "interviewer" || roleName === "AI面试官") {
    return (
      <div className="msg-enter flex gap-3 mb-4">
        <div className="w-9 h-9 rounded-full border border-red-500/30 flex items-center justify-center shrink-0 mt-0.5 overflow-hidden">
          <img src={getRoleAvatar(roleName)} alt="AI 面试官" loading="lazy" className="w-full h-full object-cover" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-semibold text-red-400">AI 面试官</span>
            <span className="text-[10px] text-zinc-500">压力测试中</span>
          </div>
          <div className={`bg-red-500/5 border border-red-500/15 rounded-2xl rounded-tl-sm px-4 py-2.5 ${isStreaming ? "streaming-cursor" : ""}`}>
            <div className="prose prose-invert prose-sm max-w-none text-zinc-300">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Agent role message - group chat style
  return (
    <div className="msg-enter flex gap-3 mb-4">
      {/* Avatar */}
      <div
        className="w-9 h-9 rounded-full flex items-center justify-center shrink-0 mt-0.5 overflow-hidden"
        style={{
          border: `1.5px solid ${roleInfo?.color || "#52525b"}40`,
        }}
      >
        {roleName ? (
          <img src={getRoleAvatar(roleName)} alt={roleInfo?.name || roleName} loading="lazy" className="w-full h-full object-cover" />
        ) : (
          <span className="text-zinc-500">?</span>
        )}
      </div>

      {/* Message body */}
      <div className="flex-1 min-w-0">
        {/* Role name */}
        <div className="flex items-center gap-2 mb-1">
          <span
            className="text-xs font-semibold"
            style={{ color: roleInfo?.color || "#a1a1aa" }}
          >
            {roleInfo?.name || roleName || "未知角色"}
          </span>
        </div>

        {/* Bubble */}
        <div className={`bg-dark-800/60 border border-zinc-700/40 rounded-2xl rounded-tl-sm px-4 py-2.5 ${isStreaming ? "streaming-cursor" : ""}`}>
          <div className="prose prose-invert prose-sm max-w-none text-zinc-300 leading-relaxed">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
