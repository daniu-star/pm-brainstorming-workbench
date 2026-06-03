import React, { type SVGProps } from "react";
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
  children: React.ReactNode;
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
  const { children, ...data } = makeIcon(size, "M3 3h18v8H3zM3 14h8v7H3zM13 14h8v7h-8z");
  return <svg className={className} {...data} {...props} />;
}

export function CodeIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M8 18l-6-6 6-6M16 6l6 6-6 6M14 4l-4 16");
  return <svg className={className} {...data} {...props} />;
}

export function PaletteIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2a10 10 0 1 0 5.5 18.5c.5-.5.5-1.2.1-1.7-.3-.5-.8-.8-1.3-.8H15a2 2 0 0 1-2-2v-.5a1.5 1.5 0 0 1 1.5-1.5h1.2a5.3 5.3 0 0 0 5.3-5.3A8 8 0 0 0 12 2Z M8.5 11a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z M15.5 8a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z");
  return <svg className={className} {...data} {...props} />;
}

export function ChartIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M3 3v16a2 2 0 0 0 2 2h16 M7 16l4-8 4 4 4-6 M7 16V9 M11 8v8 M15 12v4 M19 10v6");
  return <svg className={className} {...data} {...props} />;
}

export function UserIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2 M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z");
  return <svg className={className} {...data} {...props} />;
}

export function CrosshairIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2v4 M12 18v4 M2 12h4 M18 12h4 M12 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z");
  return <svg className={className} {...data} {...props} />;
}

export function SearchIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M21 21l-4.3-4.3 M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z");
  return <svg className={className} {...data} {...props} />;
}

export function InterviewIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20ZM12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10ZM12 12a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z");
  return <svg className={className} {...data} {...props} />;
}

export function SendIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M22 2 11 13M22 2 15 22 11 13 2 9 22 2");
  return <svg className={className} {...data} {...props} />;
}

export function ArrowRightIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M5 12h14 M13 5l7 7-7 7");
  return <svg className={className} {...data} {...props} />;
}

export function HistoryIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 8v4l3 3 M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0Z");
  return <svg className={className} {...data} {...props} />;
}

export function PlusIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 5v14 M5 12h14");
  return <svg className={className} {...data} {...props} />;
}

export function MicIcon({ size = 24, className, ...props }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z" />
    </svg>
  );
}

export function CoachIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2v4 M12 18v4 M2 12h4 M18 12h4 M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z M8 14l-3 4 M16 14l3 4");
  return <svg className={className} {...data} {...props} />;
}

export function InterviewerIcon({ size = 24, className, ...props }: IconProps) {
  const { children, ...data } = makeIcon(size, "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z M19 10v2a7 7 0 0 1-14 0v-2 M12 19v3 M4 4l16 16");
  return <svg className={className} {...data} {...props} />;
}

export function getRoleIcon(role: Role, size?: number) {
  const s = size ?? 24;
  switch (role) {
    case "cto": return <CodeIcon size={s} />;
    case "designer": return <PaletteIcon size={s} />;
    case "ops": return <ChartIcon size={s} />;
    case "user": return <UserIcon size={s} />;
    case "coach": return <CoachIcon size={s} />;
    case "interviewer": return <InterviewerIcon size={s} />;
  }
}

export function WalletIcon({ size = 24, className, ...props }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="2" y="5" width="20" height="14" rx="2"/>
      <path d="M16 12h.01"/>
      <path d="M2 10h20"/>
    </svg>
  );
}

export function GearIcon({ size = 24, className, ...props }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1Z"/>
    </svg>
  );
}

export function CloseIcon({ size = 24, className, ...props }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M18 6L6 18M6 6l12 12" />
    </svg>
  );
}

export function SpinnerIcon({ size = 24, className, ...props }: IconProps) {
  return (
    <svg className={`animate-spin ${className ?? ""}`} width={size} height={size} viewBox="0 0 24 24" fill="none" {...props}>
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

const ROLE_NAME_TO_FILENAME: Record<string, string> = {
  "产品教练": "coach",
  "AI面试官": "interviewer-business",
  "interviewer": "interviewer-business",
};

export function getRoleAvatar(roleName?: string): string {
  if (!roleName) return "/avatars/coach.svg";
  const filename = ROLE_NAME_TO_FILENAME[roleName] || roleName;
  return `/avatars/${filename}.svg`;
}
