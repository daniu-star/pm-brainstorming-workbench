"use client";

interface ProductWireframeProps {
  description: string;
}

interface WireframeSection {
  type: string;
  label: string;
  content: string;
}

const SECTION_STYLES: Record<string, string> = {
  header: "h-8",
  nav: "h-7",
  content: "flex-1 min-h-[80px]",
  sidebar: "w-24 min-h-[60px]",
  footer: "h-6",
};

const SECTION_LABELS: Record<string, string> = {
  header: "顶部导航",
  nav: "导航栏",
  content: "主内容区",
  sidebar: "侧边栏",
  footer: "底部",
};

function parseWireframe(description: string): WireframeSection[] {
  const sections: WireframeSection[] = [];
  const regex = /\[(header|nav|content|sidebar|footer)\]\s*(.*?)(?=\n\[|$)/gi;
  let match;
  while ((match = regex.exec(description)) !== null) {
    sections.push({
      type: match[1].toLowerCase(),
      label: SECTION_LABELS[match[1].toLowerCase()] || match[1],
      content: match[2].trim(),
    });
  }
  return sections;
}

export function ProductWireframe({ description }: ProductWireframeProps) {
  const sections = parseWireframe(description);

  if (sections.length === 0) {
    return (
      <div className="text-xs text-warm-500 italic p-2">
        {description}
      </div>
    );
  }

  const hasSidebar = sections.some((s) => s.type === "sidebar");
  const mainSections = sections.filter((s) => s.type !== "sidebar");
  const sidebarSection = sections.find((s) => s.type === "sidebar");

  return (
    <div className="border border-dashed border-warm-300 rounded-lg p-3 bg-warm-50/50">
      <div className="flex gap-2">
        <div className="flex-1 flex flex-col gap-1.5">
          {mainSections.map((section, i) => (
            <WireframeBlock key={i} section={section} />
          ))}
        </div>
        {hasSidebar && sidebarSection && (
          <div className="flex-none w-20">
            <WireframeBlock section={sidebarSection} />
          </div>
        )}
      </div>
    </div>
  );
}

function WireframeBlock({ section }: { section: WireframeSection }) {
  const styleClass = SECTION_STYLES[section.type] || "";
  const isContent = section.type === "content";
  const isNav = section.type === "nav";

  return (
    <div
      className={`border border-dashed border-warm-300 rounded bg-white flex flex-col items-center justify-center gap-0.5 px-2 py-1 ${styleClass}`}
    >
      <span className="text-xs font-semibold text-warm-500 uppercase tracking-wider">
        {section.label}
      </span>
      {section.content && (
        <span className={`text-warm-500 text-center leading-tight ${
          isContent ? "text-xs" : "text-[11px]"
        } ${isNav ? "max-w-[200px]" : ""}`}>
          {section.content.length > 60
            ? section.content.slice(0, 60) + "..."
            : section.content}
        </span>
      )}
      {isContent && (
        <div className="flex gap-1 mt-1">
          <div className="w-8 h-5 border border-dashed border-warm-200 rounded-sm bg-warm-50" />
          <div className="w-8 h-5 border border-dashed border-warm-200 rounded-sm bg-warm-50" />
          <div className="w-8 h-5 border border-dashed border-warm-200 rounded-sm bg-warm-50" />
        </div>
      )}
    </div>
  );
}
