"use client";

import type { DiscussionMap } from "@/lib/types";
import { TIMELINE_NODE_CONFIG } from "@/lib/types";
import { TimelineNodeCard } from "./TimelineNodeCard";

interface Props {
  map: DiscussionMap;
}

export function TimelineView({ map }: Props) {
  const timeline = map.timeline || [];

  if (timeline.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-warm-500 text-sm">暂无讨论节点</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="text-center pt-6 pb-4 px-4">
        <h2 className="text-xl font-semibold text-warm-600">{map.topic}</h2>
      </div>

      <div className="relative px-4 pb-8" style={{ minHeight: timeline.length * 100 }}>
        <div className="timeline-axis-dashed" />

        <div className="relative z-10">
          {timeline.map((node, i) => {
            const isLeft = i % 2 === 0;
            const config = TIMELINE_NODE_CONFIG[node.type] || TIMELINE_NODE_CONFIG.consensus;

            return (
              <div
                key={node.id || `node-${i}`}
                className="timeline-node-enter flex items-start mb-6"
                style={{ animationDelay: `${i * 100}ms` }}
              >
                {isLeft ? (
                  <>
                    <div className="w-1/2 pr-6 flex justify-end">
                      <TimelineNodeCard node={node} />
                    </div>
                    <div className="relative flex-shrink-0" style={{ width: 12 }}>
                      <span
                        className="timeline-dot"
                        style={{
                          borderColor: config.color,
                          backgroundColor: `${config.color}30`,
                          top: 14,
                        }}
                      />
                    </div>
                    <div className="w-1/2 pl-6" />
                  </>
                ) : (
                  <>
                    <div className="w-1/2 pr-6" />
                    <div className="relative flex-shrink-0" style={{ width: 12 }}>
                      <span
                        className="timeline-dot"
                        style={{
                          borderColor: config.color,
                          backgroundColor: `${config.color}30`,
                          top: 14,
                        }}
                      />
                    </div>
                    <div className="w-1/2 pl-6 flex justify-start">
                      <TimelineNodeCard node={node} />
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
