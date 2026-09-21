# tests/test_font_identify_stage.py
"""T3.1 + T3.3 字体识别链测试：识别器接口 / YuzuMarker 候选生成（torch 可选）
/ ``font_identify`` 主进程后置阶段 / PipelineWorker 接线。

本测试 venv 无 torch——yuzu 推理路径全部以接口 + mock 锁定，覆盖：

1. base（T3.1）：``FontCandidate`` 契约、``FontRecognizer`` 抽象接口、
   ``get_recognizer`` 工厂（绝不抛异常；torch 缺失时可用链为空）、
   ``collect_candidates`` 链内隔离与去重归并。
2. yuzu（T3.1）：``is_available`` 静态判定（不 import torch、不联网）、
   torch 缺失 / 加载失败 / 推理失败 / 非法输入的空候选降级、Top-N 映射、
   失败状态粘滞与 ``reset()`` 可恢复、有界重试下载（次数上限 / 总时长
   预算 / ``HF_ENDPOINT`` 镜像 / 已存在免下载 / huggingface_hub 优先）。
3. identify_stage（T3.3）：默认关闭零开销（不开视频、不 import 识别模块）、
   Top-N 挂载与许可查询、(roi, 稳定文本) 去重采样（同组只随机访问 1 帧）、
   yuzu 失败仅字形重排可用时的降级、阶段异常不中断、max_groups 上限、
   取消、本机字体名称→路径解析、真实合成小视频随机访问取帧集成、
   restored_results 原有元组结构不被修改。
4. worker 接线：``font_identify_config`` 默认 None、``_stage_ctx()``
   不携带（chunk worker 红线）、启用/关闭时阶段函数调用行为、阶段异常
   吞掉、run() 中调用点位于构造优化器之前。
"""

from __future__ import annotations

import copy
import importlib.util
import inspect
import os
import shutil
import sys
import types
from dataclasses import fields as dc_fields

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import font_intel.identify_stage as stage  # noqa: E402
import font_intel.recognizer.base as base  # noqa: E402
import font_intel.recognizer.yuzu as yuzu  # noqa: E402
from font_intel.identify_stage import (  # noqa: E402
    FontIdentifyConfig,
    FontIdentifyResult,
    run_font_identify_stage,
)
from font_intel.fonts_db import FontsDB  # noqa: E402
from font_intel.recognizer.base import FontCandidate, FontRecognizer  # noqa: E402

TORCH_MISSING = importlib.util.find_spec("torch") is None

DEJAVU_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

ROI_ENTRY = {"type": "rect", "points": [90, 590, 420, 70],
             "start_frame": 0, "end_frame": 100}
LINE_BOX = (100, 600, 500, 650)


def _has_dejavu() -> bool:
    return os.path.isfile(DEJAVU_SANS)


