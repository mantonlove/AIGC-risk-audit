#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIGC 触发钩子 —— 在 UserPromptSubmit 阶段识别「降 AIGC」类意图并注入 skill 上下文。

被三个 Agent 共用（同一份脚本，无第三方依赖，用系统 python3 运行）：
  - Claude Code：~/.claude/settings.json 的 hooks.UserPromptSubmit
  - Codex      ：~/.codex/hooks.json   的 hooks.UserPromptSubmit
  - DSH        ：经由上面两个桥接插件（dsh-hooks-claude-code / dsh-hooks-codex）

设计约束（重要）
----------------
1. **只注入上下文，不阻断**（exit 0）。任何异常都静默退出，绝不影响用户提问。
2. 注入文本**不承诺降低检测率，也不预测改写对分数的影响**。
   2026-09-17 的 A 条件只是把人类摘要中的数字和术语尾附到 AI 摘要；
   三个本地模型的均值下降，但每个模型仍有逐篇升分，且没有商业平台验证。
   C 条件历史代码也未真正拆句。细节见 references/edit-effects.md。
3. 同时支持两种方言：Codex（snake_case payload，stdout 无尾换行）与
   Claude Code（hookSpecificOutput.additionalContext）。按 stdin 字段自动判别。

触发逻辑（v2：修正首版的漏触发与误触发）
----------------------------------------
- 主题词：AIGC / AI 率 / AI 检测 / 机器生成 / 论文被判 AI
- 平台词：知网、维普、PaperPass、学习通、超星、大雅 —— **必须与主题词近距离共现**，
  否则「知网查重怎么收费」这类无关提问会被误触发
- 意图词：降 / 降低 / 太高 / 超标 / 怎么办 / 复核 / 申诉 / 自查
- 「复核 / 申诉 / 误判」必须有 AIGC 主题，且有学术语境或明确的 AIGC 检测表述；
  裸词以及普通查重申诉不触发。
- **否定守卫**：出现「不需要 / 不用 / 别 / 只想查…」时**不触发**
- **元任务守卫**：仅当请求是「配置/开发/审计这个 skill 或 hook」时才抑制。
  首版用的无界 `skill.*改` 会吞掉「用这个 skill 改写论文、降低 AI 率」这类真实请求。
