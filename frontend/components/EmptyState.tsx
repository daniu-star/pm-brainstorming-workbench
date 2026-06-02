"use client";

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      {icon && <div className="mb-4 text-zinc-400">{icon}</div>}
      <h3 className="text-sm font-medium text-zinc-400 mb-1">{title}</h3>
      {description && <p className="text-xs text-zinc-400 max-w-xs">{description}</p>}
      {action && (
        <button
          onClick={action.onClick}
          aria-label={action.label}
          className="mt-4 text-xs font-medium text-indigo-400 hover:text-indigo-300 active:text-indigo-200 bg-indigo-500/10 hover:bg-indigo-500/20 active:bg-indigo-500/30 px-4 py-2 rounded-lg transition-all duration-200 min-h-[44px] flex items-center focus-visible:ring-2 focus-visible:ring-indigo-500/50 focus-visible:outline-none"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
