import { apiUrl } from "./api";
import { getUserHeaders, handleExpiredSession } from "./user";

export interface SSEEvent {
  type: string;
  role?: string;
  role_name?: string;
  token?: string;
  tokens?: number;
  phase?: string;
  message?: string;
  error_code?: string;
  retryable?: boolean;
  action?: string;
  node?: string;
  interview_id?: string;
  dimensions_covered?: string[];
  question_count?: number;
  interview_completed?: boolean;
  current_dimension?: string | null;
  audit_run_id?: string;
  audit_status?: "not_started" | "active" | "completed" | "aborted" | "superseded";
  audit_report?: string | null;
  // Pipeline 相关字段
  output?: string;
  canvas_tree?: Record<string, unknown>;
  portrait?: Record<string, unknown>;
  acceptance_result?: { passed: boolean; gaps: string[]; suggestions: string[]; summary: string };
  gaps?: string[];
  suggestions?: string[];
  revision_count?: number;
  total_tokens?: number;
  prd?: string;
  product_portrait?: Record<string, unknown>;
  session_id?: string;
  clarification_state?: import("./types").ClarificationState;
  round_id?: string;
  opinion?: Record<string, unknown>;
}

export type SSEConnectionStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

const TIMEOUT_MS = 30000;
const MAX_RETRIES = 5;

export function createSSEConnection(
  endpoint: string,
  body: unknown,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  onError: (err: string) => void,
  onStatusChange?: (status: SSEConnectionStatus) => void
): AbortController {
  const controller = new AbortController();
  let retryCount = 0;
  let finalized = false;
  let activeRequest: AbortController | null = null;

  controller.signal.addEventListener("abort", () => activeRequest?.abort());

  function finishOnce() {
    if (finalized || controller.signal.aborted) return;
    finalized = true;
    onStatusChange?.("connected");
    onDone();
  }

  function attemptConnect() {
    if (controller.signal.aborted || finalized) return;
    const requestController = new AbortController();
    activeRequest = requestController;
    let timedOut = false;

    onStatusChange?.(retryCount > 0 ? "reconnecting" : "connecting");

    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    function resetTimeout() {
      if (timeoutId) clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        timedOut = true;
        requestController.abort();
        onError("连接超时，正在重连...");
        retryWithBackoff();
      }, TIMEOUT_MS);
    }

    function retryWithBackoff() {
      if (controller.signal.aborted || finalized) return;
      if (retryCount >= MAX_RETRIES) {
        onStatusChange?.("disconnected");
        onError("连接失败，请检查网络后重试");
        return;
      }
      retryCount++;
      onStatusChange?.("reconnecting");
      const delay = Math.min(1000 * Math.pow(2, retryCount - 1), 16000);
      setTimeout(() => attemptConnect(), delay);
    }

    fetch(apiUrl(endpoint), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getUserHeaders() },
      body: JSON.stringify(body),
      signal: requestController.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const text = await response.text();
          if (response.status === 401) {
            handleExpiredSession();
          }
          onStatusChange?.("disconnected");
          onError(`HTTP ${response.status}: ${text}`);
          return;
        }

        retryCount = 0;
        onStatusChange?.("connected");
        resetTimeout();

        const reader = response.body?.getReader();
        if (!reader) {
          onStatusChange?.("disconnected");
          onError("No response body");
          return;
        }

        const decoder = new TextDecoder();
        let buffer = "";

        let serverDone = false;
        while (!serverDone) {
          const { done, value } = await reader.read();
          if (done) break;

          resetTimeout();
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.replace(/\r/g, "").split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const data = JSON.parse(line.slice(6));
                if (!data.type || typeof data.type !== "string") {
                  console.warn("SSE: event missing 'type' field:", line.slice(6, 100));
                  continue;
                }
                if (data.type === "done") {
                  if (timeoutId) clearTimeout(timeoutId);
                  serverDone = true;
                  finishOnce();
                  await reader.cancel();
                  break;
                } else {
                  onEvent(data as SSEEvent);
                }
              } catch {
                console.warn("SSE: malformed JSON line:", line.slice(6, 100));
              }
            }
          }
        }

        if (timeoutId) clearTimeout(timeoutId);
        if (!serverDone) finishOnce();
      })
      .catch((err) => {
        if (timeoutId) clearTimeout(timeoutId);
        if (err.name === "AbortError" && timedOut) return;
        if (err.name !== "AbortError" && !finalized) {
          if (err.message?.includes("Failed to fetch") || err.message?.includes("NetworkError")) {
            onStatusChange?.("disconnected");
            onError("无法连接到服务器，请确认后端服务已启动");
          } else {
            retryWithBackoff();
          }
        }
      });
  }

  attemptConnect();
  return controller;
}
