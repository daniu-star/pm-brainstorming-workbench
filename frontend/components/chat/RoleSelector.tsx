"use client";

import { CrosshairIcon, handleAvatarError } from "@/components/icons";
import { ROLES } from "@/lib/types";
import { useSessionStore } from "@/store/sessionStore";

export function RoleSelector() {
  const { isStreaming, targetRole, setTargetRole } = useSessionStore();

  return (
    <div className="border-t border-cyan-300/10 bg-slate-950/45 px-4 py-3 backdrop-blur-xl">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[0.2em] text-cyan-200/65">
          AGENT MATRIX
        </span>
        <span className="text-[10px] text-slate-400">选择审议角色</span>
      </div>
      <div className="flex items-center gap-3 overflow-x-auto pb-1">
        {ROLES.map((role) => {
          const isActive = targetRole === role.id;

          return (
            <button
              key={role.id}
              type="button"
              onClick={() => setTargetRole(role.id)}
              disabled={isStreaming}
              className="group flex min-w-12 shrink-0 flex-col items-center gap-1.5 rounded-xl p-1.5 transition-all duration-200 hover:bg-cyan-300/[0.06] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-cyan-300/45"
              aria-label={`向 ${role.name} 提问`}
            >
              <span
                className={`h-10 w-10 shrink-0 overflow-hidden rounded-xl border transition-all duration-200 ${
                  isActive
                    ? "scale-105 border-cyan-200/70"
                    : "border-white/10 group-hover:border-cyan-300/35"
                }`}
                style={{
                  boxShadow: isActive
                    ? `0 0 0 1px rgba(34,211,238,.28), 0 10px 28px ${role.color}45`
                    : "0 8px 22px rgba(2, 8, 23, .28)",
                }}
              >
                <img
                  src={`/avatars/${role.id}.svg`}
                  onError={handleAvatarError}
                  alt={role.name}
                  className="h-full w-full object-cover"
                />
              </span>
              <span
                className="text-[11px] leading-none text-slate-400 transition-colors group-hover:text-slate-200"
                style={{ color: isActive ? role.color : undefined }}
              >
                {role.name}
              </span>
            </button>
          );
        })}

        <button
          type="button"
          onClick={() => setTargetRole("all")}
          disabled={isStreaming}
          className="group flex min-h-[52px] min-w-12 shrink-0 flex-col items-center gap-1.5 rounded-xl p-1.5 transition-all duration-200 hover:bg-cyan-300/[0.06] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-cyan-300/45"
          aria-label="让所有角色一起讨论"
        >
          <span
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border transition-all duration-200 ${
              targetRole === "all"
                ? "scale-105 border-cyan-200/70"
                : "border-white/10"
            }`}
            style={{
              background:
                targetRole === "all"
                  ? "linear-gradient(135deg, #06b6d4, #3b82f6 54%, #7c3aed)"
                  : "linear-gradient(135deg, rgba(6,182,212,.12), rgba(59,130,246,.12), rgba(124,58,237,.12))",
              boxShadow:
                targetRole === "all"
                  ? "0 0 0 1px rgba(34,211,238,.28), 0 12px 30px rgba(37,99,235,.32)"
                  : "0 8px 22px rgba(2, 8, 23, .28)",
            }}
          >
            <CrosshairIcon size={14} className="text-white" />
          </span>
          <span
            className={`text-[11px] leading-none ${
              targetRole === "all"
                ? "font-semibold text-cyan-200"
                : "text-slate-400"
            }`}
          >
            全部 @
          </span>
        </button>
      </div>
    </div>
  );
}
