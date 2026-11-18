"use client";

import { useEffect, useMemo, useRef } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { MessageBubble } from "./MessageBubble";
import { BrainIcon } from "@/components/icons";
import { ROLES } from "@/lib/types";
import type { Message } from "@/lib/types";
import type { MessageStage, MessageView } from "@/lib/contracts.generated";

const NEAR_BOTTOM_PX = 80;

/** v2 MessageStage 的中文标签 */
const STAGE_LABELS: Record<MessageStage, string> = {
  clarify: "澄清",
  brainstorm: "脑暴",
  audit: "审计",
  system: "系统",
};

const STAGE_COLORS: Record<MessageStage, string> = {
  clarify: "text-sky-300 border-sky-500/30 bg-sky-500/10",
  brainstorm: "text-brand-300 border-brand-500/30 bg-brand-500/10",
  audit: "text-red-300 border-red-500/30 bg-red-500/10",
  system: "text-zinc-400 border-zinc-600/40 bg-zinc-700/20",
};

/** RoleCode → 展示名（同时作为 MessageBubble 的 role_name，兼容 ROLE_MAP 中文别名） */
const ROLE_CODE_LABELS: Record<string, string> = {
  coach: "产品教练",
  cto: "技术负责人",
  designer: "设计师",
  ops: "运营负责人",
  target_user: "目标用户",
  moderator: "主持",
  interviewer: "AI 面试官",
};

function roleDisplayName(role: string | null | undefined): string {
  if (!role) return "";
  return ROLE_CODE_LABELS[role] ?? role;
}

interface DisplayItem {
  /** 稳定 key：v2 用 message_id，legacy 用序号 */
  key: string;
  message: Message;
  stage?: MessageStage;
  roleCode?: string;
  /** 流式占位（尚未完成的消息） */
  draft?: boolean;
}

function viewToMessage(view: MessageView): Message {
  const isHuman = view.sender_kind === "human";
  return {
    role: isHuman ? "user" : "assistant",
    content: view.content,
    ...(isHuman ? {} : { role_name: roleDisplayName(view.role_code) }),
  };
}

export function MessageList() {
  const {
    messages,
    messagesBySession,
    currentSessionId,
    isStreaming,
    streamingContent,
    streamingRole,
    streamingMessageId,
  } = useSessionStore();

  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);

  // v2 消息（按 session 隔离）；v2 不可用时回退旧 messages 数组
  const sessionViews = currentSessionId ? messagesBySession[currentSessionId] : undefined;
  const useV2 = Array.isArray(sessionViews) && sessionViews.length > 0;

  const items = useMemo<DisplayItem[]>(() => {
    if (useV2) {
      const seen = new Set<string>();
      const list: DisplayItem[] = [];
      for (const view of sessionViews as MessageView[]) {
        // 消息按 message_id 去重（store 已合并，这里做防御性兜底）
        if (!view || !view.id || seen.has(view.id)) continue;
        seen.add(view.id);
        if (view.sender_kind === "system") continue;
        list.push({
          key: view.id,
          message: viewToMessage(view),
          stage: view.stage,
          roleCode: view.role_code,
          draft: view.status === "draft",
        });
      }
      return list;
    }
    return messages.map((msg, index) => ({
      key: `legacy-${index}`,
      message: msg,
    }));
  }, [useV2, sessionViews, messages]);

  // 流式占位：同一 message_id 出现时替换而非追加
  const streamingItem = useMemo<DisplayItem | null>(() => {
    if (!isStreaming || !streamingContent) return null;
    if (streamingMessageId && items.some((item) => item.key === streamingMessageId)) return null;
    return {
      key: streamingMessageId ? `streaming-${streamingMessageId}` : "streaming-placeholder",
      message: {
        role: "assistant",
        content: streamingContent,
        ...(streamingRole ? { role_name: roleDisplayName(streamingRole) } : {}),
      },
      draft: true,
    };
  }, [isStreaming, streamingContent, streamingMessageId, streamingRole, items]);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  }

  useEffect(() => {
    if (isAtBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [items.length, streamingContent]);

  const rendered = streamingItem ? [...items, streamingItem] : items;
  const showTyping = isStreaming && !streamingContent;
  const isLoading = isStreaming && showTyping;

  if (rendered.length === 0 && !isLoading) {
    return (
      <div className="flex-1 flex flex-col min-h-0">
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center px-8 max-w-md">
            <div className="mb-5 flex justify-center">
              <BrainIcon size={56} className="text-zinc-600" />
            </div>
            <h3 className="text-lg font-medium text-zinc-300 mb-1.5">准备开始脑暴</h3>
            <p className="text-sm text-zinc-500 leading-relaxed">
              四位专家即将围绕你的产品方向展开讨论
            </p>
            <div className="mt-6 flex items-center justify-center gap-3">
              {ROLES.map((r) => (
                <div key={r.id} className="flex flex-col items-center gap-1.5">
                  <div
                    className="w-10 h-10 rounded-full overflow-hidden bg-dark-800"
                    style={{ border: `1.5px solid ${r.color}30` }}
                  >
                    <img
                      src={`/avatars/${r.id}.svg`}
                      alt={r.name}
                      loading="lazy"
                      className="w-full h-full object-cover"
                    />
                  </div>
                  <span className="text-[10px] text-zinc-500">{r.name}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-2 py-3"
      aria-live="polite"
      aria-busy={isStreaming}
    >
      <div className="space-y-1 px-1">
        <div className="flex justify-center mb-3">
          <span className="text-xs text-zinc-500 bg-dark-800/50 px-3 py-1 rounded-full border border-zinc-700/30">
            讨论开始
          </span>
        </div>

        {rendered.map((item, index) => {
          const showStage =
            item.stage && item.stage !== rendered[index - 1]?.stage;
          const isCopyDraft = item.draft === true;
          return (
            <div key={item.key} className="msg-enter">
              {showStage && item.stage ? (
                <div className="flex px-1 mb-1.5 mt-2">
                  <span
                    className={`text-[10px] leading-none px-2 py-1 rounded-full border ${
                      STAGE_COLORS[item.stage] ?? "text-zinc-400 border-zinc-600/40"
                    }`}
                  >
                    {STAGE_LABELS[item.stage] ?? item.stage}
                  </span>
                </div>
              ) : null}
              <MessageBubble message={item.message} isStreaming={isCopyDraft} />
            </div>
          );
        })}

        {isLoading ? (
          <div className="flex gap-3 mb-4 px-1">
            <div className="w-9 h-9 rounded-full border border-zinc-700/50 bg-dark-800 flex items-center justify-center shrink-0 mt-0.5">
              <span className="text-[10px] text-zinc-500">…</span>
            </div>
            <div className="flex items-center gap-2 pt-2">
              <div className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full typing-dot" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full typing-dot" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full typing-dot" style={{ animationDelay: "300ms" }} />
              </div>
              <span className="text-xs text-zinc-500">
                {streamingRole ? `${roleDisplayName(streamingRole)} 正在输入...` : "思考中..."}
              </span>
            </div>
          </div>
        ) : null}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