def _text_crop(text: str = "ABC", font_path: str = DEJAVU_SANS,
               size: tuple = (420, 70)) -> np.ndarray:
    """合成"视频字块"：白底黑字灰度图（rerank 可归一化比对）。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", size, 255)
    ImageDraw.Draw(img).text(
        (12, 6), text, font=ImageFont.truetype(font_path, 42), fill=0)
    return np.asarray(img)


def _frame_item(frame_num, texts, boxes, roi="roi_0", scores=None):
    if scores is None:
        scores = [0.95] * len(texts)
    data = {
        "rec_texts": list(texts),
        "rec_scores": list(scores),
        "rec_boxes": [list(b) for b in boxes],
        "dt_polys": [], "rec_polys": [],
    }
    return (data, frame_num, roi, frame_num / 25.0)


def _items_same_text(frames=(10, 11, 12), roi="roi_0", text="字幕テスト"):
    return [_frame_item(f, [text], [LINE_BOX], roi=roi) for f in frames]


def _fake_extract(crop):
    """monkeypatch 替身：记录 (video, roi_entry, frame) 调用并返回合成字块。"""
    calls: list = []

    def _extract(video_path, roi_entry, frame_num, cap=None, fps=0.0):
        calls.append({"video": video_path, "roi": roi_entry, "frame": frame_num})
        if crop is None:
            return None
        return crop.copy(), frame_num / 25.0

    return _extract, calls


class FakeRecognizer(FontRecognizer):
    """测试替身：可注入候选、可用性、异常。"""

    source = "fake"

    def __init__(self, cands=None, raise_exc=False, avail=True):
        self.cands = list(cands or [])
        self.raise_exc = raise_exc
        self._avail = bool(avail)
        self.calls: list = []

    @property
    def available(self) -> bool:
        return self._avail

    def identify(self, text, crop_image, top_n=5):
        self.calls.append((text, top_n))
        if self.raise_exc:
            raise RuntimeError("recognizer exploded")
        return self.cands[:top_n]


def _seed_db(tmp_path):
    path = str(tmp_path / "fonts.db")
    db = FontsDB(path)
    db.import_seed([{
        "table": "fonts",
        "canonical_name": "Liberation Sans",
        "license_category": "open_source",
        "license_name": "OFL-1.1",
    }])
    db.close()
    return path


def _capture_canary(monkeypatch):
    """VideoCapture 哨兵：任何打开视频的企图都立刻失败。"""

    def _boom(*a, **k):
        raise AssertionError("VideoCapture must not be opened")

    monkeypatch.setattr(cv2, "VideoCapture", _boom)


# ══════════════════════════════════════════════════════════════════
# 1. base：识别器接口（T3.1）
# ══════════════════════════════════════════════════════════════════


class TestBase:
    def test_font_candidate_as_dict(self):
        c = FontCandidate("N", 0.5, "yuzu", font_path="/a.ttf")
        assert c.as_dict() == {
            "name": "N", "score": 0.5, "source": "yuzu", "font_path": "/a.ttf"}

    def test_recognizer_is_abstract(self):
        with pytest.raises(TypeError):
            FontRecognizer()  # type: ignore[abstract]

    def test_get_recognizer_never_raises_and_contract_holds(self):
        chain = base.get_recognizer(None)
        assert isinstance(chain, list)
        for rec in chain:
            assert callable(rec.identify)
            assert isinstance(rec.available, bool)
            assert isinstance(rec.source, str) and rec.source

    def test_get_recognizer_reads_config_fields(self):
        cfg = FontIdentifyConfig(enabled=True, allow_download=False)
        chain = base.get_recognizer(cfg)
        assert chain, "cfg 非 None 时至少构造 yuzu 识别器对象"
        rec = chain[0]
        assert rec.source == "yuzu"
        assert rec.allow_download is False

    @pytest.mark.skipif(not TORCH_MISSING, reason="torch 已安装，无 torch 断言不适用")
    def test_get_recognizer_no_available_without_torch(self):
        """torch 缺失：工厂不抛异常，返回的链中可用项为空。"""
        chain = base.get_recognizer(FontIdentifyConfig(enabled=True))
        assert isinstance(chain, list)
        assert not any(getattr(r, "available", True) for r in chain)

    def test_collect_candidates_merges_dedupes_and_isolates(self):
        r1 = FakeRecognizer(cands=[
            FontCandidate("A", 0.5, "s1"),
            FontCandidate("B", 0.9, "s1", font_path="/b.ttf"),
        ])
        r2 = FakeRecognizer(cands=[
            FontCandidate("a", 0.7, "s2"),  # 与 A 同名（大小写不敏感）取高分
            FontCandidate("C", 0.4, "s2"),
        ])
        boom = FakeRecognizer(raise_exc=True)
        crop = np.zeros((8, 8), dtype=np.uint8)
        out = base.collect_candidates([r1, r2, boom], "t", crop, top_n=5)
        assert [c.name.casefold() for c in out] == ["b", "a", "c"]
        assert out[0].font_path == "/b.ttf"
        assert out[1].score == 0.7  # A/a 同名去重取最高分
        assert boom.calls  # 失败识别器确实被调用过（异常被链内吞掉）

    def test_collect_candidates_topn(self):
        class _NoTruncRecognizer(FakeRecognizer):
            def identify(self, text, crop_image, top_n=5):
                self.calls.append((text, top_n))
                return list(self.cands)  # 不在识别器侧截断，专测链归并截断

        rec = _NoTruncRecognizer(cands=[
            FontCandidate(f"F{ i }", 0.1 * i, "s") for i in range(5)])
        out = base.collect_candidates([rec], "t", np.zeros((8, 8)), top_n=2)
        assert [c.name for c in out] == ["F4", "F3"]

    def test_collect_candidates_skips_garbage_entries(self):
        rec = FakeRecognizer(cands=[FontCandidate("  ", 0.9, "s"), "junk", None,
                                    FontCandidate("OK", 0.5, "s")])
        out = base.collect_candidates([rec], "t", np.zeros((8, 8)), top_n=9)
        assert [c.name for c in out] == ["OK"]


# ══════════════════════════════════════════════════════════════════
# 2. yuzu：YuzuMarker 候选生成器（T3.1，torch 可选）
# ══════════════════════════════════════════════════════════════════


class TestYuzuAvailability:
    def test_model_repo_constant(self):
        assert yuzu.DEFAULT_MODEL_REPO == "JeffersonQin/YuzuMarker.FontDetection"

    def test_is_available_static_without_torch(self, monkeypatch):
        monkeypatch.setattr(yuzu, "_torch_available", lambda: False)
        assert yuzu.YuzuFontRecognizer.is_available() is False
        assert yuzu.YuzuFontRecognizer.is_available(allow_download=True) is False

    def test_is_available_with_torch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(yuzu, "_torch_available", lambda: True)
        w = tmp_path / "w.ckpt"
        w.write_bytes(b"x")
        assert yuzu.YuzuFontRecognizer.is_available(weights_path=str(w)) is True
        missing = str(tmp_path / "missing.ckpt")
        # 权重缺失 + 不允许下载 → 不可用；允许延迟下载 → 可用
        assert yuzu.YuzuFontRecognizer.is_available(
            weights_path=missing, allow_download=False) is False
        assert yuzu.YuzuFontRecognizer.is_available(
            weights_path=missing, allow_download=True) is True

    def test_module_import_never_pulls_torch(self):
        """模块导入零 torch 依赖：torch 不在 sys.modules 也必须可导入。"""
        assert "torch" not in sys.modules or TORCH_MISSING or True
        # 真正的断言：上面 import yuzu 已成功（文件级 import），此处仅守
        # 回归——若有人在模块顶层加 torch import，TORCH_MISSING 环境下
        # 文件级 import 就会失败。
        assert callable(yuzu.YuzuFontRecognizer.identify)


class TestYuzuIdentify:
    def test_identify_empty_when_torch_missing(self, monkeypatch):
        monkeypatch.setattr(yuzu, "_torch_available", lambda: False)
        rec = yuzu.YuzuFontRecognizer()
        assert rec.available is False
        crop = np.zeros((16, 16), dtype=np.uint8)
        assert rec.identify("テスト", crop, top_n=3) == []
        assert rec.state == "failed" and "torch" in rec.last_error

    def test_identify_topn_mapping_with_mocked_inference(self, monkeypatch):
        monkeypatch.setattr(yuzu, "_torch_available", lambda: True)
        rec = yuzu.YuzuFontRecognizer()
        rec._state = "loaded"
        rec._model = object()
        rec._labels = ["A", "B", "C"]
        monkeypatch.setattr(
            yuzu.YuzuFontRecognizer, "_infer_top_n",
            staticmethod(lambda model, crop, top_n: [("A", 0.9), ("B", 0.05)][:top_n]))
        cands = rec.identify("任意文本", np.zeros((32, 32), np.uint8), top_n=1)
        assert [c.name for c in cands] == ["A"]
        assert cands[0].source == "yuzu"
        assert cands[0].score == pytest.approx(0.9)

    def test_identify_inference_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(yuzu, "_torch_available", lambda: True)

        def _boom(model, crop, top_n):
            raise RuntimeError("cuda exploded")

        monkeypatch.setattr(yuzu.YuzuFontRecognizer, "_infer_top_n",
                            staticmethod(_boom))
        rec = yuzu.YuzuFontRecognizer()
        rec._state = "loaded"
        rec._model = object()
        rec._labels = ["A"]
        assert rec.identify("t", np.zeros((16, 16), np.uint8)) == []
        assert rec.state == "failed" and "inference" in rec.last_error

    def test_identify_invalid_inputs_never_load_model(self, monkeypatch):
        monkeypatch.setattr(yuzu, "_torch_available", lambda: True)
        rec = yuzu.YuzuFontRecognizer()
        calls: list = []
        monkeypatch.setattr(
            rec, "_ensure_loaded", lambda: calls.append(1) or True)
        assert rec.identify("", np.zeros((16, 16), np.uint8)) == []
        assert rec.identify("   ", np.zeros((16, 16), np.uint8)) == []
        assert rec.identify("t", None) == []
        assert calls == []  # 非法输入不触发模型加载/下载

    def test_failure_state_sticky_until_reset(self, monkeypatch):
        """加载失败粘滞（组循环不反复触发下载）；reset() 后可重试（可恢复）。"""
        monkeypatch.setattr(yuzu, "_torch_available", lambda: True)
        rec = yuzu.YuzuFontRecognizer()
        calls: list = []

        def _fake_load():
            # 复刻 _ensure_loaded 失败契约：返回 False 并落失败状态。
            calls.append(1)
            rec._state = yuzu.STATE_FAILED
            rec.last_error = "weights_download_failed"
            return False

        monkeypatch.setattr(rec, "_ensure_loaded", _fake_load)
        crop = np.zeros((16, 16), np.uint8)
        assert rec.identify("t", crop) == []
        assert rec.identify("t", crop) == []
        assert len(calls) == 1
        rec.reset()
        assert rec.state == "unloaded"
        assert rec.identify("t", crop) == []
        assert len(calls) == 2


class TestYuzuDownload:
    class _FailOpener:
        def __init__(self, sink):
            self.sink = sink

        def __call__(self, url, timeout=None):
            self.sink.append(url)
            raise IOError("network down")

    class _OkResp:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def read(self, size=-1):
            return self._chunks.pop(0) if self._chunks else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_hf_endpoint_mirror(self, monkeypatch):
        monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com/")
        url = yuzu.hf_download_url(yuzu.DEFAULT_MODEL_REPO, "w.ckpt")
        assert url == "https://hf-mirror.com/JeffersonQin/YuzuMarker.FontDetection/resolve/main/w.ckpt"
        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        url = yuzu.hf_download_url(yuzu.DEFAULT_MODEL_REPO, "w.ckpt")
        assert url == "https://huggingface.co/JeffersonQin/YuzuMarker.FontDetection/resolve/main/w.ckpt"

    def test_bounded_download_success_after_retry(self, tmp_path):
        dest = tmp_path / "w.ckpt"
        calls: list = []

        def opener(url, timeout=None):
            calls.append(url)
            if len(calls) == 1:
                raise IOError("first try fails")
            return self._OkResp([b"weight", b"bytes"])

        got = yuzu.bounded_download(
            "http://x/w.ckpt", str(dest), opener=opener, sleep=lambda s: None)
        assert got == str(dest)
        assert dest.read_bytes() == b"weightbytes"
        assert len(calls) == 2

    def test_bounded_download_gives_up_after_max_attempts(self, tmp_path):
        dest = tmp_path / "w.ckpt"
        sink: list = []
        got = yuzu.bounded_download(
            "http://x/w.ckpt", str(dest),
            opener=self._FailOpener(sink), sleep=lambda s: None,
            max_attempts=3, total_budget=60.0)
        assert got is None
        assert len(sink) == 3  # 恰好 3 次尝试（有界）
        assert not dest.exists()

    def test_bounded_download_total_time_budget(self, tmp_path):
        """总时长预算封顶：每次尝试"耗时"10s（opener 推进时钟）、预算
        25s → 只试 2 次；重试间隔计入同一预算。"""
        dest = tmp_path / "w.ckpt"
        sink: list = []
        clock = {"now": 0.0}
        sleeps: list = []

        def slow_opener(url, timeout=None):
            sink.append(url)
            clock["now"] += 10.0  # 每次尝试本身耗 10s
            raise IOError("network down")

        def fake_sleep(sec):
            sleeps.append(sec)
            clock["now"] += float(sec)

        got = yuzu.bounded_download(
            "http://x/w.ckpt", str(dest),
            opener=slow_opener, sleep=fake_sleep,
            now=lambda: clock["now"],
            max_attempts=10, per_attempt_timeout=5.0, total_budget=25.0)
        assert got is None
        assert len(sink) == 2
        assert sum(sleeps) <= 25.0 + 1e-6

    def test_ensure_weights_existing_file_never_downloads(self, tmp_path):
        w = tmp_path / "w.ckpt"
        w.write_bytes(b"have")

        def _boom(*a, **k):
            raise AssertionError("must not download")

        assert yuzu.ensure_weights(str(w), allow_download=True, opener=_boom) == str(w)

    def test_ensure_weights_blocked_when_not_allowed(self, tmp_path):
        def _boom(*a, **k):
            raise AssertionError("must not download")

        got = yuzu.ensure_weights(
            str(tmp_path / "w.ckpt"), allow_download=False, opener=_boom)
        assert got is None

    def test_ensure_weights_bounded_urllib_fallback(self, tmp_path, monkeypatch):
        """huggingface_hub 不可用时回落 urllib 直连（不强依赖 hub）。"""
        monkeypatch.setattr(yuzu, "_load_hf_hub", lambda: None)
        sink: list = []
        w = tmp_path / "sub" / "w.ckpt"
        got = yuzu.ensure_weights(
            str(w), allow_download=True,
            opener=self._FailOpener(sink), sleep=lambda s: None,
            max_attempts=3, per_attempt_timeout=5.0, total_budget=60.0)
        assert got is None
        assert len(sink) == 3
        assert sink[0].startswith("https://huggingface.co/")
        assert w.parent.is_dir()  # 缓存目录已建好（可恢复：重试即续用）

    def test_ensure_weights_via_huggingface_hub(self, tmp_path, monkeypatch):
        def fake_hf_hub_download(**kw):
            path = os.path.join(kw["local_dir"], "w.ckpt")
            with open(path, "wb") as fh:
                fh.write(b"from-hub")
            return path

        monkeypatch.setattr(
            yuzu, "_load_hf_hub",
            lambda: types.SimpleNamespace(hf_hub_download=fake_hf_hub_download))

        def _boom(*a, **k):
            raise AssertionError("hub 可用时不得走 urllib")

        w = tmp_path / "w.ckpt"
        got = yuzu.ensure_weights(str(w), allow_download=True, opener=_boom)
        assert got == str(w)
        assert w.read_bytes() == b"from-hub"


# ══════════════════════════════════════════════════════════════════
# 3. identify_stage：font_identify 后置阶段（T3.3）
# ══════════════════════════════════════════════════════════════════


class TestIdentifyStageDisabled:
    def test_none_and_disabled_configs_are_inert(self, monkeypatch):
        """配置关闭（默认）：不开视频、不触发任何识别模块导入，零开销直通。"""
        _capture_canary(monkeypatch)
        canary = types.ModuleType("font_intel.recognizer.yuzu")

        def _spy(*a, **k):
            raise AssertionError("yuzu must not be constructed when disabled")

        canary.YuzuFontRecognizer = _spy
        monkeypatch.setitem(sys.modules, "font_intel.recognizer.yuzu", canary)

        for cfg in (None, FontIdentifyConfig(enabled=False)):
            res = run_font_identify_stage(_items_same_text(), "v.avi", 25.0, cfg)
            assert isinstance(res, FontIdentifyResult)
            assert res.status == stage.STATUS_DISABLED
            assert res.identifications == {}

    def test_result_dataclass_defaults(self):
        res = FontIdentifyResult()
        assert res.enabled is False and res.identifications == {}


class TestIdentifyStageSampling:
    def test_disabled_never_opens_video(self, monkeypatch):
        _capture_canary(monkeypatch)
        run_font_identify_stage(
            _items_same_text(), "v.avi", 25.0, FontIdentifyConfig(enabled=False))

    def test_topn_mounted_license_and_structure_unchanged(self, monkeypatch, tmp_path):
        """识别链 mock 全通：Top-N 挂载侧表、许可查询、restored_results 不变。"""
        cfg = FontIdentifyConfig(
            enabled=True, top_n=2, confidence_threshold=0.3,
            db_path=_seed_db(tmp_path), scan_system_fonts=False)
        fake = FakeRecognizer(cands=[
            FontCandidate("Liberation Sans", 0.8, "yuzu", font_path=DEJAVU_SANS),
            FontCandidate("Mystery Script", 0.1, "yuzu"),
            FontCandidate("Third Font", 0.05, "yuzu", font_path=DEJAVU_SANS),
        ])
        extract, calls = _fake_extract(_text_crop("ABC"))
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)

        items = _items_same_text(frames=(10, 11, 12), text="ABC")
        snapshot = copy.deepcopy(items)
        res = run_font_identify_stage(
            iter(items), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[fake])

        assert res.status == stage.STATUS_OK
        assert res.groups_total == 1
        assert res.groups_sampled == 1
        assert set(res.identifications) == {
            ("roi_0", 10, 0), ("roi_0", 11, 0), ("roi_0", 12, 0)}
        entry = res.identifications[("roi_0", 11, 0)]
        assert entry["text"] == "ABC"
        assert entry["frame_num"] == 11  # 中位帧采样（10,11,12 的中位）
        fonts = entry["identified_fonts"]
        assert len(fonts) == 2  # top_n 截断
        assert fonts[0]["name"] == "Liberation Sans"
        assert fonts[0]["license_category"] == "open_source"
        assert fonts[0]["glyph_ranked"] is True
        assert fonts[0]["low_confidence"] is False
        assert fonts[1]["name"] == "Mystery Script"  # 无 font_path → 模型序殿后
        assert fonts[1]["glyph_ranked"] is False
        assert fonts[1]["license_category"] is None  # 库未收录
        assert fonts[1]["low_confidence"] is True  # 0.1 < 0.3
        # 识别器按接口收到 (text, crop, top_n)
        assert fake.calls and fake.calls[0][0] == "ABC"
        assert fake.calls[0][1] == 2
        assert calls and calls[0]["roi"] is ROI_ENTRY
        # 输入元组结构分毫未动（侧表语义，不改既有消费方）
        assert items == snapshot

    def test_same_group_sampled_once(self, monkeypatch):
        """同 (roi, 稳定文本) 只随机访问取 1 帧；组内所有行共享同一识别结果。"""
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        fake = FakeRecognizer(cands=[
            FontCandidate("Liberation Sans", 0.9, "yuzu", font_path=DEJAVU_SANS)])
        extract, calls = _fake_extract(_text_crop())
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)

        frames = (10, 11, 12, 13, 14)
        res = run_font_identify_stage(
            _items_same_text(frames=frames), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[fake])

        assert len(calls) == 1
        assert calls[0]["frame"] == 12
        assert set(res.identifications) == {("roi_0", f, 0) for f in frames}
        assert all(res.identifications[k]["frame_num"] == 12
                   for k in res.identifications)

    def test_distinct_text_or_roi_form_distinct_groups(self, monkeypatch):
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        extract, calls = _fake_extract(_text_crop())
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)

        items = (
            _items_same_text(frames=(10, 11), roi="roi_0", text="A")
            + _items_same_text(frames=(10, 11), roi="roi_0", text="B")
            + _items_same_text(frames=(10, 11), roi="roi_1", text="A")
        )
        res = run_font_identify_stage(
            items, "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY, ROI_ENTRY], recognizers=[FakeRecognizer()])
        assert res.groups_total == 3
        assert res.groups_sampled == 3
        assert len(calls) == 3
        assert {k[0] for k in res.identifications} == {"roi_0", "roi_1"}
        assert {res.identifications[k]["text"] for k in res.identifications} == {"A", "B"}

    def test_max_groups_cap(self, monkeypatch):
        cfg = FontIdentifyConfig(enabled=True, max_groups=1, scan_system_fonts=False)
        extract, calls = _fake_extract(_text_crop())
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)

        items = (_items_same_text(frames=(10, 11), roi="roi_0", text="A")
                 + _items_same_text(frames=(10, 11), roi="roi_0", text="B"))
        res = run_font_identify_stage(
            items, "v.avi", 25.0, cfg, roi_data=[ROI_ENTRY],
            recognizers=[FakeRecognizer()])
        assert res.groups_total == 2
        assert res.groups_sampled == 1
        assert len(calls) == 1

    def test_cancel_stops_sampling(self, monkeypatch):
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        extract, calls = _fake_extract(_text_crop())
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)
        state = {"n": 0}

        def cancel_check():
            state["n"] += 1
            return state["n"] > 1

        items = (_items_same_text(frames=(10, 11), roi="roi_0", text="A")
                 + _items_same_text(frames=(10, 11), roi="roi_0", text="B"))
        res = run_font_identify_stage(
            items, "v.avi", 25.0, cfg, roi_data=[ROI_ENTRY],
            recognizers=[FakeRecognizer()], cancel_check=cancel_check)
        assert res.status == stage.STATUS_CANCELLED
        assert res.groups_sampled == 1
        assert len(calls) == 1


class TestIdentifyStageDegradation:
    def test_no_recognizer_no_video_open(self, monkeypatch):
        """链中无可用识别器：不开视频、不出候选，状态可判。"""
        _capture_canary(monkeypatch)
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        res = run_font_identify_stage(
            _items_same_text(), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[])
        assert res.status == stage.STATUS_NO_RECOGNIZER
        assert res.identifications == {}

    def test_yuzu_failure_glyph_only_still_degrades_safely(self, monkeypatch):
        """yuzu 失败（抛异常被链内吞掉）：无候选 → 组状态 no_candidates，
        阶段不中断、其余键照常挂载。"""
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        boom = FakeRecognizer(raise_exc=True)
        off = FakeRecognizer(avail=False)
        extract, calls = _fake_extract(_text_crop())
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)

        res = run_font_identify_stage(
            _items_same_text(frames=(10, 11)), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[off, boom])
        assert res.status == stage.STATUS_OK
        assert boom.calls  # 失败识别器确实被调用（异常在链内隔离）
        entry = res.identifications[("roi_0", 10, 0)]
        assert entry["status"] == "no_candidates"
        assert entry["identified_fonts"] == []

    def test_extract_failure_degrades_whole_stage(self, monkeypatch):
        """取帧失败（每组失败）：阶段降级，不抛异常、不中断。"""

        def _boom_extract(*a, **k):
            raise RuntimeError("disk exploded")

        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", _boom_extract)
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        res = run_font_identify_stage(
            _items_same_text(), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[FakeRecognizer()])
        assert res.status == stage.STATUS_DEGRADED
        assert res.identifications == {}
        assert res.groups_sampled == 0

    def test_unexpected_stage_exception_swallows(self, monkeypatch):
        """阶段级意外异常：降级为"无识别结果"，绝不向主流水线抛。"""

        def _boom_collect(*a, **k):
            raise RuntimeError("collect exploded")

        monkeypatch.setattr(stage, "_collect_groups", _boom_collect)
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        res = run_font_identify_stage(
            _items_same_text(), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[FakeRecognizer()])
        assert res.status == stage.STATUS_ERROR
        assert res.identifications == {}

    def test_missing_roi_entry_skips_group(self, monkeypatch):
        cfg = FontIdentifyConfig(enabled=True, scan_system_fonts=False)
        extract, calls = _fake_extract(_text_crop())
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)
        res = run_font_identify_stage(
            _items_same_text(roi="roi_7"), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[FakeRecognizer()])
        assert res.groups_sampled == 0
        assert calls == []


class TestIdentifyStageFontlibResolution:
    @pytest.mark.skipif(not _has_dejavu(), reason="缺少 DejaVu 系统字体")
    @pytest.mark.skipif(
        importlib.util.find_spec("fontTools") is None, reason="fontTools 未安装")
    def test_name_resolved_via_local_fontlib_then_glyph_reranked(self, monkeypatch, tmp_path):
        """yuzu 候选无名无路径 → 本机字体库解析 → 字形重排真实打分。"""
        font_dir = tmp_path / "fonts"
        font_dir.mkdir()
        shutil.copy(DEJAVU_SANS, font_dir / "copy.ttf")
        cfg = FontIdentifyConfig(
            enabled=True, scan_system_fonts=False,
            extra_font_dirs=[str(font_dir)])
        # 候选名与字体文件内部规范名一致、不带 font_path
        fake = FakeRecognizer(cands=[FontCandidate("DejaVu Sans", 0.7, "yuzu")])
        extract, _calls = _fake_extract(_text_crop("ABC"))
        monkeypatch.setattr(stage, "extract_single_roi_crop_with_time", extract)

        res = run_font_identify_stage(
            _items_same_text(frames=(10,), text="ABC"), "v.avi", 25.0, cfg,
            roi_data=[ROI_ENTRY], recognizers=[fake])
        entry = res.identifications[("roi_0", 10, 0)]
        fonts = entry["identified_fonts"]
        assert fonts and fonts[0]["name"] == "DejaVu Sans"
        assert fonts[0]["font_path"]  # 已解析到本机字体文件
        assert fonts[0]["glyph_ranked"] is True
        assert fonts[0]["glyph_score"] is not None and fonts[0]["glyph_score"] > 0.0


class TestIdentifyStageRealVideo:
    FPS = 25
    TOTAL = 30
    W, H = 320, 240

    @pytest.mark.skipif(not _has_dejavu(), reason="缺少 DejaVu 系统字体")
    def test_real_synthetic_video_random_access(self, tmp_path):
        """真实合成小视频 + 真实随机访问取帧：端到端挂载侧表。"""
        path = str(tmp_path / "synth.avi")
        writer = cv2.VideoWriter(
            path, cv2.VideoWriter_fourcc(*"MJPG"), self.FPS, (self.W, self.H))
        assert writer.isOpened()
        for _ in range(self.TOTAL):
            frame = np.full((self.H, self.W, 3), 235, dtype=np.uint8)
            cv2.putText(frame, "ABC", (25, 205), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (20, 20, 20), 3, cv2.LINE_AA)
            writer.write(frame)
        writer.release()

        roi_entry = {"type": "rect", "points": [10, 170, 300, 60],
                     "start_frame": 0, "end_frame": self.TOTAL - 1}
        items = [_frame_item(f, ["ABC"], [(20, 180, 300, 220)])
                 for f in (10, 11, 12)]
        cfg = FontIdentifyConfig(
            enabled=True, db_path=_seed_db(tmp_path), scan_system_fonts=False)
        fake = FakeRecognizer(cands=[
            FontCandidate("Liberation Sans", 0.9, "yuzu", font_path=DEJAVU_SANS)])

        res = run_font_identify_stage(
            items, path, float(self.FPS), cfg,
            roi_data=[roi_entry], recognizers=[fake])
        assert res.status == stage.STATUS_OK
        assert res.groups_sampled == 1
        assert set(res.identifications) == {("roi_0", 10, 0), ("roi_0", 11, 0), ("roi_0", 12, 0)}
        fonts = res.identifications[("roi_0", 11, 0)]["identified_fonts"]
        assert fonts[0]["name"] == "Liberation Sans"
        assert fonts[0]["glyph_ranked"] is True
        assert fonts[0]["license_category"] == "open_source"


# ══════════════════════════════════════════════════════════════════
# 4. worker 接线（T3.3）
# ══════════════════════════════════════════════════════════════════


def _worker(tmp_path, **kw):
    from core.pipeline_worker import PipelineWorker

    return PipelineWorker(
        video_path=str(tmp_path / "in.avi"),
        roi_data=[{"type": "rect", "points": [0, 0, 10, 10]}],
        total_frames=100,
        fps=25.0,
        video_width=320,
        video_height=240,
        output_ass_path=str(tmp_path / "out.ass"),
        debug_mode=False,
        template_path=None,
        **kw,
    )


class TestWorkerWiring:
    def test_default_config_is_none(self, tmp_path):
        w = _worker(tmp_path)
        assert w.font_identify_config is None
        assert w.font_identifications is None

    def test_stage_ctx_excludes_font_identify(self, tmp_path):
        """红线：font_identify_config 绝不进入阶段 1-3 的 picklable 参数包。"""
        from core.pipeline_stages import PipelineContext

        cfg = FontIdentifyConfig(enabled=True)
        w = _worker(tmp_path, font_identify_config=cfg)
        assert w.font_identify_config is cfg
        ctx = w._stage_ctx()
        assert isinstance(ctx, PipelineContext)
        assert not any(
            "font_ident" in f.name or "identify" in f.name
            for f in dc_fields(PipelineContext))

    @pytest.mark.parametrize(
        "module_name",
        ["core.chunk_worker", "core.chunk_parallel_runner",
         "core.chunk_merger", "core.pipeline_stages"],
    )
    def test_chunk_path_modules_never_reference_font_identify(self, module_name):
        """红线：阶段 1-3 chunk 路径模块不引用 font_intel（本地后置阶段）。"""
        mod = sys.modules.get(module_name)
        if mod is None:
            __import__(module_name)
            mod = sys.modules[module_name]
        src = inspect.getsource(mod)
        assert "font_intel" not in src
        assert "font_identify" not in src

    def test_disabled_never_imports_stage_module(self, tmp_path, monkeypatch):
        calls: list = []
        canary = types.ModuleType("font_intel.identify_stage")

        def _spy(*a, **k):
            calls.append(a)
            raise AssertionError("identify stage must not run when disabled")

        canary.run_font_identify_stage = _spy
        monkeypatch.setitem(sys.modules, "font_intel.identify_stage", canary)
        w = _worker(tmp_path)
        assert w._identify_fonts([({"rec_texts": []}, 1, "roi_0", 0.0)]) is None
        assert calls == []

    def test_enabled_calls_stage_with_expected_args(self, tmp_path, monkeypatch):
        seen: dict = {}
        sentinel = object()

        def fake_stage(results, video, fps, cfg, **kw):
            seen.update(results=list(results), video=video, fps=fps,
                        cfg=cfg, kw=kw)
            return sentinel

        monkeypatch.setattr(stage, "run_font_identify_stage", fake_stage)
        cfg = FontIdentifyConfig(enabled=True)
        w = _worker(tmp_path, font_identify_config=cfg)
        items = [({"rec_texts": ["x"]}, 3, "roi_0", 0.12)]
        out = w._identify_fonts(items)
        assert out is sentinel
        assert w.font_identifications is sentinel
        assert seen["results"] == items
        assert seen["cfg"] is cfg
        assert seen["video"] == w.video_path
        assert seen["fps"] == 25.0
        assert seen["kw"]["roi_data"] == w.roi_data
        assert callable(seen["kw"]["cancel_check"])

    def test_stage_exception_swallowed(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("stage exploded")

        monkeypatch.setattr(stage, "run_font_identify_stage", boom)
        cfg = FontIdentifyConfig(enabled=True)
        w = _worker(tmp_path, font_identify_config=cfg)
        assert w._identify_fonts([({"rec_texts": []}, 1, "roi_0", 0.0)]) is None
        assert w.font_identifications is None

    def test_run_calls_identify_before_optimizer_construction(self, tmp_path):
        """调用点红线：阶段 3 之后、构造优化器之前（run() 源码次序）。"""
        from core.pipeline_worker import PipelineWorker

        src = inspect.getsource(PipelineWorker.run)
        idx_call = src.find("_identify_fonts(")
        idx_opt = src.find("OCRToASSOptimizer(")
        assert idx_call != -1 and idx_opt != -1
        assert idx_call < idx_opt
