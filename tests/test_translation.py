# tests/test_translation.py
"""font_intel.translation 单测（T2.1 + T2.2：三级提供方降级链 + 字幕特化）。

覆盖（全部 mock，绝不真实联网）：
- monkeypatch ``core.llm_client.call_llm``：断言模块所有服务端调用确实经由
  该入口（工作区硬约束：429 有界退避与 LlmApiError 归一都在 llm_client 内）。
- 三级降级链：云端 → 本地 Sakura → VLM；各级未配置/抛 LlmApiError 的组合。
- 429 模拟：全链 LlmApiError → 该批保留原文、调用方不炸（不抛异常）。
- 字幕特化：术语表注入/坏文件降级、上下文窗口组装、行长超限二次缩译、
  输出行数不符保留原文、ASS 换行规范化。
- 批处理：取消检查、并发路径、结果统计（成功批/降级批/提供方分布）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import types

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

import core.llm_client as llm_client  # noqa: E402
from core.llm_client import LlmApiError  # noqa: E402
from font_intel.translation import (  # noqa: E402
    DEFAULT_SAKURA_MODEL,
    DEFAULT_TARGET_LANGUAGE,
    PROVIDER_CLOUD,
    PROVIDER_NONE,
    PROVIDER_SAKURA,
    PROVIDER_VLM,
    TranslationConfig,
    TranslationResult,
    load_glossary,
    translate_subtitle_texts,
)

FOUR = ["こんにちは", "今日はいい天気", "また明日", "ありがとう"]


@pytest.fixture(autouse=True)
def _isolated_vlm_env(monkeypatch):
    """隔离 VLM 通道环境变量：默认三级里 VLM 未配置，测试按需自行 setenv。"""
    for name in ("VLM_REFINE_BASE_URL", "VLM_REFINE_API_KEY", "VLM_REFINE_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _cfg(**kw) -> TranslationConfig:
    kw.setdefault("cloud_api_key", "sk-test")
    kw.setdefault("cloud_base_url", "https://cloud.example.com")
    kw.setdefault("cloud_model", "cloud-model")
    kw.setdefault("batch_size", 2)
    kw.setdefault("max_concurrent_requests", 1)
    return TranslationConfig(**kw)


def _sakura(**kw) -> dict:
    """在默认配置（云端已配置）之上追加本地 Sakura 端点。

    需要"云端未配置"的用例显式传 ``cloud_api_key=""``。
    """
    kw.setdefault("sakura_base_url", "http://127.0.0.1:8080/v1")
    return kw


def _resp(content: str):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


def _arr(*items: str) -> str:
    return json.dumps(list(items), ensure_ascii=False)


_PROVIDER_BASE_HINTS = (
    ("cloud", "cloud.example"),
    ("sakura", "127.0.0.1"),
    ("vlm", "vlm.example"),
)


def _provider_of(base_url) -> str:
    b = str(base_url or "")
    for name, hint in _PROVIDER_BASE_HINTS:
        if hint in b:
            return name
    return "unknown"


def _install_call_llm(monkeypatch, outputs_by_provider: dict) -> dict:
    """整体替换 ``core.llm_client.call_llm``，按提供方分组回放 outputs。

    outputs_by_provider：``{"cloud"/"sakura"/"vlm": [str | Exception, ...]}``，
    按该提供方被调用的次序回放（str = 成功响应内容，Exception = 抛出）；
    某提供方队列耗尽后再收到其调用即 AssertionError（实现多调了没写进
    用例的请求），未列入 dict 的提供方收到调用同样报错。fake 装在
    core.llm_client.call_llm 上：若模块不经由该入口，fake 拦不到、用例即红。
    """
    calls = {
        "n": 0, "messages": [], "kwargs": [], "providers": [],
        "by_provider": {k: list(v) for k, v in outputs_by_provider.items()},
    }

    def fake_call_llm(messages, model="", temperature=1.0, base_url=None,
                      api_key=None, timeout=None, **kw):
        provider = _provider_of(base_url)
        calls["n"] += 1
        calls["messages"].append(messages)
        calls["providers"].append(provider)
        calls["kwargs"].append({
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": temperature,
            "timeout": timeout,
        })
        queue = calls["by_provider"].get(provider)
        if queue is None:
            raise AssertionError(f"unexpected call to unlisted provider {provider}")
        if not queue:
            raise AssertionError(f"unexpected extra call_llm invocation for {provider}")
        out = queue.pop(0)
        if isinstance(out, Exception):
            raise out
        return _resp(out)

    monkeypatch.setattr(llm_client, "call_llm", fake_call_llm)
    return calls


def _user_content(call_messages: list) -> str:
    """取最近一次调用的 user 文本（chat 通道 content 为 str）。"""
    return call_messages[-1][1]["content"]


def _enable_vlm(monkeypatch, base="http://vlm.example:9000/v1", key="vk",
                model="test-vlm"):
    monkeypatch.setenv("VLM_REFINE_BASE_URL", base)
    monkeypatch.setenv("VLM_REFINE_API_KEY", key)
    if model:
        monkeypatch.setenv("VLM_REFINE_MODEL", model)


# ── 配置默认值 ───────────────────────────────────────────────────


class TestConfigDefaults:
    def test_default_target_language(self):
        assert TranslationConfig().target_language == DEFAULT_TARGET_LANGUAGE
        assert DEFAULT_TARGET_LANGUAGE == "简体中文"

    def test_cloud_defaults_reuse_polish_constants(self):
        from core.subtitle_llm_polish import DEFAULT_DEEPSEEK_BASE, DEFAULT_DEEPSEEK_MODEL
        cfg = TranslationConfig()
        assert cfg.cloud_base_url == DEFAULT_DEEPSEEK_BASE
        assert cfg.cloud_model == DEFAULT_DEEPSEEK_MODEL

    def test_sakura_and_batch_defaults(self):
        cfg = TranslationConfig()
        assert cfg.sakura_base_url == ""  # 默认未配置
        assert cfg.sakura_model == DEFAULT_SAKURA_MODEL
        assert cfg.context_window_lines >= 1
        assert cfg.max_concurrent_requests >= 1
        assert cfg.batch_size >= 1


# ── 短路：空输入 / 三级全未配置 ──────────────────────────────────


class TestShortCircuit:
    def test_empty_input_no_llm_call(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {})
        res = translate_subtitle_texts([], _cfg())
        assert isinstance(res, TranslationResult)
        assert res.texts == []
        assert res.stats.total_lines == 0
        assert calls["n"] == 0

    def test_no_provider_configured_keeps_originals_no_network(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {})
        cfg = _cfg(cloud_api_key="  ", sakura_base_url="")
        res = translate_subtitle_texts(FOUR, cfg)
        assert calls["n"] == 0  # 绝不触网
        assert res.texts == FOUR
        assert res.stats.batches_total == 2
        assert res.stats.batches_success == 0
        assert res.stats.batches_failed == 2
        assert all(r.reason == "no_provider_configured" for r in res.stats.batches)
        assert all(r.provider == PROVIDER_NONE for r in res.stats.batches)


# ── 三级降级链 ───────────────────────────────────────────────────


class TestDegradationChain:
    def test_cloud_success_skips_lower_levels(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {"cloud": [
            _arr("你好", "今天天气真好"),
            _arr("明天见", "谢谢"),
        ]})
        res = translate_subtitle_texts(FOUR, _cfg())
        assert res.texts == ["你好", "今天天气真好", "明天见", "谢谢"]
        assert calls["n"] == 2
        assert all(k["base_url"] == "https://cloud.example.com" for k in calls["kwargs"])
        assert all(k["model"] == "cloud-model" for k in calls["kwargs"])
        assert res.stats.provider_batches == {PROVIDER_CLOUD: 2}
        assert res.stats.batches_degraded == 0

    def test_cloud_unconfigured_falls_to_sakura(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {"sakura": [
            _arr("你好", "今天天气真好"),
            _arr("明天见", "谢谢"),
        ]})
        res = translate_subtitle_texts(FOUR, _cfg(cloud_api_key="", **_sakura()))
        assert res.texts[0] == "你好"
        assert calls["n"] == 2
        assert all(k["base_url"] == "http://127.0.0.1:8080/v1" for k in calls["kwargs"])
        assert all(k["model"] == DEFAULT_SAKURA_MODEL for k in calls["kwargs"])
        # 本地端点无鉴权也必须能路由：api_key 需为非空占位（call_llm 硬要求）
        assert all(k["api_key"] for k in calls["kwargs"])
        assert res.stats.provider_batches == {PROVIDER_SAKURA: 2}

    def test_cloud_llm_api_error_falls_to_sakura(self, monkeypatch):
        err = LlmApiError("429 after bounded retries")
        calls = _install_call_llm(monkeypatch, {
            "cloud": [err, err],           # 两批云端都炸
            "sakura": [_arr("你好"), _arr("明天见")],  # 本地接住
        })
        cfg = _cfg(**_sakura(), batch_size=1)
        res = translate_subtitle_texts(FOUR[:2], cfg)
        assert res.texts == ["你好", "明天见"]
        assert calls["n"] == 4
        assert [k["base_url"] for k in calls["kwargs"]] == [
            "https://cloud.example.com", "http://127.0.0.1:8080/v1",
        ] * 2
        assert res.stats.batches_success == 2
        assert res.stats.batches_degraded == 2  # 均由降级层完成
        assert res.stats.provider_batches == {PROVIDER_SAKURA: 2}

    def test_sakura_unconfigured_falls_to_vlm_env(self, monkeypatch):
        _enable_vlm(monkeypatch)
        calls = _install_call_llm(monkeypatch, {"vlm": [_arr("你好", "今天天气真好")]})
        cfg = _cfg(cloud_api_key="", sakura_base_url="")
        res = translate_subtitle_texts(FOUR[:2], cfg)
        assert res.texts == ["你好", "今天天气真好"]
        assert calls["n"] == 1
        k = calls["kwargs"][0]
        assert k["base_url"] == "http://vlm.example:9000/v1"
        assert k["api_key"] == "vk"
        assert k["model"] == "test-vlm"
        assert res.stats.provider_batches == {PROVIDER_VLM: 1}

    def test_vlm_model_env_default(self, monkeypatch):
        from core.vlm_refine import DEFAULT_VLM_MODEL
        _enable_vlm(monkeypatch, model="")
        calls = _install_call_llm(monkeypatch, {"vlm": [_arr("你好")]})
        translate_subtitle_texts(["こんにちは"], _cfg(cloud_api_key="", sakura_base_url=""))
        assert calls["kwargs"][0]["model"] == DEFAULT_VLM_MODEL

    def test_output_invalid_at_one_level_falls_to_next(self, monkeypatch):
        # 云端返回行数不符（批 1 行=1）→ 视为该级失败 → 降到本地成功
        calls = _install_call_llm(monkeypatch, {
            "cloud": [_arr("多", "出来的行")],  # 云端：长度 2 != 1
            "sakura": [_arr("你好")],           # 本地：成功
        })
        res = translate_subtitle_texts(["こんにちは"], _cfg(**_sakura(), batch_size=1))
        assert res.texts == ["你好"]
        assert calls["n"] == 2
        assert res.stats.provider_batches == {PROVIDER_SAKURA: 1}

    def test_generic_exception_at_one_level_falls_to_next(self, monkeypatch):
        # 空响应 ValueError 等未归一异常也不得击穿降级链
        calls = _install_call_llm(monkeypatch, {
            "cloud": [ValueError("Invalid API response: empty choices")],
            "sakura": [_arr("你好")],
        })
        res = translate_subtitle_texts(["こんにちは"], _cfg(**_sakura(), batch_size=1))
        assert res.texts == ["你好"]
        assert calls["n"] == 2

    def test_all_levels_fail_batch_keeps_original(self, monkeypatch):
        _enable_vlm(monkeypatch)
        err = LlmApiError("exhausted")
        calls = _install_call_llm(monkeypatch, {
            "cloud": [err], "sakura": [err], "vlm": [err],  # 三级各一次全炸
        })
        cfg = _cfg(**_sakura(), batch_size=1)
        res = translate_subtitle_texts(["こんにちは"], cfg)
        assert res.texts == ["こんにちは"]  # 保留原文
        assert calls["n"] == 3
        assert res.stats.batches_failed == 1
        assert res.stats.batches[0].provider == PROVIDER_NONE
        assert res.stats.batches[0].reason == "llm_api_error"


# ── 429 有界退避耗尽：保留原文且调用方不炸（工作区硬约束） ────────


class Test429Degradation:
    def test_exhaustion_keeps_originals_and_never_raises(self, monkeypatch):
        _enable_vlm(monkeypatch)
        err = LlmApiError(
            "LLM API call failed after bounded retries: RateLimitError: 429"
        )
        calls = _install_call_llm(monkeypatch, {
            "cloud": [err] * 2, "sakura": [err] * 2, "vlm": [err] * 2,  # 2 批 × 3 级全炸
        })
        cfg = _cfg(**_sakura(), batch_size=1)
        res = translate_subtitle_texts(FOUR[:2], cfg)  # 不抛异常即为过
        assert res.texts == FOUR[:2]
        assert calls["n"] == 6
        assert res.stats.batches_failed == 2
        assert res.stats.batches_success == 0


# ── 字幕特化：术语表 ─────────────────────────────────────────────


class TestGlossary:
    def test_glossary_injected_into_prompt(self, monkeypatch, tmp_path):
        calls = _install_call_llm(monkeypatch, {"cloud": [_arr("你好，御主")]})
        path = tmp_path / "glossary.json"
        path.write_text(
            json.dumps({"セイバー": "Saber", "マスター": "御主"}, ensure_ascii=False),
            encoding="utf-8",
        )
        res = translate_subtitle_texts(
            ["こんにちは、マスター"], _cfg(batch_size=1, glossary_path=str(path))
        )
        assert res.texts == ["你好，御主"]
        user = _user_content(calls["messages"])
        assert "セイバー" in user and "Saber" in user
        assert "マスター" in user and "御主" in user
        assert "术语" in user

    def test_glossary_missing_file_warns_and_degrades(self, monkeypatch, tmp_path, caplog):
        calls = _install_call_llm(monkeypatch, {"cloud": [_arr("你好")]})
        with caplog.at_level(logging.WARNING):
            res = translate_subtitle_texts(
                ["こんにちは"],
                _cfg(batch_size=1, glossary_path=str(tmp_path / "nope.json")),
            )
        assert res.texts == ["你好"]
        assert "术语表" in caplog.text
        assert "セイバー" not in _user_content(calls["messages"])

    def test_glossary_bad_json_warns_and_degrades(self, monkeypatch, tmp_path, caplog):
        calls = _install_call_llm(monkeypatch, {"cloud": [_arr("你好")]})
        path = tmp_path / "glossary.json"
        path.write_text("{oops", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            res = translate_subtitle_texts(
                ["こんにちは"], _cfg(batch_size=1, glossary_path=str(path))
            )
        assert res.texts == ["你好"]
        assert "术语表" in caplog.text
        assert "セイバー" not in _user_content(calls["messages"])

    def test_load_glossary_non_dict_degrades(self, tmp_path, caplog):
        path = tmp_path / "glossary.json"
        path.write_text('["a", "b"]', encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            assert load_glossary(str(path)) == {}

    def test_load_glossary_empty_path_no_warning(self):
        assert load_glossary("") == {}


# ── 字幕特化：上下文窗口 ─────────────────────────────────────────


class TestContextWindow:
    def test_context_lines_present_with_note(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {"cloud": [
            _arr("你好", "今天天气真好"),
            _arr("明天见", "谢谢"),
            _arr("晚安", "再见"),
        ]})
        cfg = _cfg(batch_size=2, context_window_lines=2)
        all6 = FOUR + ["おやすみ", "じゃあね"]
        res = translate_subtitle_texts(all6, cfg)
        assert len(res.texts) == 6
        assert calls["n"] == 3

        # 批 2（目标行 [2],[3]）：前文含行 0/1，后文含行 4/5
        user2 = calls["messages"][1][1]["content"]
        assert FOUR[0] in user2 and FOUR[1] in user2   # 前文（不可能来自目标行）
        assert all6[4] in user2 and all6[5] in user2   # 后文
        assert FOUR[2] in user2 and FOUR[3] in user2   # 目标行
        assert "仅供参考" in user2
        assert "只翻译目标行" in user2

        # 批 1（目标行 [0],[1]）：无前文，后文含行 2/3
        user1 = calls["messages"][0][1]["content"]
        assert FOUR[2] in user1 and FOUR[3] in user1

        # 批 3（目标行 [4],[5]）：前文含行 2/3（窗口 N=2），无后文
        user3 = calls["messages"][2][1]["content"]
        assert FOUR[2] in user3 and FOUR[3] in user3
        assert FOUR[1] not in user3  # 前文窗口只到 N=2 行

    def test_context_zero_disables_window(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {"cloud": [_arr("你好", "今天天气真好")]})
        translate_subtitle_texts(FOUR, _cfg(batch_size=2, context_window_lines=0))
        user1 = calls["messages"][0][1]["content"]  # 批 1（目标行 0/1）
        assert "仅供参考" not in user1
        assert "只翻译目标行" not in user1
        assert FOUR[2] not in user1  # 后文窗口关闭（否则行 2 会作为后文出现）


# ── 字幕特化：行长约束与缩译 ─────────────────────────────────────


class TestLineLengthLimit:
    def test_overlong_line_triggers_one_shorten_retry(self, monkeypatch):
        long_text = "这一段译文实在过于冗长远远超出了六字的限制"
        calls = _install_call_llm(monkeypatch, {"cloud": [
            _arr(long_text),  # 批 1 首译：超长
            _arr("短译"),     # 批 1 缩译成功
            _arr("好"),       # 批 2：不超长，无缩译
        ]})
        cfg = _cfg(batch_size=1, max_line_chars=6)
        res = translate_subtitle_texts(["セリフ壱", "セリフ弐"], cfg)
        assert res.texts == ["短译", "好"]
        assert calls["n"] == 3
        shorten_user = calls["messages"][1][1]["content"]
        assert "缩译" in shorten_user
        assert long_text in shorten_user
        assert "6" in shorten_user
        assert res.stats.batches[0].shortened_lines == 1
        assert res.stats.batches[1].shortened_lines == 0
        assert res.stats.shortened_lines == 1

    def test_shorten_failure_keeps_first_pass(self, monkeypatch):
        long_text = "这一段译文实在过于冗长远远超出了六字的限制"
        calls = _install_call_llm(monkeypatch, {"cloud": [
            _arr(long_text),
            LlmApiError("429 on shorten"),
        ]})
        res = translate_subtitle_texts(["セリフ壱"], _cfg(batch_size=1, max_line_chars=6))
        assert res.texts == [long_text]  # 缩译失败保留首次译文
        assert calls["n"] == 2
        assert res.stats.batches[0].ok is True
        assert res.stats.batches[0].shortened_lines == 0

    def test_shorten_invalid_output_keeps_first_pass(self, monkeypatch):
        long_text = "这一段译文实在过于冗长远远超出了六字的限制"
        calls = _install_call_llm(monkeypatch, {"cloud": [
            _arr(long_text),
            _arr("短了", "多了的"),  # 行数不符 → 缩译作废
        ]})
        res = translate_subtitle_texts(["セリフ壱"], _cfg(batch_size=1, max_line_chars=6))
        assert res.texts == [long_text]
        assert calls["n"] == 2

    def test_limit_disabled_skips_shorten(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {"cloud": [_arr("超长" * 50)]})
        res = translate_subtitle_texts(["セリフ"], _cfg(batch_size=1, max_line_chars=0))
        assert calls["n"] == 1
        assert res.stats.shortened_lines == 0


# ── 输出校验 ─────────────────────────────────────────────────────


class TestOutputValidation:
    def test_wrong_length_at_all_levels_keeps_original(self, monkeypatch):
        _enable_vlm(monkeypatch)
        calls = _install_call_llm(monkeypatch, {
            "cloud": [_arr("甲", "乙")],   # 云端：长度 2 != 1
            "sakura": [_arr("丙", "丁")],  # 本地：长度 2 != 1
            "vlm": [_arr("戊", "己")],     # VLM：长度 2 != 1
        })
        res = translate_subtitle_texts(["こんにちは"], _cfg(**_sakura(), batch_size=1))
        assert res.texts == ["こんにちは"]
        assert calls["n"] == 3
        assert res.stats.batches[0].reason == "llm_output_invalid"

    def test_non_string_items_invalid(self, monkeypatch):
        _install_call_llm(monkeypatch, {
            "cloud": [json.dumps([1, 2])],  # 非字符串数组
            "sakura": [_arr("你好")],
        })
        res = translate_subtitle_texts(["こんにちは"], _cfg(**_sakura(), batch_size=1))
        assert res.texts == ["你好"]

    def test_code_fence_is_tolerated(self, monkeypatch):
        _install_call_llm(monkeypatch, {
            "cloud": ["```json\n" + _arr("你好") + "\n```"],
        })
        res = translate_subtitle_texts(["こんにちは"], _cfg(batch_size=1))
        assert res.texts == ["你好"]

    def test_json_repair_fallback(self, monkeypatch):
        pytest.importorskip("json_repair")
        _install_call_llm(monkeypatch, {"cloud": ['["你好",]']})  # 尾逗号可被修复
        res = translate_subtitle_texts(["こんにちは"], _cfg(batch_size=1))
        assert res.texts == ["你好"]

    def test_ass_newline_normalized(self, monkeypatch):
        _install_call_llm(monkeypatch, {"cloud": [
            _arr("第一行\\n第二行"),           # 字面量 \n
            _arr("第三行\n第四行"),            # 真实换行
        ]})
        res = translate_subtitle_texts(["a", "b"], _cfg(batch_size=1))
        assert res.texts == ["第一行\\N第二行", "第三行\\N第四行"]


# ── VLM 通道消息构造 ─────────────────────────────────────────────


class TestVlmChannel:
    def test_vlm_message_carries_image_parts(self, monkeypatch):
        pytest.importorskip("cv2")
        np = pytest.importorskip("numpy")
        _enable_vlm(monkeypatch)
        calls = _install_call_llm(monkeypatch, {"vlm": [_arr("你好")]})
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        cfg = _cfg(cloud_api_key="", sakura_base_url="")
        res = translate_subtitle_texts(["こんにちは"], cfg, frame_images=[frame])
        assert res.texts == ["你好"]
        content = calls["messages"][0][1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert any(
            p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/jpeg;base64,")
            for p in content
        )

    def test_vlm_without_images_text_only_content(self, monkeypatch):
        _enable_vlm(monkeypatch)
        calls = _install_call_llm(monkeypatch, {"vlm": [_arr("你好")]})
        cfg = _cfg(cloud_api_key="", sakura_base_url="")
        res = translate_subtitle_texts(["こんにちは"], cfg)
        assert res.texts == ["你好"]
        content = calls["messages"][0][1]["content"]
        parts = content if isinstance(content, list) else [{"type": "text"}]
        assert all(p["type"] == "text" for p in parts)


# ── 批处理骨架：取消 / 并发 / 进度 ───────────────────────────────


class TestBatchSkeleton:
    def test_cancel_stops_remaining_batches(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {"cloud": [_arr("你好", "今天天气真好")]})
        progress: list = []
        state = {"done": 0}

        def on_batch_done(done, total):
            state["done"] = done
            progress.append((done, total))

        res = translate_subtitle_texts(
            FOUR, _cfg(batch_size=2), cancel_check=lambda: state["done"] >= 1,
            on_batch_done=on_batch_done,
        )
        assert calls["n"] == 1  # 批 2 未发起任何请求
        assert res.texts[:2] == ["你好", "今天天气真好"]
        assert res.texts[2:] == FOUR[2:]  # 保留原文
        assert progress == [(1, 2)]
        assert res.stats.batches_attempted == 1
        assert res.stats.batches_total == 2

    def test_cancel_before_start_no_calls(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {})
        res = translate_subtitle_texts(FOUR, _cfg(batch_size=2), cancel_check=lambda: True)
        assert calls["n"] == 0
        assert res.texts == FOUR
        assert res.stats.batches_attempted == 0

    def test_concurrent_path_all_batches_translate(self, monkeypatch):
        calls = _install_call_llm(monkeypatch, {"cloud": [
            _arr("你好"), _arr("明天见"), _arr("谢谢"),
        ]})
        progress: list = []
        res = translate_subtitle_texts(
            FOUR[:3],
            _cfg(batch_size=1, max_concurrent_requests=3),
            on_batch_done=lambda done, total: progress.append((done, total)),
        )
        assert res.texts == ["你好", "明天见", "谢谢"]
        assert calls["n"] == 3
        assert sorted(progress) == [(1, 3), (2, 3), (3, 3)]

    def test_single_batch_failure_does_not_affect_others(self, monkeypatch):
        err = LlmApiError("429")
        _install_call_llm(monkeypatch, {
            "sakura": [_arr("你好"), err],  # 批 1 正常；批 2 本地炸且无更底层 → 保留原文
        })
        res = translate_subtitle_texts(
            FOUR[:2], _cfg(cloud_api_key="", **_sakura(), batch_size=1, max_concurrent_requests=2)
        )
        assert res.texts == ["你好", FOUR[1]]
        assert res.stats.batches_success == 1
        assert res.stats.batches_failed == 1


# ── 结果统计 ─────────────────────────────────────────────────────


class TestStats:
    def test_mixed_outcome_stats(self, monkeypatch):
        _enable_vlm(monkeypatch)
        err = LlmApiError("429")
        _install_call_llm(monkeypatch, {
            "cloud": [_arr("你好", "今天天气真好"), err],  # 批 1 成功；批 2 炸
            "sakura": [err],                                # 批 2 降级也炸
            "vlm": [err],                                   # 批 2 VLM 也炸
        })
        res = translate_subtitle_texts(FOUR, _cfg(**_sakura()))
        st = res.stats
        assert st.total_lines == 4
        assert st.batches_total == 2
        assert st.batches_success == 1
        assert st.batches_failed == 1
        assert st.batches_attempted == 2
        assert st.provider_batches == {PROVIDER_CLOUD: 1}
        assert st.batches[0].provider == PROVIDER_CLOUD and st.batches[0].ok
        assert st.batches[1].provider == PROVIDER_NONE and not st.batches[1].ok
        assert st.batches[1].reason == "llm_api_error"

    def test_result_texts_always_match_input_length(self, monkeypatch):
        _enable_vlm(monkeypatch)
        _install_call_llm(monkeypatch, {"vlm": [LlmApiError("x")] * 2})
        res = translate_subtitle_texts(FOUR, _cfg(cloud_api_key="", sakura_base_url=""))
        assert len(res.texts) == len(FOUR)
