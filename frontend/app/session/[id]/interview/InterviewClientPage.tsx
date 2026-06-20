"use client";

import { useEffect, useState, useRef, Suspense } from "react";
import { useParams } from "next/navigation";
import { Brain, AlertCircle } from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import { InterviewView } from "@/components/interview/InterviewView";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { Card } from "@/components/ui/card";

function InterviewContent() {
  const params = useParams();
  const {
    sessionId,
    loadSession,
    createInterviewSpace,
    startInterview,
    isHistoryOpen,
    toggleHistory,
  } = useSessionStore();
  const dimensionsCovered = useSessionStore((s) => s.dimensionsCovered);
  const questionCount = useSessionStore((s) => s.questionCount);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [interviewId, setInterviewId] = useState<string | null>(null);
  const triggeredRef = useRef(false);

  useEffect(() => {
    const id = Array.isArray(params?.id) ? params.id[0] : params?.id;
    if (!id) return;
    loadSession(id)
      .then(async () => {
        try {
          const spaceId = await createInterviewSpace(id);
          setInterviewId(spaceId);
        } catch (err) {
          setLoadError(err instanceof Error ? err.message : "创建面试空间失败");
        }
        setLoading(false);
      })
      .catch((err) => {
        setLoadError(err instanceof Error ? err.message : "加载会话失败");
        setLoading(false);
      });
  }, [params?.id, loadSession, createInterviewSpace]);

  useEffect(() => {
    if (!loading && sessionId && interviewId && !triggeredRef.current) {
      triggeredRef.current = true;
      startInterview();
    }
  }, [loading, sessionId, interviewId, startInterview]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <Card className="p-8 max-w-sm w-full text-center">
          <div className="mb-4 flex justify-center">
            <Brain className="h-12 w-12 text-primary animate-pulse" />
          </div>
          <div className="text-foreground text-lg font-medium mb-2">准备面试...</div>
          <div className="mt-4 w-48 h-1 bg-muted rounded-full mx-auto overflow-hidden">
            <div className="h-full bg-primary/50 rounded-full animate-pulse" style={{ width: "60%" }} />
          </div>
        </Card>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <Card className="p-8 max-w-md w-full text-center">
          <div className="mb-4 flex justify-center">
            <AlertCircle className="h-12 w-12 text-destructive" />
          </div>
          <div className="text-destructive text-lg font-semibold mb-2">加载失败</div>
          <div className="text-muted-foreground text-sm mb-4">{loadError}</div>
          <a href="/" className="text-primary hover:text-primary/80 text-sm underline">
            返回首页
          </a>
        </Card>
      </div>
    );
  }

  return (
    <>
      <InterviewView
        dimensionsCovered={dimensionsCovered}
        questionCount={questionCount}
      />
      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />
    </>
  );
}

export default function InterviewClientPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <div className="w-10 h-10 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
        </div>
      }
    >
      <InterviewContent />
    </Suspense>
  );
}
