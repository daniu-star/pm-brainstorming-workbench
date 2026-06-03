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
      {icon && <div className="mb-4 text-warm-500">{icon}</div>}
      <h3 className="text-sm font-medium text-warm-600 mb-1">{title}</h3>
      {description && <p className="text-xs text-warm-500 max-w-xs">{description}</p>}
      {action && (
        <button
          onClick={action.onClick}
          aria-label={action.label}
          className="mt-4 text-xs font-medium text-amber-600 hover:text-amber-700 active:text-amber-800 bg-amber-50 hover:bg-amber-100 active:bg-amber-200 border border-amber-200 px-4 py-2 rounded-xl transition-colors duration-200 min-h-[44px] flex items-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
