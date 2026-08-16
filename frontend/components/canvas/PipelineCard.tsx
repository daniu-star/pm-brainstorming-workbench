"use client";

import type { Branch } from "@/lib/types";
import { ROLE_MAP, TYPE_CONFIG } from "@/lib/types";
import { getRoleAvatar } from "@/components/icons";

const MAX_VISIBLE_LEAVES = 5;

interface Props {
  branch: Branch;
  index: number;
  total: number;
}

export function PipelineCard({ branch }: Props) {
  const childCount = branch.children?.length || 0;
  const firstRoleColor = branch.children?.[0] ? ROLE_MAP[branch.children[0].source_role]?.color : "#52525b";
  const visibleLeaves = (branch.children || []).slice(0, MAX_VISIBLE_LEAVES);
  const hiddenCount = childCount - MAX_VISIBLE_LEAVES;

  return (
    <div
      className="w-[230px] shrink-0 rounded-xl bg-dark-800/40 flex flex-col"
      style={{ borderTop: `3px solid ${firstRoleColor}` }}
    >
      {/* Header */}
      <div className="px-4 py-3 border-b border-zinc-800/50">
        <span className="text-base text-zinc-200 font-semibold line-clamp-2">{branch.name}</span>
        <span className="text-xs text-zinc-500 mt-1 block">{childCount} 个观点</span>
      </div>

      {/* Leaves */}
      <div className="flex-1 px-2 py-2 space-y-1">
        {visibleLeaves.map((leaf, j) => {
          const roleColor = ROLE_MAP[leaf.source_role]?.color || "#6b7280";
          const cfg = TYPE_CONFIG[leaf.type] || { label: leaf.type, color: "#6b7280", bg: "#6b728018" };
          return (
            <div key={j} className="flex items-start gap-2 px-2 py-1.5 rounded-md hover:bg-dark-700/30 transition-colors group">
              <img
                src={getRoleAvatar(leaf.source_role)}
                alt=""
                loading="lazy"
                className="w-5 h-5 rounded-full shrink-0 mt-0.5"
              />
              <span
                className="text-[10px] px-1 py-0.5 rounded-full font-medium shrink-0 mt-0.5 leading-none"
                style={{ backgroundColor: cfg.bg, color: cfg.color }}
              >
                {cfg.label}
              </span>
              <span className="text-sm text-zinc-300 leading-snug flex-1 min-w-0 line-clamp-2">{leaf.name}</span>
            </div>
          );
        })}
        {hiddenCount > 0 && (
          <div className="text-xs text-zinc-500 text-center py-1">+{hiddenCount} 条更多</div>
        )}
      </div>
    </div>
  );
}
