"""第 42 批（B5）：/ops 三态 —— 加载中/失败不得伪装成「一切正常」。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")
p = ROOT / "app/ops/page.tsx"
src = p.read_text(encoding="utf-8")

# 1) 区分 loading / error / ready
old = "  const overall = detail?.status ?? null;"
assert src.count(old) == 1
src = src.replace(
    old,
    "  // 第 42 批：detail 为 null 时以前会渲染「Worker 正常 / 排队 0 / 模型 standard」——\n"
    "  // 故障时误导值班判断。这里显式区分 loading / error / ready 三态。\n"
    "  const ready = detail !== null;\n"
    "  const pendingText = busy && !ready ? \"读取中…\" : void 0;\n"
    "  const overall = detail?.status ?? null;",
)

# 2) 失败时不保留上一次的 detail（避免旧数据冒充新数据）
old_fetch = (
    "    } catch (err) {\n"
    "      setLoadError(errorMessage(err));\n"
    "    } finally {\n"
    "      setBusy(false);\n"
    "    }"
)
assert src.count(old_fetch) == 1
src = src.replace(
    old_fetch,
    "    } catch (err) {\n"
    "      setLoadError(errorMessage(err));\n"
    "      // 读取失败时清空 detail：宁可显示「—」，也不能拿上次的成功数据冒充当前状态。\n"
    "      setDetail(null);\n"
    "    } finally {\n"
    "      setBusy(false);\n"
    "    }",
)

# 3) 去掉死分支（errorMessage(401) 返回「请先登录」，不含 "401"）
old_hint = (
    '          {loadError.includes("401") || loadError.includes("未登录") ? "（请先登录再访问 /ops）" : ""}'
)
if src.count(old_hint) == 1:
    src = src.replace(
        old_hint,
        '          {loadError.includes("请先登录") || loadError.includes("登录") ? "（请先登录再访问 /ops）" : ""}',
    )

# 4) 各卡片 badge 在未就绪时显示「—」
replacements = [
    (
        '<Card title="Worker" badge={worker.tone === "red" ? "需要处理" : "正常"} tone={worker.tone}>',
        '<Card\n          title="Worker"\n'
        '          badge={ready ? (worker.tone === "red" ? "需要处理" : "正常") : pendingText ?? "—"}\n'
        "          tone={ready ? worker.tone : \"zinc\"}\n"
        "        >",
    ),
    (
        '<Card title="队列" badge={`排队 ${detail?.queues?.queued ?? 0}`} tone={(detail?.queues?.queued ?? 0) > 0 ? "amber" : "zinc"}>',
        '<Card\n          title="队列"\n'
        '          badge={ready ? `排队 ${detail.queues?.queued ?? 0}` : pendingText ?? "—"}\n'
        '          tone={ready && (detail.queues?.queued ?? 0) > 0 ? "amber" : "zinc"}\n'
        "        >",
    ),
    (
        '<Card title="存储" badge={detail?.storage?.db ? "可读" : "不可用"} tone={detail?.storage?.db ? "zinc" : "red"}>',
        '<Card\n          title="存储"\n'
        '          badge={ready ? (detail.storage?.db ? "可读" : "不可用") : pendingText ?? "—"}\n'
        '          tone={ready && !detail.storage?.db ? "red" : "zinc"}\n'
        "        >",
    ),
    (
        '<Card title="沙箱" badge={detail?.sandbox?.enabled ? "已开启" : "未开启"} tone={sandbox.tone}>',
        '<Card\n          title="沙箱"\n'
        '          badge={ready ? (detail.sandbox?.enabled ? "已开启" : "未开启") : pendingText ?? "—"}\n'
        '          tone={ready ? sandbox.tone : "zinc"}\n'
        "        >",
    ),
    (
        '<Card title="模型" badge={detail?.budget_profile ?? "standard"}>',
        '<Card title="模型" badge={ready ? (detail.budget_profile ?? "standard") : pendingText ?? "—"}>',
    ),
    (
        '<Card title="报告质量" badge={reportQuality.text} tone={reportQuality.tone}>',
        '<Card\n          title="报告质量"\n'
        '          badge={ready ? reportQuality.text : pendingText ?? "—"}\n'
        '          tone={ready ? reportQuality.tone : "zinc"}\n'
        "        >",
    ),
]
for old_card, new_card in replacements:
    assert src.count(old_card) == 1, old_card[:80]
    src = src.replace(old_card, new_card)

p.write_text(src, encoding="utf-8", newline="")
print("patched app/ops/page.tsx")
