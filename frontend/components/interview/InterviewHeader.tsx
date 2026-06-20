"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft, Phone, PhoneOff, Volume2, VolumeX } from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";

const INTERVIEWER_AVATAR = "/avatars/interviewer-business.svg";

const DIMENSIONS: { key: string; label: string }[] = [
  { key: "problem_validity", label: "问题有效性" },
  { key: "solution_effectiveness", label: "方案有效性" },
  { key: "technical_risk", label: "技术风险" },
  { key: "business_viability", label: "商业可行性" },
  { key: "user_adoption", label: "用户采用" },
  { key: "execution_risk", label: "执行风险" },
];

function DimensionRing({ dimensionsCovered }: { dimensionsCovered: string[] }) {
  const size = 40;
  const r = 15;
  const cx = size / 2;
  const cy = size / 2;
  const C = 2 * Math.PI * r;
  const segmentLen = C / DIMENSIONS.length;
  const gap = 3;
  const dashLen = segmentLen - gap;
  const coveredCount = DIMENSIONS.filter((d) => dimensionsCovered.includes(d.key)).length;

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <g transform={`rotate(-90 ${cx} ${cy})`}>
          {DIMENSIONS.map((dim, i) => {
            const covered = dimensionsCovered.includes(dim.key);
            const offset = -(i * segmentLen);
            return (
              <circle
                key={dim.key}
                cx={cx}
                cy={cy}
                r={r}
                fill="none"
                stroke="currentColor"
                strokeWidth={3}
                strokeLinecap="round"
                strokeDasharray={`${dashLen} ${C - dashLen}`}
                strokeDashoffset={offset}
                className={covered ? "text-primary" : "text-muted"}
              />
            );
          })}
        </g>
      </svg>
      <span className="absolute text-[9px] font-semibold text-muted-foreground leading-none">
        {coveredCount}/{DIMENSIONS.length}
      </span>
    </div>
  );
}

interface InterviewHeaderProps {
  phoneMode?: boolean;
  onTogglePhoneMode?: () => void;
  dimensionsCovered?: string[];
}

export function InterviewHeader({
  phoneMode,
  onTogglePhoneMode,
  dimensionsCovered = [],
}: InterviewHeaderProps) {
  const router = useRouter();
  const { sessionId, interviewMode, setInterviewMode } = useSessionStore();

  return (
    <header className="h-14 bg-card/90 backdrop-blur border-b border-border flex items-center justify-between px-4 shrink-0 shadow-sm">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => {
            if (sessionId) {
              router.push(`/session/${sessionId}`);
            } else {
              router.push("/");
            }
          }}
          aria-label="返回主会话"
          className="h-10 w-10"
        >
          <ArrowLeft className="h-5 w-5" />
        </Button>

        <div className="flex items-center gap-2">
          <Avatar className="h-8 w-8 border border-primary/30">
            <AvatarImage src={INTERVIEWER_AVATAR} alt="AI 面试官" />
            <AvatarFallback>AI</AvatarFallback>
          </Avatar>
          <div className="flex flex-col">
            <span className="text-sm font-bold text-foreground leading-tight">AI 压力面试官</span>
            <Badge variant="secondary" className="flex items-center gap-1 text-[11px] py-0 px-1.5 w-fit">
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
              面试进行中
            </Badge>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <DimensionRing dimensionsCovered={dimensionsCovered} />

        <Button
          variant={interviewMode === "voice" ? "default" : "outline"}
          size="icon"
          onClick={() => setInterviewMode(interviewMode === "voice" ? "text" : "voice")}
          aria-label={interviewMode === "voice" ? "关闭语音" : "开启语音"}
          aria-pressed={interviewMode === "voice"}
          className="h-10 w-10"
        >
          {interviewMode === "voice" ? (
            <Volume2 className="h-4 w-4" />
          ) : (
            <VolumeX className="h-4 w-4" />
          )}
        </Button>

        <Button
          variant={phoneMode ? "destructive" : "outline"}
          size="icon"
          onClick={onTogglePhoneMode}
          aria-label="电话模式"
          aria-pressed={phoneMode}
          className="h-10 w-10"
        >
          {phoneMode ? <PhoneOff className="h-4 w-4" /> : <Phone className="h-4 w-4" />}
        </Button>
      </div>
    </header>
  );
}
