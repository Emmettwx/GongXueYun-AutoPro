import os
import json
import requests

def _extract_json(text):
    """从模型返回文本中稳健提取 JSON 对象：兼容 ```json 包裹、前后废话、单引号等。"""
    import re
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        frag = text[s:e + 1]
        try:
            obj = json.loads(frag)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        fixed = frag.replace(chr(39), chr(34))
        fixed = re.sub(r",\s*}", "}", fixed)
        try:
            obj = json.loads(fixed)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _check_min_length(content, report_type):
    """正文不达配置下限（周报 WEEK_MIN_WORDS=300 / 月报 MONTH_MIN_WORDS=600）视为失败，由调用方中止并通知。"""
    try:
        if report_type == "month":
            min_len = int((os.getenv("MONTH_MIN_WORDS") or "600").strip() or "600")
        else:
            min_len = int((os.getenv("WEEK_MIN_WORDS") or "300").strip() or "300")
    except ValueError:
        min_len = 600 if report_type == "month" else 300
    content = (content or "").strip()
    if len(content) < min_len:
        raise ValueError(f"AI 生成正文仅 {len(content)} 字，低于平台要求的 {min_len} 字，已中止提交")
    return content

def _call_openai_compatible(api_key, base_url, model, prompt):
    """调用 OpenAI 兼容格式接口，返回 (title, content) 或 None。"""
    res = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        timeout=20,
    )
    res.raise_for_status()
    text_out = res.json()["choices"][0]["message"]["content"].strip()
    data = _extract_json(text_out)
    if data and data.get("title") and data.get("content"):
        return data["title"].strip(), data["content"].strip()
    return None


def _call_gemini(api_key, prompt):
    """调用 Google Gemini 原生接口，返回 (title, content) 或 None。"""
    api_url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-2.5-flash:generateContent?key={api_key}")
    res = requests.post(api_url,
                        json={"contents": [{"parts": [{"text": prompt}]}]},
                        headers={"Content-Type": "application/json"}, timeout=20)
    res.raise_for_status()
    text_out = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    data = _extract_json(text_out)
    if data and data.get("title") and data.get("content"):
        return data["title"].strip(), data["content"].strip()
    return None


def generate_report_content(report_type="week", title_hint="", extra_context="", user=None, week_index=None):
    """
    智能生成高质量报告内容：
    AI_PROVIDER 选择供应商（deepseek / openai / qwen / kimi / zhipu / gemini），
    默认 deepseek。密钥读取顺序：对应专用 key -> AI_API_KEY -> GEMINI_API_KEY。
    未配置密钥或调用失败（含重试 2 次后仍失败）时直接抛异常，由调用方中止并邮件通知；正文不足平台下限视为失败。
    """
    company_name = (user.get("address") if user else "") or "实习单位"
    user_desc = (user.get("desc") if user else "") or "日常业务实践"

    provider = (os.getenv("AI_PROVIDER") or "deepseek").strip().lower()
    api_key = (os.getenv("AI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()

    if provider == "gemini":
        api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("AI_API_KEY") or "").strip()
    elif provider == "deepseek":
        api_key = (os.getenv("DEEPSEEK_API_KEY") or api_key).strip()
    elif provider == "openai":
        api_key = (os.getenv("OPENAI_API_KEY") or api_key).strip()
    elif provider == "qwen":
        api_key = (os.getenv("DASHSCOPE_API_KEY") or api_key).strip()
    elif provider == "kimi":
        api_key = (os.getenv("MOONSHOT_API_KEY") or api_key).strip()
    elif provider == "zhipu":
        api_key = (os.getenv("ZHIPU_API_KEY") or api_key).strip()

    COMPAT_DEFAULTS = {
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        "kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
        "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    }

    if api_key:
        try:
            try:
                week_min = int((os.getenv("WEEK_MIN_WORDS") or "300").strip() or "300")
                month_min = int((os.getenv("MONTH_MIN_WORDS") or "600").strip() or "600")
            except ValueError:
                week_min, month_min = 300, 600
            min_words = f"{month_min}~{month_min + 120}" if report_type == "month" else f"{week_min}~{week_min + 100}"
            prompt = (
                f"你是一名在【{company_name}】认真参与【{user_desc}】的大学生实习生。\n"
                f"请帮我撰写一篇真实的工学云【{'周报' if report_type == 'week' else '月报'}】。\n"
                f"要求：\n"
                f"1. 标题简练，如'{title_hint or '实习总结'}'\n"
                f"2. 正文字数必须在 {min_words} 字以上，分 3 段，语言诚恳自然、积极向上，体现学习过程、工作心得和下一步计划；\n"
                f"3. 必须且只输出一个合法的 JSON 对象，不要输出任何解释文字、不要 markdown 代码块，格式为 {{\"title\":\"标题\",\"content\":\"正文\"}}，正文中的换行请用 \\n 表示；\n"
                f"4. 不要出现'作为一个AI'、不要出现占位符，纯中文正文。\n"
                f"补充上下文信息: {extra_context}\n"
                "内容必须紧扣上述岗位工作展开，写具体做了什么、遇到什么问题、如何解决；不同周次/月份侧重不同阶段进展，不要写成通用套话。\n"
            )
            if report_type == "week" and week_index:
                prompt += f"这是实习第{week_index}周的周报，请聚焦本周的阶段进展，与之前周次有明显区别。\n"

            result = None
            last_error = None
            max_attempts = 3  # 首次 + 自动重试 2 次
            for attempt in range(1, max_attempts + 1):
                try:
                    if provider == "gemini":
                        result = _call_gemini(api_key, prompt)
                    else:
                        default_url, default_model = COMPAT_DEFAULTS.get(
                            provider, COMPAT_DEFAULTS["deepseek"]
                        )
                        base_url = os.getenv("AI_BASE_URL") or default_url
                        model = os.getenv("AI_MODEL") or default_model
                        result = _call_openai_compatible(api_key, base_url, model, prompt)

                    if result:
                        checked = _check_min_length(result[1], report_type)
                        if attempt > 1:
                            print(f"[AI_REPORT_INFO] 第 {attempt} 次尝试生成成功", flush=True)
                        return result[0], checked
                    last_error = "API 未返回有效 JSON（title/content 缺失或为空）"
                except Exception as e:
                    last_error = e

                if attempt < max_attempts:
                    wait_sec = 2 * attempt  # 2s、4s 递增退避
                    print(f"[AI_REPORT_WARN] 调用 {provider} API 第 {attempt}/{max_attempts} 次失败: {last_error}，{wait_sec} 秒后重试...", flush=True)
                    import time
                    time.sleep(wait_sec)
                else:
                    print(f"[AI_REPORT_WARN] 调用 {provider} API 连续 {max_attempts} 次失败，停止重试: {last_error}", flush=True)
        except Exception as e:
            last_error = e
            print(f"[AI_REPORT_WARN] 调用 {provider} API 异常: {e}", flush=True)

    # 未配置密钥或调用失败：不再使用内置模板，直接抛出异常由调用方中止并邮件通知
    raise RuntimeError(
        f"[AI_REPORT_ABORT] AI 报告生成失败（供应商={provider}，重试2次后仍失败）：{last_error if 'last_error' in locals() and last_error else '未配置密钥或调用失败'}，已取消本轮报告提交，请检查 Secrets 与日志。"
    )
