import type { Message } from "./types";
import type { DiscussionMap } from "./types";

const ROLE_NAMES: Record<string, string> = {
  cto: "技术负责人",
  designer: "设计师",
  ops: "运营负责人",
  user_persona: "目标用户",
  interviewer: "AI 面试官",
  coach: "产品教练",
};

function formatTimestamp(ts?: string | number): string {
  if (!ts) return new Date().toLocaleDateString("zh-CN");
  return new Date(ts).toLocaleString("zh-CN");
}

export function exportSessionAsMarkdown(
  topic: string,
  messages: Message[],
  discussionMap?: DiscussionMap | null
): void {
  const lines: string[] = [];

  lines.push(`# ${topic}`);
  lines.push(``);
  lines.push(`> 导出时间：${formatTimestamp()}`);
  lines.push(``);

  lines.push(`---`);
  lines.push(``);

  lines.push(`## 对话记录`);
  lines.push(``);

  for (const msg of messages) {
    const isUser = msg.role === "user";
    const roleName = msg.role_name
      ? ROLE_NAMES[msg.role_name] || msg.role_name
      : isUser
        ? "用户"
        : "助手";
    const time = msg.timestamp ? formatTimestamp(msg.timestamp) : "";

    lines.push(`### ${roleName}${time ? ` · ${time}` : ""}`);
    lines.push(``);
    lines.push(msg.content);
    lines.push(``);
  }

  if (discussionMap && discussionMap.timeline?.length) {
    lines.push(`---`);
    lines.push(``);
    lines.push(`## 讨论地图`);
    lines.push(``);
    lines.push(`**主题：${discussionMap.topic}**`);
    lines.push(``);

    for (const node of discussionMap.timeline) {
      const typeLabel =
        node.type === "consensus"
          ? "✅ 共识"
          : node.type === "disagreement"
            ? "⚠️ 分歧"
            : "📝 阶段总结";
      lines.push(`- ${typeLabel}：${node.content}`);
    }
  }

  const content = lines.join("\n");
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${topic.slice(0, 30).replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, "_")}_${new Date().toISOString().slice(0, 10)}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
