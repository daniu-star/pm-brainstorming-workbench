"use client";

import type { FeatureTree } from "@/lib/types";
import { BrainIcon } from "@/components/icons";
import { PipelineCard } from "./PipelineCard";
import { PipelineArrow } from "./PipelineArrow";

interface Props {
  tree: FeatureTree;
}

export function PipelineView({ tree }: Props) {
  const branches = tree.branches || [];

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Compact root bar */}
      <div className="shrink-0 px-5 py-3 border-b border-zinc-800/50 flex items-center gap-2">
        <BrainIcon size={14} className="text-brand-300 shrink-0" />
        <span className="text-sm text-zinc-400 font-medium truncate">{tree.root}</span>
      </div>

      {/* Horizontal pipeline with right-edge fade hint */}
      <div className="relative flex-1 min-h-0">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 z-10 w-8 bg-gradient-to-l from-dark-900 to-transparent"
        />
        <div className="h-full overflow-x-auto overflow-y-hidden">
          <div className="flex items-start gap-0 p-5 min-h-full">
            {branches.map((branch, i) => (
              <div key={`branch-${i}`} className="flex items-start">
                <PipelineCard branch={branch} index={i} total={branches.length} />
                {i < branches.length - 1 && <PipelineArrow />}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
