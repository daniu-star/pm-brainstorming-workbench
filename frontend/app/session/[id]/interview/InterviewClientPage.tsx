"use client";

import { useEffect, useState, useRef, useCallback, Suspense } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Brain, AlertCircle } from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import { InterviewView } from "@/components/interview/InterviewView";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

type ErrorInfo = {
  title: string;
  description: string;
  showRetry: boolean;
};

function getErrorMessage(error: string): ErrorInfo {
  if (error.includes("Not Found")) {
    return {
      title: "后端服务未更新或未启动",
      description: "请重启后端服务后重试。如果问题持续，请检查后端是否已加载最新的面试路由代码。",
      showRetry: true,
    };
  }
  if (error.includes("会话未找到") || error.toLowerCase().includes("session not found")) {
    return {
      title: "会话不存在或已被删除",
      description: "该会话可能已被删除或会话 ID 无效。",
      showRetry: false,
    };
  }
  if (error.includes("无法连接") || error.includes("Failed to fetch") || error.includes("NetworkError")) {
    return {
      title: "无法连接到服务器",
      description: "请确认后端服务已启动并正常运行。",
      showRetry: true,
    };
  }
  return {
    title: "加载失败",
    description: error,
    showRetry: true,
  };
}

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

  const loadInterviewData = useCallback(
    async (id: string) => {
      setLoading(true);
      setLoadError(null);
      try {
        await loadSession(id);
        try {
          const spaceId = await createInterviewSpace(id);
          setInterviewId(spaceId);
        } catch (err) {
          setLoadError(err instanceof Error ? err.message : "创建面试空间失败");
        }
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : "加载会话失败");
      } finally {
        setLoading(false);
      }
    },
    [loadSession, createInterviewSpace]
  );

  useEffect(() => {
    const id = Array.isArray(params?.id) ? params.id[0] : params?.id;
    if (!id) return;
    loadInterviewData(id);
  }, [params?.id, loadInterviewData]);

  const retry = () => {
    const id = Array.isArray(params?.id) ? params.id[0] : params?.id;
    if (!id) return;
    triggeredRef.current = false;
    loadInterviewData(id);
  };

  useEffect(() => {
    if (!loading && sessionId && interviewId && !triggeredRef.current) {
      triggeredRef.current = true;
      startInterview();
    }
  }, [loading, sessionId, interviewId, startInterview]);

  if (loading) {
    return (
      <div className="interview-dark-container min-h-screen flex items-center justify-center p-4">
        <Card className="p-8 max-w-sm w-full text-center bg-[#0f0f1a] border-slate-800">
          <div className="mb-4 flex justify-center">
            <Brain className="h-12 w-12 text-primary animate-pulse" />
          </div>
          <div className="text-slate-200 text-lg font-medium mb-2">准备面试...</div>
          <div className="mt-4 w-48 h-1 bg-muted rounded-full mx-auto overflow-hidden">
            <div className="h-full bg-primary/50 rounded-full animate-pulse" style={{ width: "60%" }} />
          </div>
        </Card>
      </div>
    );
  }

  if (loadError) {
    const errorInfo = getErrorMessage(loadError);
    return (
      <div className="interview-dark-container min-h-screen flex items-center justify-center p-4">
        <Card className="p-8 max-w-md w-full text-center bg-[#0f0f1a] border-slate-800">
          <div className="mb-4 flex justify-center">
            <AlertCircle className="h-12 w-12 text-destructive" />
          </div>
          <div className="text-destructive text-lg font-semibold mb-2">{errorInfo.title}</div>
          <div className="text-slate-400 text-sm mb-4">{errorInfo.description}</div>
          <div className="flex flex-col gap-2 items-center">
            {errorInfo.showRetry && (
              <Button onClick={retry} variant="default">
                重试
              </Button>
            )}
            <Link href="/" className="text-primary hover:text-primary/80 text-sm underline">
              返回首页
            </Link>
          </div>
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
        <div className="interview-dark-container min-h-screen flex items-center justify-center">
          <div className="w-10 h-10 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
        </div>
      }
    >
      <InterviewContent />
    </Suspense>
  );
}
