/**
 * 澄清简报草稿与服务端版本更新的冲突判定（M3 修复）
 *
 * 背景：BriefCard 原来在 `productVersion` 变化时无条件 `setEdits({})`，
 * 用户正在编辑的草稿会被一条 `product_state.updated` 静默清空。
 * 这里把「是否保留草稿 / 是否提示冲突」抽成纯函数，便于 `node --test` 覆盖
 * （本机 npm registry 不可达，不能安装 RTL 渲染组件，项目惯例是抽 lib 纯函数）。
 */

import type { BriefFieldName, ProductBrief } from "./contracts.generated";

export type BriefEdits = Record<string, string>;

/** 服务端版本更新、本地仍有草稿时的提示文案 */
export const BRIEF_STALE_NOTICE = "服务端已更新，是否覆盖或丢弃本地草稿？";

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
