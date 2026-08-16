"use client";

import { useSessionStore } from "@/store/sessionStore";
import { ROLES, type Role } from "@/lib/types";
import { CrosshairIcon } from "@/components/icons";

export function RoleSelector() {
  const { isStreaming, targetRole, setTargetRole } = useSessionStore();

  const handleRoleClick = (role: Role | "all") => {
    if (isStreaming) return;
    setTargetRole(role);
  };

  return (
    <div className="px-4 py-2.5 border-t border-zinc-800/30">
      <div className="flex items-center gap-2 overflow-x-auto">
        <span className="text-[10px] text-zinc-500 mr-1 shrink-0">@谁:</span>
        {ROLES.map((role) => {
          const selected = targetRole === role.id;
          return (
            <button
              key={role.id}
              onClick={() => handleRoleClick(role.id)}
              disabled={isStreaming}
              aria-pressed={selected}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed hover:scale-105 active:scale-95 shrink-0 min-h-[44px] focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-offset-dark-900"
              style={{
                color: role.color,
                borderColor: selected ? role.color : `${role.color}30`,
                backgroundColor: selected ? `${role.color}2e` : `${role.color}0a`,
                boxShadow: selected ? `0 0 0 1px ${role.color}55` : undefined,
              }}
              aria-label={`向 ${role.name} 提问${selected ? "（当前定向对象）" : ""}`}
            >
              <span className="w-5 h-5 rounded-full overflow-hidden shrink-0">
                <img src={`/avatars/${role.id}.svg`} alt={role.name} loading="lazy" className="w-full h-full object-cover" />
              </span>
              <span>{role.name}</span>
              {selected && (
                <span aria-hidden="true" className="ml-0.5 text-[10px]">
                  ●
                </span>
              )}
            </button>
          );
        })}
        <button
          onClick={() => handleRoleClick("all")}
          disabled={isStreaming}
          aria-pressed={targetRole === "all"}
          className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed hover:scale-105 active:scale-95 shrink-0 min-h-[44px] focus:outline-none focus:ring-2 focus:ring-brand-400/50 focus:ring-offset-1 focus:ring-offset-dark-900 ${
            targetRole === "all"
              ? "bg-brand-500/25 text-brand-300 border-brand-400/60"
              : "bg-brand-500/10 text-brand-400 border-brand-500/30"
          }`}
          aria-label={targetRole === "all" ? "全员讨论（当前选中）" : "让所有角色一起讨论"}
        >
          <CrosshairIcon size={14} />
          <span>全部 @</span>
          {targetRole === "all" && (
            <span aria-hidden="true" className="ml-0.5 text-[10px]">
              ●
            </span>
          )}
        </button>
      </div>
    </div>
  );
}
