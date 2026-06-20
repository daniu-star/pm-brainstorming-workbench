"use client";

import type { ProductPortrait as ProductPortraitType } from "@/lib/types";
import { ProductWireframe } from "./ProductWireframe";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

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
      <Card className="border-border">
        <CardHeader className="p-5 space-y-1">
          <CardTitle className="text-xl text-foreground">{product_name}</CardTitle>
          <p className="text-sm text-primary font-medium">{tagline}</p>
        </CardHeader>
      </Card>

      {target_users && (
        <Card className="border-border">
          <CardHeader className="p-4 space-y-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              目标用户
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <p className="text-sm text-foreground leading-relaxed">{target_users}</p>
          </CardContent>
        </Card>
      )}

      {core_features.length > 0 && (
        <Card className="border-border">
          <CardHeader className="p-4 space-y-3">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              核心功能
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0 space-y-3">
            {mustHave.length > 0 && (
              <div className="space-y-2">
                {mustHave.map((f, i) => (
                  <FeatureItem key={i} feature={f} />
                ))}
              </div>
            )}
            {niceToHave.length > 0 && (
              <div className="space-y-2 pt-2 border-t border-border">
                {niceToHave.map((f, i) => (
                  <FeatureItem key={i} feature={f} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {style_keywords.length > 0 && (
        <Card className="border-border">
          <CardHeader className="p-4 space-y-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              风格关键词
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <div className="flex flex-wrap gap-2">
              {style_keywords.map((kw, i) => (
                <Badge key={i} variant="secondary">
                  {kw}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {color_scheme && (
        <Card className="border-border">
          <CardHeader className="p-4 space-y-3">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              配色方案
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <div className="flex gap-4">
              {(["primary", "secondary", "accent", "background"] as const).map((key) => {
                const hex = color_scheme[key];
                if (!hex) return null;
                return (
                  <div key={key} className="flex flex-col items-center gap-1.5">
                    <div
                      className="w-10 h-10 rounded-full border border-border shadow-inner"
                      style={{ backgroundColor: hex }}
                    />
                    <span className="text-[10px] text-muted-foreground font-mono">{hex}</span>
                    <span className="text-[10px] text-muted-foreground">
                      {key === "primary" ? "主色" : key === "secondary" ? "辅色" : key === "accent" ? "强调" : "背景"}
                    </span>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {interaction_style && (
        <Card className="border-border">
          <CardHeader className="p-4 space-y-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              交互风格
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <p className="text-sm text-foreground">{interaction_style}</p>
          </CardContent>
        </Card>
      )}

      {wireframe_description && (
        <Card className="border-border">
          <CardHeader className="p-4 space-y-3">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              页面线框
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <ProductWireframe description={wireframe_description} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function FeatureItem({ feature }: { feature: { name: string; description: string; priority: "must-have" | "nice-to-have" } }) {
  const isMust = feature.priority === "must-have";
  return (
    <div className="flex items-start gap-2">
      <Badge
        variant={isMust ? "default" : "secondary"}
        className="shrink-0 mt-0.5 px-1.5 text-[10px]"
      >
        {isMust ? "必备" : "加分"}
      </Badge>
      <div className="min-w-0">
        <span className="text-sm font-medium text-foreground">{feature.name}</span>
        {feature.description && (
          <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{feature.description}</p>
        )}
      </div>
    </div>
  );
}
