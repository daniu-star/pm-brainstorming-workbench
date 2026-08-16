import { apiUrl, statusMessage } from "./api";

export interface SSEEvent {
  type: string;
  role?: string;
  role_name?: string;
  token?: string;
  phase?: string;
  message?: string;
  action?: string;
  node?: unknown;
  covered?: string[];
  /** role_done 携带的全量回复内容，前端优先于 token 流拼接（B110）。 */
  content?: string;
}

export function createSSEConnection(
  endpoint: string,
  body: unknown,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  onError: (err: string) => void
): AbortController {
  const controller = new AbortController();

  fetch(apiUrl(endpoint), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        onError(statusMessage(response.status));
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        onError("网络连接失败，请检查网络后重试");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.replace(/\r/g, "").split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === "done") {
                onDone();
              } else {
                onEvent(data as SSEEvent);
              }
            } catch {
              // Skip malformed JSON lines
            }
          }
        }
      }
      onDone();
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        onError("网络连接失败，请检查网络后重试");
      }
    });

  return controller;
}
