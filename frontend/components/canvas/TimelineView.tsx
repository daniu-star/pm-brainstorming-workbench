"use client";

import type { DiscussionMap } from "@/lib/types";
import { TIMELINE_NODE_CONFIG } from "@/lib/types";
import { TimelineNodeCard } from "./TimelineNodeCard";
import { ScrollArea } from "@/components/ui/scroll-area";

interface Props {
  map: DiscussionMap;
}

export function TimelineView({ map }: Props) {
  const timeline = map.timeline || [];

  if (timeline.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-muted-foreground text-sm">暂无讨论节点</p>
      </div>
    );
  }

  return (
    <ScrollArea className="flex-1 h-full">
      <div className="text-center pt-6 pb-4 px-4">
        <h2 className="text-xl font-semibold text-foreground">{map.topic}</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          版本 {map.version || 1} · {map.sources?.length || 0} 条可追溯来源
        </p>
      </div>

      <div className="relative px-4 pb-8" style={{ minHeight: timeline.length * 100 }}>
        <div className="timeline-axis-dashed" />

        <div className="relative z-10">
          {timeline.map((node, i) => {
            const config = TIMELINE_NODE_CONFIG[node.type] || TIMELINE_NODE_CONFIG.consensus;
            const isLeft = i % 2 === 0;

            return (
              <div
                key={node.id || `node-${i}`}
                className="timeline-node-enter flex items-start mb-6"
                style={{ animationDelay: `${i * 100}ms` }}
              >
                {/* Mobile: always left-aligned */}
                <div className="md:hidden w-full pl-8 flex">
                  <span
                    className="timeline-dot"
                    style={{
                      borderColor: config.color,
                      backgroundColor: `${config.color}30`,
                      top: 14,
                      left: 20,
                    }}
                  />
                  <TimelineNodeCard node={node} />
                </div>

                {/* Desktop: alternating layout */}
                {isLeft ? (
                  <>
                    <div className="hidden md:block md:w-1/2 md:pr-6 md:flex md:justify-end">
                      <TimelineNodeCard node={node} />
                    </div>
                    <div className="hidden md:block relative flex-shrink-0" style={{ width: 12 }}>
                      <span
                        className="timeline-dot"
                        style={{
                          borderColor: config.color,
                          backgroundColor: `${config.color}30`,
                          top: 14,
                        }}
                      />
                    </div>
                    <div className="hidden md:block md:w-1/2 md:pl-6" />
                  </>
                ) : (
                  <>
                    <div className="hidden md:block md:w-1/2 md:pr-6" />
                    <div className="hidden md:block relative flex-shrink-0" style={{ width: 12 }}>
                      <span
                        className="timeline-dot"
                        style={{
                          borderColor: config.color,
                          backgroundColor: `${config.color}30`,
                          top: 14,
                        }}
                      />
                    </div>
                    <div className="hidden md:block md:w-1/2 md:pl-6 md:flex md:justify-start">
                      <TimelineNodeCard node={node} />
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </ScrollArea>
  );
}
