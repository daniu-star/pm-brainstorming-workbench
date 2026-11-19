/**
 * 澄清简报草稿与服务端版本更新的冲突判定（M3 修复）
 *
 * 背景：BriefCard 原来在 `productVersion` 变化时无条件 `setEdits({})`，
 * 用户正在编辑的草稿会被一条 `product_state.updated` 静默清空。
 * 这里把「是否保留草稿 / 是否提示冲突」抽成纯函数，便于 `node --test` 覆盖
 * （本机 npm registry 不可达，不能安装 RTL 渲染组件，项目惯例是抽 lib 纯函数）。
 */

import { BRIEF_FIELDS } from "./contracts.generated";
import type { BriefFieldName, ProductBrief } from "./contracts.generated";

export type BriefEdits = Record<string, string>;

// --------------------------------------------------------------------------- //
// 阶段门控（UX-D2 / P1）
// --------------------------------------------------------------------------- //

/** 只有澄清阶段才允许「确认简报 / 跳过澄清」 */
export const BRIEF_ACTION_PHASE = "clarifying";

/** 修订简报在服务端允许的阶段（backend/domain/state_machine.py ACTION_PHASES[revise_brief]） */
export const BRIEF_REVISE_PHASES = ["clarifying", "brainstorming", "audit_completed"] as const;

type PhaseInput = { phase?: string | null; v2Phase?: string | null } | string | null | undefined;

function readPhase(input: PhaseInput): string | null {
  if (typeof input === "string") return input;
  if (!input) return null;
  return input.phase ?? input.v2Phase ?? null;
}

/**
 * 「确认简报 / 带假设进入脑暴」是否可见。
 *
 * 背景（UX-D2）：BriefCard 之前只凭 `!isSkipped` 渲染动作区，brainstorming /
 * auditing / audit_completed 阶段仍显示确认按钮，点击必被状态机拒绝
 * （backend/domain/state_machine.py ACTION_PHASES[confirm_brief] 仅 clarifying）。
 */
export function briefActionsVisible(input: PhaseInput): boolean {
  return readPhase(input) === BRIEF_ACTION_PHASE;
}

/** 是否允许编辑并提交修订（revise_brief）；phase 未知时放行，避免旧会话被误锁。 */
export function briefReviseAllowed(input: PhaseInput): boolean {
  const phase = readPhase(input);
  if (!phase) return true;
  return (BRIEF_REVISE_PHASES as readonly string[]).includes(phase);
}

/**
 * 非澄清阶段的只读说明。audit_completed 必须显式说明「保存修订会回退阶段」，
 * 因为 backend/services/command_service.py 的 revise_brief 会把 phase 写回 brainstorming。
 */
export function briefPhaseNotice(input: PhaseInput): string | null {
  const phase = readPhase(input);
  if (!phase || phase === BRIEF_ACTION_PHASE) return null;
  if (phase === "brainstorming") {
    return "已进入脑暴阶段，不再提供「确认简报 / 带假设进入脑暴」；如需修改字段，可在下方编辑并保存修订。";
  }
  if (phase === "auditing") {
    return "审计进行中，简报已锁定，避免修改审计基线；审计结束后可再修订。";
  }
  if (phase === "audit_completed") {
    return "审计已完成，不再提供确认入口。保存修订会把会话阶段回退到「脑暴」，需要重新审计。";
  }
  return "当前阶段不允许确认或修订简报。";
}

/** 服务端版本更新、本地仍有草稿时的提示文案 */
export const BRIEF_STALE_NOTICE = "服务端已更新，是否覆盖或丢弃本地草稿？";

/**
 * 已确认简报里仍停留在「待确认」的字段。
 *
 * 背景：backend/services/command_service.py:1387-1406 的 revise_brief 会按提交的
 * status 写字段（缺省 proposed），同时把 brief.status 强制保持 confirmed —— 于是
 * 头部显示「已确认」、字段旁显示「待确认」，而页面上没有任何再确认入口。
 * 前端现在在已确认简报上提交 status:"confirmed"（BriefCard.changedFields），新数据
 * 不再产生漂移；本函数兜底历史数据，让用户仍能一次性把字段并入已确认版本。
 */
export function briefFieldsAwaitingConfirm(
  brief: ProductBrief | null | undefined
): BriefFieldName[] {
  if (!brief || brief.status !== "confirmed") return [];
  return BRIEF_FIELDS.filter((field) => {
    const status = brief[field]?.status;
    // 缺失字段不算「待确认」：没有内容可并入，提示只会误导
    return status !== undefined && status !== "confirmed" && status !== "unknown";
  });
}

/** 字段漂移提示（配合「确认字段修订」按钮） */
export const BRIEF_FIELD_DRIFT_NOTICE =
  "简报已确认，但以下字段仍停留在「待确认」。点「确认字段修订」把它们并入当前版本，无需重新发起澄清。";

/** 是否存在未提交的本地编辑 */
export function hasBriefEdits(edits: BriefEdits | null | undefined): boolean {
  return Boolean(edits && Object.keys(edits).length > 0);
}

export interface BriefVersionDecision {
  /** true = 保留本地草稿（不要调用 setEdits({})） */
  keepEdits: boolean;
  /** 冲突提示文案（null = 无提示） */
  notice: string | null;
}

/**
 * 服务端 product_version 变化后如何处理本地草稿：
 * - 有草稿 → 保留并提示「覆盖 / 丢弃」，绝不静默清空；
 * - 无草稿 → 允许清空（保持原有行为）。
 */
export function decideBriefDraftOnServerVersion(
  edits: BriefEdits | null | undefined
): BriefVersionDecision {
  if (hasBriefEdits(edits)) {
    return { keepEdits: true, notice: BRIEF_STALE_NOTICE };
  }
  return { keepEdits: false, notice: null };
}

/**
 * 必填字段缺失判定：本地编辑优先于服务端值。
 *
 * 背景：确认简报时 payload.fields 会用 edits 覆盖服务端值，但按钮此前只看服务端
 * brief 的必填状态。用户在编辑态清空必填项后仍能点「确认简报」，把空值提交上去。
 * 这里把「覆盖后的有效值」抽成纯函数，编辑态与提交前都用同一份判断。
 */
export function missingRequiredBriefFields(
  brief: ProductBrief | null | undefined,
  edits: BriefEdits | null | undefined,
  required: readonly BriefFieldName[]
): BriefFieldName[] {
  return required.filter((field) => {
    const edited = edits?.[field];
    const value = typeof edited === "string" ? edited : brief?.[field]?.value;
    return typeof value !== "string" || value.trim().length === 0;
  });
}
