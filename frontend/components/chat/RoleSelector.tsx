"use client";

import { useSessionStore } from "@/store/sessionStore";
import { ROLES } from "@/lib/types";
import { CrosshairIcon, handleAvatarError } from "@/components/icons";

export function RoleSelector() {
  const { isStreaming, targetRole, setTargetRole } = useSessionStore();

  return (
    <div className="px-4 py-2.5 border-t border-warm-200/80">
      <div className="flex items-center gap-3 overflow-x-auto">
        {ROLES.map((role) => (
          <button
            key={role.id}
            onClick={() => setTargetRole(role.id)}
            disabled={isStreaming}
            className="flex flex-col items-center gap-1 shrink-0 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:ring-offset-1 focus:ring-offset-white"
            aria-label={`向 ${role.name} 提问`}
          >
            <span
              className={`w-9 h-9 rounded-full overflow-hidden shrink-0 transition-transform duration-200 ${targetRole === role.id ? "scale-110" : ""}`}
              style={{
                boxShadow:
                  targetRole === role.id
                    ? `0 0 0 2px white, 0 0 0 4px ${role.color}`
                    : "none",
              }}
            >
              <img
                src={`/avatars/${role.id}.svg`}
                onError={handleAvatarError}
                alt={role.name}
                className="w-full h-full object-cover"
              />
            </span>
            <span
              className="text-[11px] leading-none"
              style={{ color: targetRole === role.id ? role.color : undefined }}
            >
              {role.name}
            </span>
          </button>
        ))}
        <button
          onClick={() => setTargetRole("all")}
          disabled={isStreaming}
          className="flex flex-col items-center gap-1 shrink-0 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:ring-offset-1 focus:ring-offset-white min-h-[44px]"
          aria-label="让所有角色一起讨论"
        >
          <span
            className={`w-9 h-9 rounded-full shrink-0 flex items-center justify-center transition-transform duration-200 ${targetRole === "all" ? "scale-110" : ""}`}
            style={{
              background: targetRole === "all"
                ? "linear-gradient(135deg, #3b82f6, #a855f7, #22c55e, #f97316)"
                : "linear-gradient(135deg, #3b82f620, #a855f720, #22c55e20, #f9731620)",
              boxShadow: targetRole === "all"
                ? "0 0 0 2px white, 0 0 0 4px #f59e0b"
                : "none",
            }}
          >
            <CrosshairIcon size={14} className="text-white" />
          </span>
          <span
            className={`text-[11px] leading-none ${targetRole === "all" ? "text-amber-600 font-semibold" : "text-warm-500"}`}
          >
            全部 @
          </span>
        </button>
      </div>
    </div>
  );
}
