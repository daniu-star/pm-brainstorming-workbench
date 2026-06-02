"use client";

import type { ProductPortrait as ProductPortraitType } from "@/lib/types";
import { ProductWireframe } from "./ProductWireframe";

interface ProductPortraitProps {
  portrait: ProductPortraitType;
}

export function ProductPortrait({ portrait }: ProductPortraitProps) {
  const {
    product_name,
    tagline,
    target_users,
    core_features,
    style_keywords,
    color_scheme,
    interaction_style,
    wireframe_description,
  } = portrait;

  const mustHave = core_features.filter((f) => f.priority === "must-have");
  const niceToHave = core_features.filter((f) => f.priority === "nice-to-have");

  return (
    <div className="p-4 space-y-4 overflow-y-auto">
      <div className="bg-white rounded-xl border border-warm-200 p-5 shadow-sm">
        <h2 className="text-xl font-bold text-warm-800">{product_name}</h2>
        <p className="text-sm text-amber-600 mt-1 font-medium">{tagline}</p>
      </div>

      {target_users && (
        <div className="bg-white rounded-xl border border-warm-200 p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">目标用户</h3>
          <p className="text-sm text-warm-700 leading-relaxed">{target_users}</p>
        </div>
      )}

      {core_features.length > 0 && (
        <div className="bg-white rounded-xl border border-warm-200 p-4 shadow-sm space-y-3">
          <h3 className="text-xs font-semibold text-warm-500 uppercase tracking-wider">核心功能</h3>
          {mustHave.length > 0 && (
            <div className="space-y-2">
              {mustHave.map((f, i) => (
                <FeatureItem key={i} feature={f} />
              ))}
            </div>
          )}
          {niceToHave.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-warm-100">
              {niceToHave.map((f, i) => (
                <FeatureItem key={i} feature={f} />
              ))}
            </div>
          )}
        </div>
      )}

      {style_keywords.length > 0 && (
        <div className="bg-white rounded-xl border border-warm-200 p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">风格关键词</h3>
          <div className="flex flex-wrap gap-2">
            {style_keywords.map((kw, i) => (
              <span
                key={i}
                className="px-2.5 py-1 text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200 rounded-full"
              >
                {kw}
              </span>
            ))}
          </div>
        </div>
      )}

      {color_scheme && (
        <div className="bg-white rounded-xl border border-warm-200 p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-3">配色方案</h3>
          <div className="flex gap-4">
            {(["primary", "secondary", "accent", "background"] as const).map((key) => {
              const hex = color_scheme[key];
              if (!hex) return null;
              return (
                <div key={key} className="flex flex-col items-center gap-1.5">
                  <div
                    className="w-10 h-10 rounded-full border border-warm-200 shadow-inner"
                    style={{ backgroundColor: hex }}
                  />
                  <span className="text-[10px] text-warm-500 font-mono">{hex}</span>
                  <span className="text-[10px] text-warm-400">
                    {key === "primary" ? "主色" : key === "secondary" ? "辅色" : key === "accent" ? "强调" : "背景"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {interaction_style && (
        <div className="bg-white rounded-xl border border-warm-200 p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">交互风格</h3>
          <p className="text-sm text-warm-700">{interaction_style}</p>
        </div>
      )}

      {wireframe_description && (
        <div className="bg-white rounded-xl border border-warm-200 p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-3">页面线框</h3>
          <ProductWireframe description={wireframe_description} />
        </div>
      )}
    </div>
  );
}

function FeatureItem({ feature }: { feature: { name: string; description: string; priority: "must-have" | "nice-to-have" } }) {
  const isMust = feature.priority === "must-have";
  return (
    <div className="flex items-start gap-2">
      <span
        className={`shrink-0 mt-0.5 px-1.5 py-0.5 text-[10px] font-semibold rounded ${
          isMust
            ? "bg-amber-100 text-amber-700 border border-amber-300"
            : "bg-warm-100 text-warm-500 border border-warm-200"
        }`}
      >
        {isMust ? "必备" : "加分"}
      </span>
      <div className="min-w-0">
        <span className="text-sm font-medium text-warm-700">{feature.name}</span>
        {feature.description && (
          <p className="text-xs text-warm-500 mt-0.5 leading-relaxed">{feature.description}</p>
        )}
      </div>
    </div>
  );
}
