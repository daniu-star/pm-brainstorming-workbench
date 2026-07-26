import type { CSSProperties } from "react";

interface CosmicBackgroundProps {
  density?: number;
  className?: string;
}

export function CosmicBackground({
  density = 72,
  className = "",
}: CosmicBackgroundProps) {
  return (
    <div className={`cosmic-stars ${className}`} aria-hidden="true">
      {Array.from({ length: density }, (_, index) => {
        const size = index % 13 === 0 ? 3 : index % 5 === 0 ? 2 : 1;
        const style = {
          "--star-x": `${(index * 47 + 7) % 100}%`,
          "--star-y": `${(index * 71 + 13) % 100}%`,
          "--star-size": `${size}px`,
          "--star-delay": `${-((index * 0.37) % 8).toFixed(2)}s`,
          "--star-duration": `${4 + (index % 7)}s`,
        } as CSSProperties;
        return <span key={index} style={style} />;
      })}
    </div>
  );
}
