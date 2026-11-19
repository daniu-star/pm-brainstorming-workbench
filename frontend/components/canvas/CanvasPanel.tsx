"use client";

import { useMemo, useState } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { CanvasToolbar } from "./CanvasToolbar";
import { PipelineView } from "./PipelineView";
import {
  BrainIcon,
  ChartIcon,
  CrosshairIcon,
  LayersIcon,
  PlayIcon,
  SearchIcon,
  ShieldIcon,
  SignalIcon,
  UserIcon,
  ArrowRightIcon,
} from "@/components/icons";
import { errorMessage, isApiError } from "@/lib/api";
import { decisionActionGate } from "@/lib/phaseRouter";
import { toast } from "@/components/Toast";
import type { CanvasBranch, CanvasNode, DecisionItem } from "@/lib/contracts.generated";

// --------------------------------------------------------------------------- //
// 节点类型元数据（画布投影只做确定性渲染，不调用模型）
// --------------------------------------------------------------------------- //

type IconComponent = (props: { size?: number; className?: string }) => JSX.Element;

interface NodeKindMeta {
  label: string;
  icon: IconComponent;
  tone: string;
}

const NODE_KIND_META: Record<string, NodeKindMeta> = {
  problem: { label: "问题", icon: CrosshairIcon, tone: "text-red-300" },
  user: { label: "用户", icon: UserIcon, tone: "text-sky-300" },
  option: { label: "方案", icon: LayersIcon, tone: "text-brand-300" },
  risk: { label: "风险", icon: ShieldIcon, tone: "text-amber-300" },
  assumption: { label: "假设", icon: BrainIcon, tone: "text-violet-300" },
  question: { label: "问题", icon: SearchIcon, tone: "text-emerald-300" },
  experiment: { label: "实验", icon: PlayIcon, tone: "text-teal-300" },
  decision: { label: "决策", icon: ArrowRightIcon, tone: "text-orange-300" },
  claim: { label: "论断", icon: SignalIcon, tone: "text-zinc-300" },
};

const NODE_STATUS_LABELS: Record<string, string> = {
  proposed: "建议",
  accepted: "采纳",
  confirmed: "已确认",
  rejected: "已否决",
  superseded: "已被取代",
  open: "未解决",
  resolved: "已解决",
  unknown: "未提及",
  planned: "已计划",
  completed: "已完成",
};

const CANVAS_STATUS_LABELS: Record<string, string> = {
  generating: "生成中",
  latest: "最新",
  stale: "过期 (stale)",
  error: "加载失败",
};

const CANVAS_STATUS_CLASSES: Record<string, string> = {
  generating: "text-brand-300 border-brand-500/40 bg-brand-500/10",
  latest: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
  stale: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  error: "text-red-300 border-red-500/40 bg-red-500/10",
};

function sourceCount(node: CanvasNode): number {
  return (
    (node.source_message_ids?.length ?? 0) +
    (node.evidence_refs?.length ?? 0) +
    (node.claim_ids?.length ?? 0) +
    (node.option_ids?.length ?? 0) +
    (node.decision_ids?.length ?? 0) +
    (node.experiment_ids?.length ?? 0)
  );
}

/**
 * PROD-01：画布决策节点的确认入口。
 *
 * 画布投影里决策节点自带 decision_ids（backend/services/canvas_projector.py:207-229，
 * id=`decision:{decision.id}`、status=决策状态），这里按 id 从 productState.decisions
 * 取回完整决策，经 confirm_decision 提交（backend/services/command_service.py:1350）。
 */