"""

import json
import re
import sys

SUBJECT = (r"(?:AIGC|AI\s*率|AI\s*检测|AI\s*生成|人工智能生成|机器生成"
           r"|被判.{0,4}AI|判为.{0,4}AI|疑为\s*AI)")
PLATFORM = r"(?:知网|维普|PaperPass|paperpass|学习通|超星|大雅|Turnitin)"
INTENT = (r"(?:降|降低|下降|压低|太高|过高|超标|怎么办|怎么弄|处理|复核|申诉"
          r"|自查|复检|送检|过不了|不过关|误判|错判|冤枉|被判错)")

SUBJECT_RE = re.compile(SUBJECT, re.IGNORECASE)
ACADEMIC_RE = re.compile(
    r"(?:论文|学术|期刊|毕业|学位|课程作业|投稿|研究报告|查重报告|" + PLATFORM + r")",
    re.IGNORECASE)
DIRECT_AIGC_RE = re.compile(
    r"(?:降|降低|压低)\s*(?:AIGC|AI\s*率)"
    r"|(?:AIGC|AI\s*率)\s*(?:检测|分数|太高|过高|超标|降|降低|复核|申诉|误判)",
    re.IGNORECASE)

TRIGGER_PATTERNS = [
    SUBJECT + r".{0,12}" + INTENT,
    INTENT + r".{0,12}" + SUBJECT,
    r"降\s*AIGC",
    r"降\s*(?:低)?\s*AI\s*率",
    r"(?:降低|压低)\s*(?:AIGC|AI\s*检测)",
    PLATFORM + r".{0,20}" + SUBJECT,
    SUBJECT + r".{0,20}" + PLATFORM,
    r"论文.{0,10}(?:被|判|查).{0,10}(?:AI|AIGC|机器)",
    r"(?:AIGC|AI)\s*率.{0,10}(?:太高|过高|超标|怎么)",
    # 6) 直接请求做检测（"帮我做 AIGC 检测""跑一下 AI 检测"）
    r"(?:做|跑|进行|开展|帮我|帮忙|来个|来个)\s*(?:一下|个)?\s*"
    r"(?:AIGC|AI)\s*(?:检测|查重|检查)",
    r"(?:AIGC|AI)\s*(?:检测|查重)\s*(?:一下|下|吗|？|\?)",
    # 7) 申诉语境仍须经下方 AIGC 主题和学术语境两道检查。
    r"(?:申辩|自证).{0,16}" + SUBJECT,
    SUBJECT + r".{0,16}(?:申辩|自证)",
]
TRIGGER_RE = re.compile("|".join(f"(?:{p})" for p in TRIGGER_PATTERNS), re.IGNORECASE)

# 否定守卫：用户在排除这件事，不是要做这件事
NEGATION_RE = re.compile(
    r"(?:不\s*需要|不用|不必|别|勿|无需|不是要|没打算|只想|只查|仅仅|只是想)"
    r"[^。；;]{0,20}(?:降|AIGC|AI\s*率|AI\s*检测)", re.IGNORECASE)

# 元任务守卫：只抑制「对 skill / hook 本身的配置与开发」
META_RE = re.compile(
    r"(?:配置|安装|卸载|注册|开发|编写|审计|审查|调试|排查|优化|改进|新增|删除|移除)"
    r"[^。；;]{0,16}(?:skill|技能|钩子|hook)"
    r"|(?:skill|技能|钩子|hook)[^。；;]{0,16}"
    r"(?:配置|安装|注册|开发|编写|审计|调试|排查)",
    re.IGNORECASE)

CONTEXT = (
    "检测到「AIGC 检测 / 降 AIGC」类需求，请先读取 skill `aigc-risk-audit`。"
    "处理时注意三条：\n"
    "1) 不承诺任何平台达标。知网/维普/PaperPass/超星学习通是私有黑盒，"
    "本项目没有其接口或校准锚点；不得声称「已降到 X%」或「保证过检」。\n"
    "2) 不代写、不伪装。若目标是把纯 AI 生成稿伪装成原创，说明不能协助，"
    "转为帮助作者建立自己的论证与草稿（见 SKILL.md 开篇）。\n"
    "3) 告知口径边界：2026-09-13 的旧评分版本中，120 篇标记为人类撰写的留出摘要里，"
    "d_hello 将 16 篇标为 AI（13.33%，是多篇文档的二元误判率）。"
    "这不能与单篇论文的疑似字数占比比较，也不能推断商业平台结果。\n"
    "服务方向：写作质量审查（提高可核查性、补作者立场、清理模板化结构）+ 自证存证。"
    "**本 skill 不预测改写对检测分数的影响方向或幅度**，也不以降低检测分数为交付目标。"
)


def detect_dialect(payload):
    """Codex payload 带 turn_id/model；Claude Code 带 hook_event_name。"""
    if isinstance(payload, dict):
        if "turn_id" in payload or "permission_mode" in payload:
            return "codex"
        if "hook_event_name" in payload or "session_id" in payload:
            return "claude"
    return "claude"


def emit(dialect, context):
    if dialect == "codex":
        # Codex：纯 stdout 输出即 additionalContext，不写尾换行
        sys.stdout.write(context)
    else:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }, ensure_ascii=False))


def should_trigger(prompt):
    """返回 (是否触发, 原因)。原因用于测试与排查，不输出给模型。"""
    if not prompt:
        return False, "empty"
    if not TRIGGER_RE.search(prompt):
        return False, "no-trigger-pattern"
    if not SUBJECT_RE.search(prompt):
        return False, "no-aigc-context"
    if not (ACADEMIC_RE.search(prompt) or DIRECT_AIGC_RE.search(prompt)):
        return False, "no-academic-context"
    if NEGATION_RE.search(prompt):
        return False, "negated-intent"
    if META_RE.search(prompt):
        return False, "meta-task"
    return True, "triggered"


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0

    prompt = ""
    if isinstance(payload, dict):
        prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
    ok, _reason = should_trigger(prompt)
    if not ok:
        return 0

    emit(detect_dialect(payload), CONTEXT)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # 钩子绝不阻断用户提问
        sys.exit(0)
