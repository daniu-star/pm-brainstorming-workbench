/**
 * 纯状态守卫函数（无 React / 无 zustand / 无 I/O）
 *
 * 这些函数是 store 的核心不变量，抽出来是为了让 `node --test` 能直接覆盖
 * （`frontend/tests/store.test.cjs` 通过 tsc 编译产物 require 本文件）：
 * - 消息按 `id`（= message_id）去重：同 id **替换**，不追加；
 * - 事件按 `run_id + event_seq` 去重；
 * - `product_version` 单调：旧版本丢弃并给出 warning；
 * - 会话 generation 守卫：只有当前 `session_id` + `generation` 的异步结果可以写回。
 */

export interface SessionGuard {
  sessionId: string | null;
  generation: number;
}

/** 只有 session_id 与 generation 都一致时，异步结果才允许写回。 */
export function isGuardCurrent(current: SessionGuard, expected: SessionGuard): boolean {
  return current.sessionId === expected.sessionId && current.generation === expected.generation;
}

// --------------------------------------------------------------------------- //
// 消息去重
// --------------------------------------------------------------------------- //

export interface MessageLike {
  id: string;
  seq?: number;
}

function hasNumericSeq(item: { seq?: number }): boolean {
  return typeof item.seq === "number" && Number.isFinite(item.seq);
}

/**
 * 合并消息列表：
 * - 已存在相同 id → 用 incoming 覆盖（保持原有相对顺序）
 * - 新 id → 追加
 * - 所有条目都有 seq 时按 seq 升序稳定排序
 */
export function mergeMessagesById<T extends MessageLike>(
  existing: readonly T[],
  incoming: readonly T[]
): { items: T[]; added: number; replaced: number } {
  const items: T[] = existing.slice();
  const indexById = new Map<string, number>();
  items.forEach((item, index) => indexById.set(item.id, index));

  let added = 0;
  let replaced = 0;

  for (const message of incoming) {
    const index = indexById.get(message.id);
    if (index === undefined) {
      indexById.set(message.id, items.length);
      items.push(message);
      added += 1;
    } else {
      items[index] = message;
      replaced += 1;
    }
  }

  if (items.length > 0 && items.every(hasNumericSeq)) {
    // Array.prototype.sort 在 ES2019+ 稳定；这里显式带 id 兜底以保证确定性
    items.sort((a, b) => {
      const delta = (a.seq as number) - (b.seq as number);
      if (delta !== 0) return delta;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  }

  return { items, added, replaced };
}

export function upsertMessageById<T extends MessageLike>(
  existing: readonly T[],
  message: T
): { items: T[]; added: boolean } {
  const merged = mergeMessagesById(existing, [message]);
  return { items: merged.items, added: merged.added === 1 };
}

// --------------------------------------------------------------------------- //
// product_version 单调
// --------------------------------------------------------------------------- //

export interface VersionAcceptance {
  /** 是否接受该版本（>= 当前版本） */
  accepted: boolean;
  /** 接受后应当使用的版本号 */
  version: number;
  /** 是否是被丢弃的旧版本 */
  stale: boolean;
  /** 需要记录到 store 的 warning（stale 时非空） */
  warning: string | null;
}

/**
 * 版本单调守卫：只接受 `incoming >= current`。
 * - 相等视为幂等重放，接受但不产生 warning；
 * - 小于则丢弃并返回 warning（PRD：旧版本不得覆盖新版本）。
 */
export function acceptProductVersion(current: number, incoming: number): VersionAcceptance {
  const normalizedCurrent = Number.isFinite(current) && current > 0 ? Math.floor(current) : 0;
  const normalizedIncoming = Number.isFinite(incoming) ? Math.floor(incoming) : 0;

  if (normalizedIncoming < 1) {
    return {
      accepted: false,
      version: normalizedCurrent,
      stale: true,
      warning: `忽略非法 product_version=${incoming}`,
    };
  }
  if (normalizedIncoming < normalizedCurrent) {
    return {
      accepted: false,
      version: normalizedCurrent,
      stale: true,
      warning: `丢弃旧版本 product_version=${normalizedIncoming}（当前 ${normalizedCurrent}）`,
    };
  }
  return { accepted: true, version: normalizedIncoming, stale: false, warning: null };
}

/**
 * `loop_version` 单调：闭环快照由多个来源并发写回（每条 `loop.updated` 事件、
 * `finalizeRun` 复拉、决策后主动刷新），响应乱序时旧快照会把 status 从
 * `awaiting_decision` 改回 `running`，导致决策三键全部禁用（「当前轮仍在执行」），
 * 甚至把 currentLoop 指针改回上一个历史闭环。语义与 `acceptProductVersion` 一致：
 * 版本回退一律丢弃并给出 warning，指针只在被接受时移动。
 */
export function acceptLoopVersion(current: number, incoming: number): VersionAcceptance {
  const normalizedCurrent = Number.isFinite(current) && current > 0 ? Math.floor(current) : 0;
  const normalizedIncoming = Number.isFinite(incoming) ? Math.floor(incoming) : 0;

  if (normalizedIncoming < 1) {
    return {
      accepted: false,
      version: normalizedCurrent,
      stale: true,
      warning: `忽略非法 loop_version=${incoming}`,
    };
  }
  if (normalizedCurrent > 0 && normalizedIncoming < normalizedCurrent) {
    return {
      accepted: false,
      version: normalizedCurrent,
      stale: true,
      warning: `丢弃旧版本 loop_version=${normalizedIncoming}（当前 ${normalizedCurrent}）`,
    };
  }
  return { accepted: true, version: normalizedIncoming, stale: false, warning: null };
}

/** 是否需要为新的 product_version 重新拉取画布。 */
export function shouldRefetchCanvas(
  currentCanvasVersion: number | null | undefined,
  incomingProductVersion: number
): boolean {
  if (!Number.isFinite(incomingProductVersion) || incomingProductVersion < 1) return false;
  if (currentCanvasVersion === null || currentCanvasVersion === undefined) return true;
  return incomingProductVersion > currentCanvasVersion;
}

// --------------------------------------------------------------------------- //
// 事件去重
// --------------------------------------------------------------------------- //

export function eventDedupeKey(runId: string, eventSeq: number): string {
  return `${runId}:${eventSeq}`;
}

/** true = 首次见到（需要处理）；false = 重复事件，应丢弃。 */
export function isNewEvent(seen: Set<string>, runId: string, eventSeq: number): boolean {
  const key = eventDedupeKey(runId, eventSeq);
  if (seen.has(key)) return false;
  seen.add(key);
  return true;
}

// --------------------------------------------------------------------------- //
// 一次性守卫（终结事件 / onDone）
// --------------------------------------------------------------------------- //

/** 返回一个 once 守卫：第一次调用返回 true，之后都返回 false。 */
export function createOnceGuard(): () => boolean {
  let fired = false;
  return () => {
    if (fired) return false;
    fired = true;
    return true;
  };
}