function DecisionNodeAction({ node }: { node: CanvasNode }) {
  const { productState, productVersion, v2Phase, sessionView, sendCommand, reloadSession } =
    useSessionStore();
  const [pending, setPending] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [optionId, setOptionId] = useState("");

  const decisionIds = node.decision_ids ?? [];
  const decisions = (productState?.decisions ?? []).filter((item) => decisionIds.includes(item.id));
  // 待确认决策优先；没有待确认项时，已确认但被标记复核（needs_review=True）的决策
  // 仍需给复核入口，否则该标记会永久停留在画布上（command_service.py:1409-1416 置位，
  // command_service.py:1442-1460 只在再次 confirm_decision 时清除）。
  const decision: DecisionItem | null =
    decisions.find((item) => item.status === "proposed") ??
    decisions.find((item) => item.status === "confirmed" && item.needs_review === true) ??
    null;
  const needsReview = decision?.status === "confirmed" && decision.needs_review === true;
  const phase = v2Phase ?? sessionView?.phase ?? null;
  const version = productState?.version ?? productVersion;
  const gate = decisionActionGate({
    status: decision?.status ?? node.status ?? null,
    needsReview: decision?.needs_review ?? false,
    phase,
    productVersion: version,
  });

  const options = productState?.options ?? [];
  const preferred = decision?.option_id ?? null;
  const currentOption = optionId || preferred || "";

  if (!decision || !gate.visible) return null;

  const decisionId = decision.id;
  const sessionVersion = sessionView?.session_version ?? null;

  async function handleConfirm() {
    if (!gate.enabled || pending) return;
    setPending(true);
    setErrorText(null);
    try {
      await sendCommand(
        "confirm_decision",
        {
          expected_product_version: version,
          decision_id: decisionId,
          option_id: currentOption || undefined,
        },
        { expectedSessionVersion: sessionVersion }
      );
      toast.success(needsReview ? "决策已复核并确认" : "决策已确认");
    } catch (error) {
      if (isApiError(error) && error.code === "VERSION_CONFLICT") {
        toast.error("内容已在别处更新，已刷新");
        await reloadSession();
        return;
      }
      const message = errorMessage(error);
      setErrorText(message);
      toast.error(message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mt-2 flex flex-col gap-1.5">
      {needsReview ? (
        <p
          role="note"
          className="rounded-md border border-amber-700/40 bg-amber-900/20 px-2 py-1.5 text-[11px] leading-relaxed text-amber-300"
        >
          {node.detail && node.detail.trim()
            ? node.detail
            : "需要复核：简报必填字段已修订或复核人提出反对，请核对结论与方案选择后确认。"}
          {" "}复核并确认后该标记会被清除。
        </p>
      ) : null}
      {options.length > 1 ? (
        <label className="flex items-center gap-2 text-[11px] text-zinc-500">
          采用方案
          <select
            value={currentOption}
            onChange={(event) => setOptionId(event.target.value)}
            disabled={pending || !gate.enabled}
            className="min-h-[32px] min-w-0 flex-1 rounded-md border border-zinc-700/60 bg-dark-900/70 px-2 py-1 text-[11px] text-zinc-200 focus:border-brand-500/60 focus:outline-none disabled:opacity-60"
          >
            <option value="">不指定具体方案</option>
            {options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.title}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void handleConfirm()}
          disabled={!gate.enabled || pending}
          title={gate.reason ?? undefined}
          className="min-h-[36px] px-3 text-xs font-medium rounded-lg border border-orange-500/40 bg-orange-600/20 text-orange-200 hover:bg-orange-600/30 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
        >
          {pending ? (
            <span className="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin" />
          ) : null}
          {needsReview ? "复核并确认" : "确认决策"}
        </button>
        {gate.reason ? (
          <span className="text-[11px] leading-relaxed text-amber-300">{gate.reason}</span>
        ) : null}
      </div>
      {errorText ? <div className="text-[11px] text-red-300">{errorText}</div> : null}
    </div>
  );
}

function CanvasNodeCard({ node, root = false }: { node: CanvasNode; root?: boolean }) {
  const meta = NODE_KIND_META[node.kind] ?? {
    label: node.kind,
    icon: LayersIcon,
    tone: "text-zinc-300",
  };
  const Icon = meta.icon;
  const statusLabel = NODE_STATUS_LABELS[node.status ?? ""] ?? node.status;
  // 无来源节点 = 模型推测（后端已显式标记时也视为推测）
  const speculative = node.model_speculation === true || sourceCount(node) === 0;

  return (
    <div
      className={`rounded-xl border px-3 py-2.5 ${
        root
          ? "border-brand-500/40 bg-brand-500/10"
          : "border-zinc-700/40 bg-dark-800/50"
      }`}
    >
      <div className="flex items-start gap-2">
        <Icon size={14} className={`mt-0.5 shrink-0 ${meta.tone}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`text-[10px] leading-none px-1.5 py-0.5 rounded border border-zinc-600/40 ${meta.tone}`}>
              {meta.label}
            </span>
            <span className="text-sm font-medium text-zinc-200 break-words">{node.title}</span>
            {statusLabel ? (
              <span className="text-[10px] text-zinc-500">{statusLabel}</span>
            ) : null}
            {speculative ? (
              <span className="text-[10px] leading-none px-1.5 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-300">
                模型推测
              </span>
            ) : null}
          </div>
          {node.detail ? (
            <p className="mt-1.5 text-xs leading-relaxed text-zinc-400 whitespace-pre-wrap">
              {node.detail}
            </p>
          ) : null}
          <>
            {node.evidence_refs?.length ? (
              <div className="mt-2 text-[10px] text-zinc-500">
                证据 {node.evidence_refs.length}
                {node.source_message_ids?.length ? ` · 消息 ${node.source_message_ids.length}` : ""}
                {node.claim_ids?.length ? ` · 论断 ${node.claim_ids.length}` : ""}
              </div>
            ) : node.source_message_ids?.length ? (
              <div className="mt-2 text-[10px] text-zinc-500">消息 {node.source_message_ids.length}</div>
            ) : null}
          </>
          {node.kind === "decision" ? <DecisionNodeAction node={node} /> : null}
        </div>
      </div>
    </div>
  );
}

function CanvasBranchColumn({ branch }: { branch: CanvasBranch }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 mb-2">
        <span className="w-1.5 h-1.5 rounded-full bg-brand-400/70" />
        <h3 className="text-xs font-medium text-zinc-300 truncate">{branch.title || branch.key}</h3>
        <span className="ml-auto text-[10px] text-zinc-600">{branch.nodes?.length ?? 0}</span>
      </div>
      {branch.nodes?.length ? (
        <div className="flex flex-col gap-2">
          {branch.nodes.map((node) => (
            <CanvasNodeCard key={node.id} node={node} />
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-zinc-700/50 px-3 py-3 text-xs text-zinc-600">
          暂无节点
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// v2：确定性画布（product_state.updated → GET /canvas）
// --------------------------------------------------------------------------- //

function CanvasV2() {
  const {
    canvas,
    canvasLoading,
    productVersion,
    isStreaming,
    refreshCanvas,
    stateWarnings,
    clearStateWarnings,
  } = useSessionStore();

  const status = canvas?.status ?? (canvasLoading ? "generating" : "latest");
  const projection = canvas?.projection ?? null;
  const version = canvas?.productVersion ?? (productVersion > 0 ? productVersion : null);
  const isGenerating = status === "generating" || canvasLoading;
  const hasNodes = Boolean(projection?.root) || Boolean(projection?.branches?.some((b) => b.nodes?.length));

  const sortedBranches = useMemo(() => {
    const branches = projection?.branches ?? [];
    return [...branches].sort((a, b) => (a.key > b.key ? 1 : a.key < b.key ? -1 : 0));
  }, [projection]);

  const statusLabel = CANVAS_STATUS_LABELS[status] ?? status;
  const statusClass = CANVAS_STATUS_CLASSES[status] ?? CANVAS_STATUS_CLASSES.latest;
  const canRetry = status === "stale" || status === "error" || (!projection && !isGenerating);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 头部：版本 + 状态徽标 + 重试 */}
      <div className="shrink-0 flex items-center gap-2 px-5 py-3 border-b border-zinc-800/50">
        <BrainIcon size={14} className="text-brand-300" />
        <span className="text-sm text-zinc-400 font-medium">实时画布</span>
        {version ? (
          <span className="text-[10px] text-zinc-500 px-1.5 py-0.5 rounded border border-zinc-700/50">
            v{version}
          </span>
        ) : null}
        <span
          className={`text-[10px] leading-none px-2 py-1 rounded-full border ${statusClass}`}
          title={isGenerating ? "正在读取新的画布投影" : undefined}
        >
          {statusLabel}
        </span>
        {canRetry ? (
          <button
            type="button"
            onClick={() => void refreshCanvas(version ?? undefined)}
            className="ml-auto min-h-[40px] px-3 text-xs font-medium text-amber-200 border border-amber-500/40 rounded-lg bg-amber-500/10 hover:bg-amber-500/20"
          >
            重试
          </button>
        ) : null}
      </div>

      {stateWarnings.length > 0 ? (
        <div className="shrink-0 mx-3 mt-2 flex items-start gap-2 px-3 py-1.5 bg-amber-900/20 border border-amber-800/40 rounded-lg text-[11px] text-amber-300">
          <span className="min-w-0 flex-1">{stateWarnings[stateWarnings.length - 1]}</span>
          <button
            type="button"
            onClick={clearStateWarnings}
            className="shrink-0 rounded px-1 text-amber-200/80 hover:text-amber-100"
            aria-label="关闭告警"
          >
            关闭
          </button>
        </div>
      ) : null}

      {/* 主体 */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {!projection && isGenerating ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="mb-3 w-5 h-5 rounded-full border-2 border-brand-500 border-t-transparent animate-spin" />
            <p className="text-sm text-zinc-400">正在读取画布投影…</p>
          </div>
        ) : !hasNodes ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-6">
            <BrainIcon size={44} className={isStreaming ? "text-brand-400" : "text-zinc-600"} />
            <p className="mt-3 text-sm text-zinc-400">
              {isStreaming ? "正在根据讨论生成画布…" : "画布还是空的"}
            </p>
            <p className="mt-1 text-xs text-zinc-600 leading-relaxed">
              {isStreaming
                ? "产品状态更新后会自动重新读取"
                : "开始讨论后，产品状态的每次提交都会刷新这里"}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {projection?.root ? <CanvasNodeCard node={projection.root} root /> : null}
            {sortedBranches.length > 0 ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
                {sortedBranches.map((branch) => (
                  <CanvasBranchColumn key={branch.key} branch={branch} />
                ))}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// legacy（v2 端点不可用时的旧 incremental 画布，默认不走）
// --------------------------------------------------------------------------- //

function CanvasLegacy() {
  const { canvasTree, messages, isStreaming, isGeneratingCanvas, generateCanvas } = useSessionStore();
  const isEmpty = !canvasTree || !canvasTree.branches?.length;

  if (isEmpty) {
    return (
      <div className="flex-1 flex flex-col min-h-0">
        <CanvasToolbar />
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center px-8 max-w-sm">
            <div className="mb-4 flex justify-center">
              <div className={`transition-all duration-500 ${isStreaming ? "scale-110" : ""}`}>
                <BrainIcon size={48} className={isStreaming ? "text-brand-400" : "text-zinc-500"} />
              </div>
            </div>
            {isStreaming ? (
              <div>
                <p className="text-sm text-zinc-400">正在分析对话内容...</p>
                <div className="mt-3 flex items-center justify-center gap-1">
                  <span className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" />
                  <span
                    className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce"
                    style={{ animationDelay: "0.15s" }}
                  />
                  <span
                    className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce"
                    style={{ animationDelay: "0.3s" }}
                  />
                </div>
              </div>
            ) : messages.length > 0 ? (
              <div>
                <p className="text-sm text-zinc-400 mb-4">讨论进行中。</p>
                <button
                  type="button"
                  onClick={() => void generateCanvas()}
                  disabled={isGeneratingCanvas}
                  className="min-h-[44px] px-5 text-sm font-medium rounded-xl border border-brand-500/40 bg-brand-600/20 text-brand-300 hover:bg-brand-600/30 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
                >
                  {isGeneratingCanvas ? (
                    <>
                      <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
                      生成中...
                    </>
                  ) : (
                    "生成画布"
                  )}
                </button>
              </div>
            ) : (
              <p className="text-sm text-zinc-500 leading-relaxed">
                实时画布
                <br />
                讨论开始后自动生成
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <CanvasToolbar />
      <PipelineView tree={canvasTree} />
    </div>
  );
}

export function CanvasPanel() {
  const legacyMode = useSessionStore((state) => state.legacyMode);
  return legacyMode ? <CanvasLegacy /> : <CanvasV2 />;
}
