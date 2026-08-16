import type { SVGProps } from "react";
import type { Role } from "@/lib/types";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

interface IconData {
  viewBox: string;
  width: number;
  height: number;
  fill: string;
  stroke: string;
  strokeWidth: number;
  strokeLinecap: "round";
  strokeLinejoin: "round";
  children: JSX.Element;
}

function makeIcon(size: number, d: string, sw = 2): IconData {
  return {
    viewBox: "0 0 24 24",
    width: size,
    height: size,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: sw,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    children: <path d={d} />,
  };
}

export function BrainIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M9.5 2A2.5 2.5 0 0 1 12 4.5V18a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2V6.5A2.5 2.5 0 0 1 20.5 4a2.5 2.5 0 0 1 0 5H18M4 10.5V11a3 3 0 0 0 3 3h.5a.5.5 0 0 1 .5.5V16a2 2 0 0 0 2 2h.5M4 10.5A2.5 2.5 0 0 1 6.5 8a2.5 2.5 0 0 1 2.5 2.5V11M4 10.5a2.5 2.5 0 0 0 0 5M16 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function CodeIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M8 18l-6-6 6-6M16 6l6 6-6 6M14 4l-4 16");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function PaletteIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2a10 10 0 1 0 5.5 18.5c.5-.5.5-1.2.1-1.7-.3-.5-.8-.8-1.3-.8H15a2 2 0 0 1-2-2v-.5a1.5 1.5 0 0 1 1.5-1.5h1.2a5.3 5.3 0 0 0 5.3-5.3A8 8 0 0 0 12 2Z M8.5 11a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z M15.5 8a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function ChartIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M3 3v16a2 2 0 0 0 2 2h16 M7 16l4-8 4 4 4-6 M7 16V9 M11 8v8 M15 12v4 M19 10v6");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function UserIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2 M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function CrosshairIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2v4 M12 18v4 M2 12h4 M18 12h4 M12 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function SearchIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M21 21l-4.3-4.3 M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function SendIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 19V5 M5 12l7-7 7 7");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function ArrowRightIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M5 12h14 M13 5l7 7-7 7");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function HistoryIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 8v4l3 3 M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function PlusIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 5v14 M5 12h14");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function MicIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z M19 10v2a7 7 0 0 1-14 0v-2 M12 19v3");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function PlayIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M8 5v14l11-7Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function VideoIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M15 10l4.6-3.1A1 1 0 0 1 21 7.7v8.6a1 1 0 0 1-1.4.8L15 14 M3 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function ShieldIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 3l8 3v5c0 5-3.4 8.7-8 10-4.6-1.3-8-5-8-10V6l8-3Z M9 12l2 2 4-5");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function LayersIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2 2 7l10 5 10-5-10-5Z M2 12l10 5 10-5 M2 17l10 5 10-5");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function SignalIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M4 20v-4 M9 20v-8 M14 20V8 M19 20V4");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function PhoneIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z");
  return <svg className={className} {...data} {...props}>{children}</svg>;
}

export function getRoleIcon(role: Role, size?: number) {
  const s = size ?? 24;
  switch (role) {
    case "cto": return <CodeIcon size={s} />;
    case "designer": return <PaletteIcon size={s} />;
    case "ops": return <ChartIcon size={s} />;
    case "user": return <UserIcon size={s} />;
    case "coach": return <UserIcon size={s} />;
    case "interviewer": return <VideoIcon size={s} />;
    default: return <UserIcon size={s} />;
  }
}

const ROLE_NAME_TO_FILENAME: Record<string, string> = {
  "产品教练": "coach",
  "AI面试官": "interviewer",
};

export function getRoleAvatar(roleName?: string): string {
  if (!roleName) return "/avatars/coach.svg";
  const filename = ROLE_NAME_TO_FILENAME[roleName] || roleName;
  return `/avatars/${filename}.svg`;
}
