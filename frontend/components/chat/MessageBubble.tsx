"use client";

import { memo } from "react";
import type { Message } from "@/lib/types";
import { ROLE_MAP } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import { getRoleAvatar, handleAvatarError } from "@/components/icons";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

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
      <div className={cn("msg-enter flex justify-end", mb)}>
        <div className="max-w-[70%]">
          <Card
            className={cn(
              "relative px-4 py-2.5 shadow-sm bg-primary text-primary-foreground border-primary",
              showMeta ? "rounded-2xl rounded-br-[4px]" : "rounded-xl",
              isStreaming && "streaming-cursor"
            )}
          >
            <div className="absolute right-0 top-2 bottom-2 w-[3px] rounded-full bg-primary-foreground/40" />
            <p className="text-sm leading-relaxed whitespace-pre-wrap">
              {message.content}
            </p>
          </Card>
        </div>
      </div>
    );
  }

  if (isInterviewer) {
    return (
      <div className={cn("msg-enter flex gap-3", mb)}>
        {showMeta ? (
          <div className="avatar-pulse-ring shrink-0 mt-0.5">
            <Avatar className="w-9 h-9 rounded-full overflow-hidden relative z-10 interviewer-avatar-ring">
              <img
                src={getRoleAvatar(roleName)}
                onError={handleAvatarError}
                alt="AI 面试官"
                className="w-full h-full object-cover"
              />
              <AvatarFallback>?</AvatarFallback>
            </Avatar>
          </div>
        ) : (
          <div className="w-9 shrink-0" />
        )}
        <div className="flex-1 min-w-0 max-w-[80%]">
          <Card
            className={cn(
              "relative px-4 py-2.5 shadow-sm transition-shadow duration-200 border-destructive/30",
              showMeta ? "rounded-2xl rounded-tl-[4px]" : "rounded-xl",
              isStreaming && "streaming-cursor"
            )}
          >
            <div className="absolute left-0 top-2 bottom-2 w-[4px] rounded-full bg-destructive" />
            {showMeta && (
              <div className="absolute top-1.5 right-3 z-10">
                <Badge variant="destructive" className="text-[11px]">
                  AI 面试官
                </Badge>
              </div>
            )}
            <div className={cn("pl-3", showMeta && "pr-16")}>
              <div className="prose prose-sm max-w-none text-foreground leading-relaxed">
                {isStreaming ? (
                  <p className="whitespace-pre-wrap">{message.content}</p>
                ) : (
                  <ReactMarkdown>{message.content}</ReactMarkdown>
                )}
              </div>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn("msg-enter flex gap-3", mb)}
      style={{ "--role-color": roleColor } as React.CSSProperties}
    >
      {showMeta ? (
        <div className="shrink-0 mt-0.5">
          <Avatar className="w-9 h-9 rounded-full overflow-hidden border-[1.5px] border-[color:var(--role-color)] [box-shadow:0_0_8px_color-mix(in_srgb,var(--role-color)_20%,transparent)]">
            {roleName ? (
              <img
                src={getRoleAvatar(roleName)}
                onError={handleAvatarError}
                alt={roleInfo?.name || roleName}
                className="w-full h-full object-cover"
              />
            ) : (
              <AvatarFallback className="text-muted-foreground">?</AvatarFallback>
            )}
          </Avatar>
        </div>
      ) : (
        <div className="w-9 shrink-0" />
      )}
      <div className="flex-1 min-w-0 max-w-[80%]">
        <Card
          className={cn(
            "relative px-4 py-2.5 shadow-sm transition-shadow duration-200 border-[color:color-mix(in_srgb,var(--role-color)_20%,transparent)] bg-[linear-gradient(135deg,color-mix(in_srgb,var(--role-color)_8%,transparent),color-mix(in_srgb,var(--role-color)_3%,transparent))]",
            showMeta ? "rounded-2xl rounded-tl-[4px]" : "rounded-xl",
            isStreaming && "streaming-cursor"
          )}
        >
          <div className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-[var(--role-color)]" />
          {showMeta && (
            <div className="absolute top-1.5 right-3 z-10">
              <Badge className="text-[11px] text-white border-transparent bg-[var(--role-color)]">
                {roleInfo?.name || roleName || "未知角色"}
              </Badge>
            </div>
          )}
          <div className={cn("pl-2", showMeta && "pr-16")}>
            <div className="prose prose-sm max-w-none text-foreground leading-relaxed">
              {isStreaming ? (
                <p className="whitespace-pre-wrap">{message.content}</p>
              ) : (
                <ReactMarkdown>{message.content}</ReactMarkdown>
              )}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
});
