"use client";

import { useEffect, useState, useRef, Suspense } from "react";
import { useParams } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { InterviewView } from "@/components/interview/InterviewView";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { BrainIcon } from "@/components/icons";

function InterviewContent() {
  const params = useParams();
  const { sessionId, loadSession, startInterview, isHistoryOpen, toggleHistory } =
    useSessionStore();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const triggeredRef = useRef(false);

  useEffect(() => {
    const id = Array.isArray(params?.id) ? params.id[0] : params?.id;
    if (!id) return;
    if (id) {
      loadSession(id)
        .then(() => setLoading(false))
        .catch((err) => {
          setLoadError(err instanceof Error ? err.message : "加载会话失败");
          setLoading(false);
        });
    }
  }, [params?.id, loadSession]);

  useEffect(() => {
    if (!loading && sessionId && !triggeredRef.current) {
      triggeredRef.current = true;
      startInterview();
    }
  }, [loading, sessionId, startInterview]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-warm-50">
        <div className="text-center">
          <div className="mb-6 flex justify-center">
            <BrainIcon size={48} className="text-red-500 animate-pulse" />
          </div>
          <div className="text-warm-600 text-lg font-medium mb-2">准备面试...</div>
          <div className="mt-4 w-48 h-1 bg-warm-200 rounded-full mx-auto overflow-hidden">
            <div className="h-full bg-red-400/50 rounded-full animate-pulse" style={{ width: "60%" }} />
          </div>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-warm-50">
        <div className="text-center max-w-md">
          <div className="text-red-600 text-lg font-semibold mb-2">加载失败</div>
          <div className="text-warm-500 text-sm mb-4">{loadError}</div>
          <a href="/" className="text-amber-600 hover:text-amber-500 text-sm underline">
            返回首页
          </a>
        </div>
      </div>
    );
  }

  return (
    <>
      <InterviewView />
      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />
    </>
  );
}

export default function InterviewClientPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-warm-50">
          <div className="w-10 h-10 border-2 border-amber-500/30 border-t-amber-500 rounded-full animate-spin" />
        </div>
      }
    >
      <InterviewContent />
    </Suspense>
  );
}
