"use client";

import type { TimelineNode } from "@/lib/types";
import { TIMELINE_NODE_CONFIG, ROLE_MAP } from "@/lib/types";
import { getRoleAvatar } from "@/components/icons";

interface Props {
  node: TimelineNode;
}

export function TimelineNodeCard({ node }: Props) {
  const config = TIMELINE_NODE_CONFIG[node.type] || TIMELINE_NODE_CONFIG.consensus;

  const gradientMap: Record<string, string> = {
    consensus: "from-emerald-100 to-transparent",
    disagreement: "from-red-100 to-transparent",
    summary: "from-amber-100 to-transparent",
  };

  const borderLeftMap: Record<string, string> = {
    consensus: "#22c55e",
    disagreement: "#ef4444",
    summary: "#6366f1",
  };

  const textColorMap: Record<string, string> = {
    consensus: "text-warm-600",
    disagreement: "text-warm-600",
    summary: "text-amber-600",
  };

  return (
    <div
      className="max-w-[280px] w-full rounded-xl bg-white shadow-sm bg-gradient-to-r hover:-translate-y-0.5 hover:shadow-md active:scale-[0.98] transition-all duration-200 relative overflow-hidden cursor-default"
      style={{
        backgroundImage: undefined,
      }}
    >
      <div className="absolute left-0 top-0 bottom-0 w-[4px]" style={{ backgroundColor: config.color }} />
      <div className={`absolute inset-0 bg-gradient-to-r ${gradientMap[node.type] || gradientMap.consensus}`} />

      <div className="relative p-3.5 pl-5">
        <div className="flex items-center justify-between mb-2">
          <span
            className="text-[10px] px-2 py-0.5 rounded-full font-medium"
            style={{ backgroundColor: config.bg, color: config.color }}
          >
            {config.label}
          </span>
          <div className="flex items-center -space-x-1.5">
            {node.roles?.slice(0, 4).map((role) => {
              const roleInfo = ROLE_MAP[role];
              return (
                <img
                  key={role}
                  src={getRoleAvatar(role)}
                  alt={roleInfo?.name || role}
                  className="w-5 h-5 rounded-full border border-warm-200"
                  style={{ borderColor: roleInfo?.color || "#8b6f47" }}
                />
              );
            })}
            {node.roles?.length > 4 && (
              <span className="w-5 h-5 rounded-full bg-warm-100 text-[11px] text-warm-500 flex items-center justify-center border border-warm-200">
                +{node.roles.length - 4}
              </span>
            )}
          </div>
        </div>

        <p className={`text-sm leading-relaxed ${textColorMap[node.type] || "text-warm-600"}`}>
          {node.content}
        </p>

        {node.type === "disagreement" && node.positions?.length > 0 && (
          <div className="mt-2 space-y-1">
            {node.positions.map((pos, i) => {
              const roleInfo = ROLE_MAP[pos.role];
              return (
                <div key={i} className="flex items-start gap-1.5">
                  <span
                    className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0"
                    style={{ backgroundColor: roleInfo?.color || "#8b6f47" }}
                  />
                  <span className="text-xs text-warm-500 leading-snug">
                    {pos.stance}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        <span
          className="absolute bottom-2 right-3 text-2xl opacity-15 select-none"
          style={{ color: config.color }}
        >
          {config.icon}
        </span>
      </div>
    </div>
  );
}
