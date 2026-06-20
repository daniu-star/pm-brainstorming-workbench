import { apiUrl } from "./api";
import { getUserHeaders } from "./user";

export interface SSEEvent {
  type: string;
  role?: string;
  role_name?: string;
  token?: string;
  tokens?: number;
  phase?: string;
  message?: string;
  action?: string;
  node?: string;
  interview_id?: string;
  dimensions_covered?: string[];
  question_count?: number;
  interview_completed?: boolean;
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

  function attemptConnect() {
    if (controller.signal.aborted) return;

    onStatusChange?.(retryCount > 0 ? "reconnecting" : "connecting");

    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    function resetTimeout() {
      if (timeoutId) clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        onError("连接超时，正在重连...");
        retryWithBackoff();
      }, TIMEOUT_MS);
    }

    function retryWithBackoff() {
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
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const text = await response.text();
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

        while (true) {
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
                  onDone();
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
        onDone();
      })
      .catch((err) => {
        if (timeoutId) clearTimeout(timeoutId);
        if (err.name !== "AbortError") {
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
