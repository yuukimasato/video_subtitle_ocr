# tests/test_translation_pipeline.py
"""T2.3 翻译接入主流水线（阶段 4，主进程）——架构红线断言 + 集成测试。

覆盖（全部 mock，绝不真实联网）：
1. 架构红线：翻译只挂主进程阶段 4——
   - ``pipeline_stages.PipelineContext``（chunk worker 的唯一进程间载荷参数）
     没有任何翻译字段；
   - chunk 路径模块（chunk_worker / chunk_parallel_runner / chunk_merger /
     pipeline_stages）源代码不引用 font_intel.translation；
   - ``PipelineWorker`` 接受 ``translation_config`` 但 ``_stage_ctx()``
     （阶段 1-3 的 picklable 参数包）不携带它。
2. 生成器接入缝：``OCRToASSOptimizer.convert_from_memory`` 在润色之后、
   事件写出之前执行翻译——**原文行转 Comment 隐藏、译文行以 Dialogue
   写出**（时间/样式/标签逐项一致；译文与原文相同 = 失败/降级不拆分）；
   翻译入口抛异常/返回长度不符时保留原文出片不中断；
   ``translation_config=None`` 与不传参数逐字节一致且翻译入口绝不被调用
   （canary 假模块守在 sys.modules）。
2.5 轨迹事件翻译：移动文字轨迹事件（Name=motion）此前完全绕过翻译——
   现与静态行同样送翻并做 Comment/Dialogue 拆分；worker 产出的轨迹事件
   携带 roi 供源语言路由；策略遮罩/mask_only Comment 行不送翻。
3. 双语路由：行文本按所属 ROI 的 ``ocr_lang`` 分组送翻，源语言提示写入
   翻译配置；未配置 ocr_lang 的 ROI 回退自动检测（source_language=""）。
4. CLI 贯通：``--translate`` 关闭时配置为 None（零行为变化）；开启时
   ``--translate-*`` 参数映射到 ``TranslationConfig``；云端 key 复用
   OPENAI_API_KEY 环境变量（llm_client 的既有缺省来源）。
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import sys
import types
from dataclasses import fields as dc_fields

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

import cli  # noqa: E402
import font_intel.translation as tr  # noqa: E402
from core.subtitle_generator.generator import OCRToASSOptimizer  # noqa: E402
from font_intel.translation import TranslationConfig  # noqa: E402


# ── 构造辅助（对齐 test_compliance_styling 的最小合成数据） ────────


def _make_optimizer(tmp_path, **kw) -> OCRToASSOptimizer:
    return OCRToASSOptimizer(
        video_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.ass"),
        fps=25.0,
        width=1280,
        height=720,
        **kw,
    )


def _make_item(frame_num, text, box, roi="roi_0"):
    poly = [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]
    data = {
        "dt_polys": [poly],
        "rec_polys": [poly],
        "rec_texts": [text],
        "rec_scores": [0.95],
        "rec_boxes": [list(box)],
    }
    return (data, frame_num, roi, frame_num / 25.0)


def _items(text="学成归来", roi="roi_0"):
    # 底部区域（y_center 625 > 720*0.75）→ BOTTOM 组 → 一条对白事件。
    return [_make_item(f, text, (100, 600, 500, 650), roi=roi)
            for f in range(10, 21)]


def _read_out(tmp_path) -> str:
    return (tmp_path / "out.ass").read_text(encoding="utf-8-sig")


@pytest.fixture()
def _no_vlm(monkeypatch):
    """隔离 VLM 通道：默认三级提供方链里 VLM 不因本机环境变量误入。"""
    for name in ("VLM_REFINE_BASE_URL", "VLM_REFINE_API_KEY", "VLM_REFINE_MODEL"):
        monkeypatch.delenv(name, raising=False)


# ── 1. 架构红线：翻译只在主进程阶段 4 ────────────────────────────


class TestArchitectureRedLines:
    def test_pipeline_context_has_no_translation_fields(self):
        """PipelineContext 是 chunk worker 子进程的唯一参数包：永不携带翻译。"""
        from core.pipeline_stages import PipelineContext

        names = {f.name for f in dc_fields(PipelineContext)}
        assert not any("translat" in n for n in names), names

    @pytest.mark.parametrize(
        "module_name",
        ["core.chunk_worker", "core.chunk_parallel_runner",
         "core.chunk_merger", "core.pipeline_stages"],
    )
    def test_chunk_path_modules_never_reference_translation(self, module_name):
        """阶段 1-3 的 chunk 路径模块源代码不引用翻译模块（阶段归属红线）。"""
        mod = sys.modules.get(module_name)
        if mod is None:
            __import__(module_name)
            mod = sys.modules[module_name]
        src = inspect.getsource(mod)
        assert "font_intel" not in src
        assert "translate_subtitle_texts" not in src
        assert "TranslationConfig" not in src

    def test_worker_stage_ctx_excludes_translation_config(self, tmp_path):
        """PipelineWorker 收下 translation_config（阶段 4 用），但
        _stage_ctx()（阶段 1-3 的 picklable 参数包）绝不携带它。"""
        from core.pipeline_worker import PipelineWorker
        from core.pipeline_stages import PipelineContext

        cfg = TranslationConfig(cloud_api_key="sk-test")
        worker = PipelineWorker(
            video_path=str(tmp_path / "in.avi"),
            roi_data=[{"type": "rect", "points": [0, 0, 10, 10],
                       "ocr_lang": "japan"}],
            total_frames=100,
            fps=25.0,
            video_width=1280,
            video_height=720,
            output_ass_path=str(tmp_path / "out.ass"),
            debug_mode=False,
            template_path=None,
            translation_config=cfg,
        )
        assert worker.translation_config is cfg
        ctx = worker._stage_ctx()
        assert isinstance(ctx, PipelineContext)
        for field in dc_fields(PipelineContext):
            assert "translat" not in field.name
        assert ctx.roi_data == worker.roi_data  # 仅阶段 1-3 字段

    def test_translation_entry_lives_on_generator_not_stages(self):
        """翻译入口挂在生成器（阶段 4）：OCRToASSOptimizer 有接入参数，
        阶段函数（extract/refine/restore）无翻译概念。"""
        assert "translation_config" in inspect.signature(
            OCRToASSOptimizer.__init__).parameters
        from core import pipeline_stages

        for fn_name in ("extract_and_ocr_stage", "refine_stage", "restore_stage"):
            params = inspect.signature(getattr(pipeline_stages, fn_name)).parameters
            assert not any("translat" in p for p in params)


# ── 2. 生成器接入缝 ──────────────────────────────────────────────


class TestGeneratorIntegration:
    def test_translation_splits_comment_original_and_dialogue_translated(
            self, monkeypatch, tmp_path, _no_vlm):
        """译文 Dialogue + 原文 Comment 拆分：时间/样式/Name 逐项一致，
        原文行保留在文件中但播放器不渲染（Comment）。"""
        seen: dict = {}

        def fake_translate(texts, cfg, **kw):
            seen["texts"] = list(texts)
            seen["cfg"] = cfg
            return tr.TranslationResult(
                texts=[f"译{ i }" for i in range(len(texts))],
                stats=tr.TranslationStats(total_lines=len(texts), batches_total=1,
                                          batches_attempted=1, batches_success=1),
            )

        monkeypatch.setattr(tr, "translate_subtitle_texts", fake_translate)
        conv = _make_optimizer(
            tmp_path, translation_config=TranslationConfig(cloud_api_key="sk-test"))
        conv.convert_from_memory(iter(_items()))

        out = _read_out(tmp_path)
        lines = out.splitlines()
        assert seen["texts"] == ["学成归来"]
        assert isinstance(seen["cfg"], TranslationConfig)
        # 原文 → Comment（隐藏但保留）；译文 → Dialogue。
        comments = [ln for ln in lines if ln.startswith("Comment:") and "学成归来" in ln]
        dialogues = [ln for ln in lines if ln.startswith("Dialogue:") and "译0" in ln]
        assert len(comments) == 1
        assert len(dialogues) == 1
        # 拆分两侧时间/样式/Name 逐项一致（Layer,Start,End,Style,Name 列）。
        c_head = comments[0].split(": ", 1)[1].rsplit(",0,0,0,,", 1)[0]
        d_head = dialogues[0].split(": ", 1)[1].rsplit(",0,0,0,,", 1)[0]
        assert c_head == d_head

    def test_translation_same_text_keeps_single_dialogue(
            self, monkeypatch, tmp_path, _no_vlm):
        """译文与原文相同（失败/降级保留原文）：不拆分，无 Comment 行。"""
        monkeypatch.setattr(
            tr, "translate_subtitle_texts",
            lambda texts, cfg, **kw: tr.TranslationResult(
                list(texts), tr.TranslationStats(total_lines=len(texts),
                                                 batches_total=1)))
        conv = _make_optimizer(
            tmp_path, translation_config=TranslationConfig(cloud_api_key="sk-test"))
        conv.convert_from_memory(iter(_items()))
        out = _read_out(tmp_path)
        assert "学成归来" in out
        assert "Comment:" not in out

    def test_translation_failure_keeps_originals_and_completes(
            self, monkeypatch, tmp_path, _no_vlm):
        """翻译入口抛异常：保留原文，出片不中断。"""

        def boom(texts, cfg, **kw):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(tr, "translate_subtitle_texts", boom)
        conv = _make_optimizer(
            tmp_path, translation_config=TranslationConfig(cloud_api_key="sk-test"))
        conv.convert_from_memory(iter(_items()))

        out = _read_out(tmp_path)
        assert "学成归来" in out  # 原文保留
        assert "Dialogue:" in out  # 出片正常完成

    def test_translation_length_mismatch_keeps_originals(
            self, monkeypatch, tmp_path, _no_vlm):
        """返回长度与输入不符：该组保留原文（防线纵深，模块本不该这样）。"""
        monkeypatch.setattr(
            tr, "translate_subtitle_texts",
            lambda texts, cfg, **kw: tr.TranslationResult(texts=[], stats=tr.TranslationStats()))
        conv = _make_optimizer(
            tmp_path, translation_config=TranslationConfig(cloud_api_key="sk-test"))
        conv.convert_from_memory(iter(_items()))
        assert "学成归来" in _read_out(tmp_path)

    def test_translation_cancel_keeps_remaining_groups_original(
            self, monkeypatch, tmp_path, _no_vlm):
        """取消翻真：未派发的组保留原文，文件仍写出（与润色同语义）。"""
        seen: list = []
        state = {"checks": 0}

        def cancel_check():
            state["checks"] += 1
            return state["checks"] > 1  # 第 1 次放行，之后为真

        def fake_translate(texts, cfg, **kw):
            seen.append(list(texts))
            return tr.TranslationResult(
                [f"译{ i }" for i in range(len(texts))], tr.TranslationStats())

        monkeypatch.setattr(tr, "translate_subtitle_texts", fake_translate)
        items = _items("こんにちは", roi="roi_0") + _items("Hello world", roi="roi_1")
        conv = _make_optimizer(
            tmp_path,
            translation_config=TranslationConfig(cloud_api_key="sk-test"),
            roi_ocr_langs={"roi_0": "japan", "roi_1": "en"},
        )
        conv.convert_from_memory(iter(items), polish_cancel_check=cancel_check)

        assert len(seen) == 1  # 只有第 1 组送翻，取消后第 2 组不再派发
        out = _read_out(tmp_path)
        # 第 1 组已译：原文转 Comment 隐藏，译文 Dialogue。
        assert any(ln.startswith("Comment:") and "こんにちは" in ln for ln in out.splitlines())
        assert any(ln.startswith("Dialogue:") and "こんにちは" not in ln and "译" in ln
                   for ln in out.splitlines())
        # 第 2 组未送翻：保持单条原文 Dialogue，无 Comment。
        assert any(ln.startswith("Dialogue:") and "Hello world" in ln
                   for ln in out.splitlines())
        assert not any("Hello world" in ln for ln in out.splitlines()
                       if ln.startswith("Comment:"))

    def test_none_config_is_inert_and_byte_identical(self, monkeypatch, tmp_path, _no_vlm):
        """translation_config=None：翻译入口绝不被调用，输出与不传参数
        逐字节一致（回归门禁 G4 的生成器版）。"""
        calls: list = []
        canary = types.ModuleType("font_intel.translation")

        def _canary(*a, **k):
            calls.append(a)
            raise AssertionError("translation must not run when config is None")

        canary.translate_subtitle_texts = _canary
        monkeypatch.setitem(sys.modules, "font_intel.translation", canary)

        os.makedirs(tmp_path / "explicit", exist_ok=True)
        os.makedirs(tmp_path / "absent", exist_ok=True)
        conv_explicit = OCRToASSOptimizer(
            video_path=str(tmp_path / "explicit" / "in.mp4"),
            output_path=str(tmp_path / "explicit" / "out.ass"),
            fps=25.0, width=1280, height=720,
            translation_config=None,
        )
        conv_absent = OCRToASSOptimizer(
            video_path=str(tmp_path / "absent" / "in.mp4"),
            output_path=str(tmp_path / "absent" / "out.ass"),
            fps=25.0, width=1280, height=720,
        )
        conv_explicit.convert_from_memory(iter(_items()))
        conv_absent.convert_from_memory(iter(_items()))
        assert calls == []
        assert (tmp_path / "explicit" / "out.ass").read_bytes() == \
            (tmp_path / "absent" / "out.ass").read_bytes()

    def test_policy_events_are_never_translated(self, monkeypatch, tmp_path, _no_vlm):
        """策略事件（遮罩/NoteBox 等空 body 或 policy 标记）不送翻译。"""
        seen: list = []

        def fake_translate(texts, cfg, **kw):
            seen.extend(texts)
            return tr.TranslationResult([f"译{ i }" for i in range(len(texts))],
                                        tr.TranslationStats())

        monkeypatch.setattr(tr, "translate_subtitle_texts", fake_translate)
        conv = _make_optimizer(
            tmp_path, translation_config=TranslationConfig(cloud_api_key="sk-test"))
        conv.convert_from_memory(iter(_items()))
        assert seen == ["学成归来"]  # 仅对白行，无其他内容混入


# ── 2.5 轨迹事件翻译（此前完全绕过翻译的阶段缺口） ────────────────


def _motion_event(text="受信メール一覧", roi="roi_0", **extra) -> dict:
    """对齐 worker/motion_ass 产出的轨迹事件形状（用户示例同款）。"""
    ev = {
        "start_time": "0:00:00.00",
        "end_time": "0:00:03.54",
        "style": "Scene",
        "name": "motion",
        "tags": r"{\an5\fs24\move(821.5,305.0,821.5,20.6,0,3545)}",
        "body": text,
        "roi": roi,
    }
    ev.update(extra)
    return ev


class TestMotionEventTranslation:
    def test_motion_events_translated_with_split(self, monkeypatch, tmp_path,
                                                 _no_vlm):
        """轨迹事件送翻：原文 Comment + 译文 Dialogue，\\move 等标签原样。"""
        seen: list = []

        def fake_translate(texts, cfg, **kw):
            seen.append(list(texts))
            return tr.TranslationResult(
                [f"译[{ t }]" for t in texts],
                tr.TranslationStats(total_lines=len(texts), batches_total=1,
                                    batches_success=1))

        monkeypatch.setattr(tr, "translate_subtitle_texts", fake_translate)
        conv = _make_optimizer(
            tmp_path,
            translation_config=TranslationConfig(cloud_api_key="sk-test"),
            motion_events=[
                _motion_event(),
                # 策略遮罩（空 body）与 mask_only Comment 行不送翻。
                _motion_event("", body="", tags=r"{\clip(...)}", policy=True),
                _motion_event("参考行", comment=True),
            ],
        )
        conv.convert_from_memory(iter(_items("学成归来")))

        # 静态行与轨迹行各成一组送翻（轨迹事件带 roi 时按 ROI 路由语言）。
        flattened = [t for group in seen for t in group]
        assert "受信メール一覧" in flattened
        assert "学成归来" in flattened
        # 策略/Comment 轨迹行绝不进模型。
        assert flattened.count("") == 0
        assert "参考行" not in flattened

        out = _read_out(tmp_path)
        lines = out.splitlines()
        comments = [ln for ln in lines if ln.startswith("Comment:")]
        dialogues = [ln for ln in lines if ln.startswith("Dialogue:")]
        # 原文轨迹行 → Comment（\\move 标签逐字保留）；译文轨迹行 → Dialogue。
        assert any("受信メール一覧" in ln and r"\move(" in ln for ln in comments)
        assert any("译[受信メール一覧]" in ln and r"\move(" in ln for ln in dialogues)
        assert any(ln.startswith("Comment:") and "学成归来" in ln for ln in lines)
        # mask_only 排版参考行维持 Comment 原样；遮罩行维持 Dialogue。
        assert any("参考行" in ln for ln in comments)
        assert any(r"\clip" in ln for ln in dialogues)

    def test_motion_only_output_also_translated(self, monkeypatch, tmp_path,
                                                _no_vlm):
        """无静态数据、纯轨迹输出：早退路径同样过翻译拆分。"""
        monkeypatch.setattr(
            tr, "translate_subtitle_texts",
            lambda texts, cfg, **kw: tr.TranslationResult(
                [f"译[{ t }]" for t in texts],
                tr.TranslationStats(total_lines=len(texts))))
        conv = _make_optimizer(
            tmp_path,
            translation_config=TranslationConfig(cloud_api_key="sk-test"),
            motion_events=[_motion_event()],
        )
        conv.convert_from_memory(iter([]))
        out = _read_out(tmp_path)
        assert any(ln.startswith("Comment:") and "受信メール一覧" in ln
                   for ln in out.splitlines())
        assert any(ln.startswith("Dialogue:") and "译[受信メール一覧]" in ln
                   for ln in out.splitlines())

    def test_worker_motion_events_carry_roi(self):
        """worker 给轨迹事件挂 roi：生成器据此路由该 ROI 的源语言。"""
        import inspect

        from core.pipeline_worker import PipelineWorker

        src = inspect.getsource(PipelineWorker._run_motion_stage)
        assert 'ev["roi"] = roi_id' in src


# ── 3. 双语路由：按 ROI ocr_lang 分组 + 源语言提示 ────────────────


class TestRoiLanguageRouting:
    def test_events_grouped_by_roi_ocr_lang(self, monkeypatch, tmp_path, _no_vlm):
        """双语场景：不同 ROI 的行各自成组送翻，源语言按 ocr_lang 路由。"""
        groups: list = []

        def fake_translate(texts, cfg, **kw):
            groups.append((list(texts), getattr(cfg, "source_language", "")))
            return tr.TranslationResult(
                [f"译{ len(groups) }-{ i }" for i in range(len(texts))],
                tr.TranslationStats(total_lines=len(texts), batches_total=1,
                                    batches_attempted=1, batches_success=1))

        monkeypatch.setattr(tr, "translate_subtitle_texts", fake_translate)
        items = _items("こんにちは", roi="roi_0") + _items("Hello world", roi="roi_1")
        conv = _make_optimizer(
            tmp_path,
            translation_config=TranslationConfig(cloud_api_key="sk-test"),
            roi_ocr_langs={"roi_0": "japan", "roi_1": "en"},
        )
        conv.convert_from_memory(iter(items))

        by_source = {src: texts for texts, src in groups}
        assert by_source["日语"] == ["こんにちは"]
        assert by_source["英语"] == ["Hello world"]
        out = _read_out(tmp_path)
        # 两组都有实际译文：原文行转 Comment，译文行 Dialogue。
        for original in ("こんにちは", "Hello world"):
            assert any(ln.startswith("Comment:") and original in ln
                       for ln in out.splitlines())

    def test_unknown_ocr_lang_falls_back_to_auto(self, monkeypatch, tmp_path, _no_vlm):
        """未配置/未知 ocr_lang：source_language 留空 = 模型自动检测。"""
        groups: list = []

        def fake_translate(texts, cfg, **kw):
            groups.append((list(texts), getattr(cfg, "source_language", "")))
            return tr.TranslationResult(list(texts), tr.TranslationStats())

        monkeypatch.setattr(tr, "translate_subtitle_texts", fake_translate)
        conv = _make_optimizer(
            tmp_path,
            translation_config=TranslationConfig(cloud_api_key="sk-test"),
            roi_ocr_langs={"roi_0": "ch", "roi_1": "xx-unknown"},
        )
        conv.convert_from_memory(
            iter(_items("学成归来", roi="roi_0") + _items("Bonjour", roi="roi_1")))

        by_source = {src: texts for texts, src in groups}
        assert by_source["简体中文"] == ["学成归来"]
        assert by_source[""] == ["Bonjour"]  # 未知 id → 自动检测

    def test_missing_roi_entry_falls_back_to_auto(self, monkeypatch, tmp_path, _no_vlm):
        """roi_ocr_langs 缺该 ROI 键（如 merge_rois 画布）：回退自动检测。"""
        groups: list = []

        def fake_translate(texts, cfg, **kw):
            groups.append((list(texts), getattr(cfg, "source_language", "")))
            return tr.TranslationResult(list(texts), tr.TranslationStats())

        monkeypatch.setattr(tr, "translate_subtitle_texts", fake_translate)
        conv = _make_optimizer(
            tmp_path,
            translation_config=TranslationConfig(cloud_api_key="sk-test"),
            roi_ocr_langs=None,
        )
        conv.convert_from_memory(iter(_items()))
        assert groups and groups[0][1] == ""

    def test_source_language_hint_lands_in_prompt(self, monkeypatch, _no_vlm):
        """translation.py 最小扩展：source_language 非空时写入提示词；
        缺省（空）prompt 与既有行为一致，不含源语言行。"""
        captured: list = []

        def fake_call_llm(**kw):
            captured.append(kw["messages"])
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content='["你好"]'))])

        monkeypatch.setattr("core.llm_client.call_llm", fake_call_llm)
        base = TranslationConfig(cloud_api_key="sk-test",
                                 cloud_base_url="https://cloud.example.com",
                                 batch_size=1, max_concurrent_requests=1)

        result = tr.translate_subtitle_texts(["こんにちは"], base)
        assert result.texts == ["你好"]
        user_text = captured[0][1]["content"]
        assert "源语言" not in user_text  # 缺省：无源语言提示（既有行为）

        jp = tr.translate_subtitle_texts(
            ["こんにちは"], dataclasses.replace(base, source_language="日语"))
        assert jp.texts == ["你好"]
        assert "源语言：日语" in captured[1][1]["content"]


# ── 4. CLI 贯通 ──────────────────────────────────────────────────


class TestCliTranslationArgs:
    def test_translate_off_gives_none(self):
        args = cli.parse_args(["video.mp4"])
        assert cli._translation_config_from_args(args) is None

    def test_translate_defaults_match_module_defaults(self, monkeypatch, _no_vlm):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        args = cli.parse_args(["video.mp4", "--translate"])
        cfg = cli._translation_config_from_args(args)
        assert isinstance(cfg, TranslationConfig)
        # 云端缺省：key 来自 OPENAI_API_KEY（此处已删 → 空 → 云端跳过）。
        assert cfg.cloud_api_key == ""
        assert cfg.target_language == tr.DEFAULT_TARGET_LANGUAGE
        assert cfg.context_window_lines == tr.DEFAULT_CONTEXT_LINES
        assert cfg.max_line_chars == tr.DEFAULT_MAX_LINE_CHARS
        assert cfg.glossary_path == ""

    def test_translate_full_mapping(self, monkeypatch, tmp_path, _no_vlm):
        glossary = tmp_path / "glossary.json"
        glossary.write_text("{}", encoding="utf-8")
        args = cli.parse_args([
            "video.mp4", "--translate",
            "--translate-target", "繁體中文",
            "--translate-provider", "sakura",
            "--translate-base-url", "http://127.0.0.1:8080/v1",
            "--translate-model", "sakura-14b",
            "--translate-glossary", str(glossary),
            "--translate-context-lines", "3",
            "--translate-max-chars", "20",
        ])
        cfg = cli._translation_config_from_args(args)
        assert cfg.target_language == "繁體中文"
        assert cfg.sakura_base_url == "http://127.0.0.1:8080/v1"
        assert cfg.sakura_model == "sakura-14b"
        # provider=sakura：云端 key 不从环境变量带入（不抢链首）。
        assert cfg.cloud_api_key == ""
        assert cfg.glossary_path == str(glossary)
        assert cfg.context_window_lines == 3
        assert cfg.max_line_chars == 20

    def test_cloud_provider_resolves_key_from_env(self, monkeypatch, _no_vlm):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        args = cli.parse_args(["video.mp4", "--translate", "--translate-provider", "cloud"])
        cfg = cli._translation_config_from_args(args)
        assert cfg.cloud_api_key == "sk-from-env"
        assert cfg.cloud_base_url  # 缺省 DeepSeek 端点
        assert cfg.sakura_base_url == ""

    def test_max_chars_zero_disables_limit(self, _no_vlm):
        args = cli.parse_args(
            ["video.mp4", "--translate", "--translate-max-chars", "0"])
        cfg = cli._translation_config_from_args(args)
        assert cfg.max_line_chars == 0

    def test_invalid_provider_rejected(self):
        with pytest.raises(SystemExit):
            cli.parse_args(
                ["video.mp4", "--translate", "--translate-provider", "magic"])

    def test_worker_cli_style_roi_langs_collection(self, tmp_path):
        """PipelineWorker 按 GUI/CLI 同一规则收集 roi_id → ocr_lang。"""
        from core.pipeline_worker import collect_roi_ocr_langs

        langs = collect_roi_ocr_langs([
            {"type": "rect", "points": [0, 0, 10, 10], "ocr_lang": "japan"},
            {"type": "rect", "points": [0, 0, 10, 10]},
            "not-a-dict",
        ])
        assert langs == {"roi_0": "japan", "roi_1": ""}
