"use client";

/**
 * 账户自带模型（BYOK）配置的客户端调用。
 *
 * 平台前期免费，但模型调用由用户自己买单：登录后必须先填自己的接口地址 / 模型名 / Key。
 * 服务端只回显密钥末 4 位，见 `GET /api/v2/llm-config`。
 */
import { apiV2 } from "@/lib/api";

export interface LlmConfigView {
  mode: "byok" | "system";
  configured: boolean;
  requires_config: boolean;
  base_url: string;
  model: string;
  key_hint: string;
  disable_reasoning: boolean;
  updated_at: string | null;
  last_verified_at: string | null;
  last_error: string | null;
  defaults: { base_url: string; model: string };
  settings_path: string;
  key_present: boolean;
}

export interface LlmConfigInput {
  base_url: string;
  model: string;
  api_key: string;
  disable_reasoning: boolean;
}

export interface LlmConfigTestResult {
  ok: boolean;
  error?: string;
  code?: string;
  reply?: string;
  model?: string;
  elapsed_ms: number;
  view: LlmConfigView;
}

export function getLlmConfig(signal?: AbortSignal): Promise<LlmConfigView> {
  return apiV2<LlmConfigView>("/api/v2/llm-config", { signal });
}

export function saveLlmConfig(input: LlmConfigInput): Promise<LlmConfigView> {
  // PUT 是幂等覆盖写：重放不会产生第二条配置，可以安全重试。
  return apiV2<LlmConfigView>("/api/v2/llm-config", {
    method: "PUT",
    body: input,
    retryTransient: true,
  });
}

export function deleteLlmConfig(): Promise<LlmConfigView> {
  return apiV2<LlmConfigView>("/api/v2/llm-config", {
    method: "DELETE",
    retryTransient: true,
  });
}

export function testLlmConfig(): Promise<LlmConfigTestResult> {
  return apiV2<LlmConfigTestResult>("/api/v2/llm-config/test", { method: "POST", body: {} });
}

/** 不需要「先配 Key」就能看的页面：登录页、产品介绍页、配置页本身。 */
export const GATE_ALLOWED_PATHS: readonly string[] = ["/login", "/product", "/settings/llm"];

export type LlmGateState = "bypass" | "loading" | "blocked" | "ready";

/**
 * 闸门状态（纯函数，便于单测）：
 * - ``bypass``：白名单页面，直接渲染；
 * - ``loading``：还没拿到配置，显示「正在检查模型配置…」；
 * - ``blocked``：必须先去配置页；
 * - ``ready``：已配置，正常渲染业务页面。
 *
 * 读配置失败（``failed``）时放行：一次网络抖动不该把整个应用锁死，
 * 真正发起的模型调用仍会被服务端 428 拦住。
 */
export function gateState(input: {
  pathname: string;
  view: LlmConfigView | null;
  failed: boolean;
}): LlmGateState {
  if (GATE_ALLOWED_PATHS.includes(input.pathname)) return "bypass";
  if (input.failed) return "bypass";
  if (input.view === null) return "loading";
  return input.view.requires_config ? "blocked" : "ready";
}
