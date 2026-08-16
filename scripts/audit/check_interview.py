"""视频面试验证：C036, C063-C079（checklist 第五节）。"""
from __future__ import annotations

from common import FRONTEND, check, read


def run() -> list[tuple[str, bool, str]]:
    print("\n== 视频面试验证（静态） ==")
    page = read(FRONTEND / "app" / "session" / "[id]" / "interview" / "page.tsx")
    iv = read(FRONTEND / "components" / "interview" / "InterviewView.tsx")
    cam = read(FRONTEND / "components" / "interview" / "InterviewCamera.tsx")
    ih = read(FRONTEND / "components" / "interview" / "InterviewHeader.tsx")
    ii = read(FRONTEND / "components" / "interview" / "InterviewInput.tsx")
    rules = read(FRONTEND / "lib" / "proctor-gaze" / "gazeRules.ts")
    cps = read(FRONTEND / "lib" / "proctor-gaze" / "createProctorSession.ts")
    export = read(FRONTEND / "lib" / "proctor-gaze" / "exportGazeFile.ts")
    sr = read(FRONTEND / "hooks" / "useSpeechRecognition.ts")

    check("C036", ("同意" in page and ("仅文字面试" in page or "摄像头" in page)
          and "sessionStorage" in page), "隐私同意门 + 文字面试降级")
    check("C063", ("exportGazeFile" in page or "exportGazeFile" in ih)
          and "结束面试" in ih, "结束面试 + gaze 数据导出")
    check("C064", "MAX_SAMPLES" in cps, "samples ring buffer")
    check("C065", "25" in rules and "20" in rules and "3000" in rules,
          "视线阈值放宽（yaw25/pitch20/3000ms）")
    check("C066", "20" in rules and ("median" in rules.lower() or "中位" in rules
          or "BASELINE_MIN_SAMPLES" in rules), "校准样本数>=20 + 中位数 baseline")
    check("C067", "echoCancellation" in sr and "noiseSuppression" in sr,
          "getUserMedia AEC 约束")
    check("C068", "isPlayingAudio" in ii and "disabled" in ii,
          "TTS 播放期间禁用麦克风")
    check("C069", ("ring-1" in iv or "ring-cyan" in iv) and "语音播放失败" in iv,
          "TTS 字幕高亮 + 失败提示")
    check("C070", 'aria-live' in cam and 'role="alert"' in cam,
          "监考状态 aria 播报 + 错误 alert")
    check("C071", "仅文字面试" in cam or "onSkipCamera" in cam,
          "摄像头失败降级路径")
    check("C072", "delegate" in cps and "CPU" in cam, "GPU/CPU 回退提示")
    check("C073", "capabilities" in iv and ("RTT" in iv or "ms" in iv) and "24ms" not in iv,
          "真实 RTT 显示")
    check("C074", "maxSeconds" in sr or "180" in sr, "录音上限可配置/提升")
    check("C075", "AbortError" in read(FRONTEND / "lib" / "audio.ts"),
          "play() AbortError 处理")
    check("C076", "okRecoverMs" in rules or "800" in rules, "状态恢复滞后防闪烁")
    check("C077", "_meta" in export, "导出文件含 _meta 元数据头")
    check("C078", "结束面试" in ih, "结束面试入口")
    check("C079", 'setStatus("active")' not in cam.split("onPlaying")[1].split("/>")[0]
          if "onPlaying" in cam else True, "onPlaying 不再抢置 active")

    from common import _results
    return _results
