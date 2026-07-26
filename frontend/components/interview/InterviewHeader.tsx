"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft, Phone, PhoneOff, Volume2, VolumeX, FileText, ShieldCheck } from "lucide-react";
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
      <span className="absolute text-xs font-semibold text-slate-300 leading-none">
        {coveredCount}/{DIMENSIONS.length}
      </span>
    </div>
  );
}

interface InterviewHeaderProps {
  phoneMode?: boolean;
  onTogglePhoneMode?: () => void;
  dimensionsCovered?: string[];
  onViewPrd?: () => void;
  hasPrd?: boolean;
}

export function InterviewHeader({
  phoneMode,
  onTogglePhoneMode,
  dimensionsCovered = [],
  onViewPrd,
  hasPrd,
}: InterviewHeaderProps) {
  const router = useRouter();
  const { sessionId, interviewMode, setInterviewMode } = useSessionStore();
  const auditStatus = useSessionStore((s) => s.auditStatus);

  return (
    <header className="interview-command-header interview-dark-header flex items-center justify-between px-4 md:px-6 shrink-0 shadow-sm">
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
          <ArrowLeft className="h-5 w-5 text-slate-300" />
        </Button>

        <div className="flex items-center gap-2">
          <Avatar className="h-10 w-10 border border-cyan-200/30 shadow-[0_0_20px_rgba(34,211,238,0.16)]">
            <AvatarImage src={INTERVIEWER_AVATAR} alt="AI 面试官" />
            <AvatarFallback>AI</AvatarFallback>
          </Avatar>
          <div className="flex flex-col">
            <span className="text-xs font-medium tracking-[0.12em] text-cyan-100/70 leading-tight">
              AI AUDIT ROOM / SECURE
            </span>
            <span className="text-sm font-bold text-slate-100 leading-tight">AI 审计专业通话</span>
            <Badge variant="secondary" className="mt-0.5 flex items-center gap-1 text-xs py-0 px-1.5 w-fit border-cyan-200/15 bg-cyan-300/5 text-cyan-100">
              <span className={`w-1.5 h-1.5 rounded-full ${auditStatus === "completed" ? "bg-emerald-400" : "bg-primary animate-pulse"}`} />
              {auditStatus === "completed" ? "审计已完成" : "审计进行中"}
            </Badge>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <DimensionRing dimensionsCovered={dimensionsCovered} />

        {hasPrd && (
          <Button
            onClick={onViewPrd}
            variant="ghost"
            size="sm"
            className="text-xs"
          >
            <FileText size={16} /> 查看PRD
          </Button>
        )}

        <Button
          variant={interviewMode === "voice" ? "default" : "outline"}
          size="sm"
          onClick={() => setInterviewMode(interviewMode === "voice" ? "text" : "voice")}
          aria-label={interviewMode === "voice" ? "关闭语音" : "开启语音"}
          aria-pressed={interviewMode === "voice"}
          className="h-10 w-10 text-slate-300 hover:text-white"
        >
          {interviewMode === "voice" ? (
            <Volume2 className="h-4 w-4" />
          ) : (
            <VolumeX className="h-4 w-4" />
          )}
        </Button>

        <Button
          variant={phoneMode ? "destructive" : "outline"}
          size="sm"
          onClick={onTogglePhoneMode}
          aria-label="AI 审计通话模式"
          aria-pressed={phoneMode}
          className="h-10 min-w-10 px-3 text-slate-200 hover:text-white"
        >
          {phoneMode ? <PhoneOff className="h-4 w-4" /> : <Phone className="h-4 w-4" />}
          <span className="hidden sm:inline">{phoneMode ? "退出通话" : "审计通话"}</span>
        </Button>
        <div className="hidden items-center gap-1.5 rounded-lg border border-emerald-300/15 bg-emerald-300/5 px-2.5 py-1.5 text-xs text-emerald-200 lg:flex">
          <ShieldCheck className="h-3.5 w-3.5" />
          独立审计记录
        </div>
      </div>
    </header>
  );
}
