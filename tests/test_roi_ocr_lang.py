# tests/test_roi_ocr_lang.py
"""每 ROI 识别语言(roi["ocr_lang"])的单元测试。

覆盖:
- RoiDefinitionWidget.roi_lang_combo:选项/默认「自动」/ROI 回填/未知值回退;
- RoiEditingMixin:条目组装携带 ocr_lang、自动时不写键、即时写回与
  _sync_selected_roi_panel_flags 兜底;
- ocr_engine_manager.get_engine_for_lang:按语言缓存独立引擎、选项合并、
  set_engine 切换时清缓存;不支持语言覆盖的引擎共享单例;
- run_batch_ocr / OcrOptimizer 批量路径:逐帧按 roi dict 路由到对应引擎;
- refine_executor._ThreadResources.ocr_for_lang:按语言懒建并缓存;
- pipeline_worker.collect_motion_roi_specs:ocr_lang 随规格透传。

GUI 用例需 QApplication(QWidget),先于 conftest 的 QCoreApplication 创建
(offscreen 平台)。
"""

from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from core import ocr_engine_manager as mgr  # noqa: E402
from core.ocr_engine_base import (  # noqa: E402
    BaseOCREngine,
    OCREngineInfo,
    OCREngineRegistry,
)  # noqa: E402
from core.ocr_engine_manager import (  # noqa: E402
    get_engine,
    get_engine_for_lang,
    run_batch_ocr,
    set_engine,
)
from core.ocr_optimizer import OcrOptimizer  # noqa: E402
from core.pipeline_worker import collect_motion_roi_specs  # noqa: E402
from core.refine_executor import StandaloneOCR, _ThreadResources  # noqa: E402
from main_window.roi_editing import RoiEditingMixin  # noqa: E402


# ---------------------------------------------------------------------------
# 测试用引擎:记录自己的语言与调用次数,便于断言路由
# ---------------------------------------------------------------------------

def _empty_ocr():
    return {"dt_polys": [], "rec_polys": [], "rec_texts": [],
            "rec_scores": [], "rec_boxes": []}


class LangProbeEngine(BaseOCREngine):
    """supports_lang_override=True 的桩引擎:按实例记录 lang 与调用量。"""

    engine_id = "lang_probe"
    supports_lang_override = True

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="lang_probe", name="LangProbe", version="0",
            description="per-roi lang test double", supports_gpu=False)

    @classmethod
    def is_available(cls) -> bool:
        return True

    def __init__(self):
        self.lang = None
        self.predict_calls = 0
        self.batch_calls = 0
        self.cleaned = False

    def initialize(self, **kwargs) -> None:
        self.lang = kwargs.get("lang")

    def predict(self, img_input):
        self.predict_calls += 1
        return []

    def predict_batch(self, images):
        self.batch_calls += 1
        return [[] for _ in images]

    def normalize_result(self, raw_result):
        return _empty_ocr()

    def cleanup(self) -> None:
        self.cleaned = True


class NoLangEngine(LangProbeEngine):
    """显式关闭语言覆盖的对照桩(单一多语言模型的行为)。"""

    engine_id = "nolang_probe"
    supports_lang_override = False

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="nolang_probe", name="NoLang", version="0",
            description="no-lang-override test double", supports_gpu=False)


@pytest.fixture
def lang_engine():
    OCREngineRegistry.register(LangProbeEngine)
    set_engine("lang_probe", {"lang": "ch"})
    engine = get_engine()
    assert isinstance(engine, LangProbeEngine)
    yield engine
    OCREngineRegistry._engines.pop("lang_probe", None)
    OCREngineRegistry._engines.pop("nolang_probe", None)


# ---------------------------------------------------------------------------
# GUI:roi_lang_combo 选项/回填/条目组装/写回
# ---------------------------------------------------------------------------

class _FakeVideoLabel:
    def __init__(self, mode="rect", rect=None):
        self._mode = mode
        self._rect = rect

    def get_draw_mode(self):
        return self._mode

    def get_base_pixmap(self):
        return object()

    def get_current_drawing_roi(self):
        return self._rect

    def get_current_drawing_poly(self):
        return None

    def is_roi_ready(self):
        return True

    def clear_current_drawing(self):
        pass


class _FakeWindow(RoiEditingMixin):
    def __init__(self, roi_def, label):
        self.roi_def_widget = roi_def
        self.video_display_widget = SimpleNamespace(video_label=label)
        self.roi_list_widget = SimpleNamespace(
            roi_list_widget=SimpleNamespace(
                currentItem=lambda: None, row=lambda item: -1,
                setCurrentRow=lambda i: None))
        self.control_panel_widget = SimpleNamespace(
            invalidate_color_gate_confirmation=lambda: None)
        self.cap = object()
        self.fps = 25.0
        self.roi_data = []
        self.logger = logging.getLogger("test_roi_ocr_lang")
        self.clipboard_roi = None
        self._suppress_selection_seek = True
        self.seek_video = lambda frame: None
        self.update_all_rois_visibility = lambda: None
        self.update_ui_state = lambda: None
        self.update_roi_list = lambda: None

    def parse_time_or_frame(self, text: str) -> int:
        text = text.strip()
        return int(text) if text.isdigit() else 0


def _make_roi_def():
    from components.roi_definition import RoiDefinitionWidget

    w = RoiDefinitionWidget()
    w.start_time_edit.setText("10")
    w.end_time_edit.setText("20")
    return w


def test_lang_combo_options_and_default():
    roi_def = _make_roi_def()
    combo = roi_def.roi_lang_combo
    assert combo.count() == 13
    assert combo.itemData(0) == ""  # 自动(跟随全局)
    assert combo.currentData() == ""
    data = [combo.itemData(i) for i in range(combo.count())]
    # 与全局「识别语言」同一份取值(含繁中/日语——双语字幕场景的主角)。
    for lang in ("ch", "chinese_cht", "en", "japan", "korean"):
        assert lang in data
    assert combo.toolTip()


def test_lang_combo_backfill_from_roi():
    roi_def = _make_roi_def()
    roi_def.set_color_restrict_from_roi({"ocr_lang": "japan"})
    assert roi_def.roi_lang_combo.currentData() == "japan"
    roi_def.set_color_restrict_from_roi({"ocr_lang": "chinese_cht"})
    assert roi_def.roi_lang_combo.currentData() == "chinese_cht"
    roi_def.set_color_restrict_from_roi({})  # 旧配置无该键 → 自动
    assert roi_def.roi_lang_combo.currentData() == ""
    roi_def.set_color_restrict_from_roi({"ocr_lang": "bogus"})
    assert roi_def.roi_lang_combo.currentData() == ""
    roi_def.set_color_restrict_from_roi(None)  # 新 ROI 复位
    assert roi_def.roi_lang_combo.currentData() == ""


def test_roi_entry_assembly_carries_lang():
    roi_def = _make_roi_def()
    win = _FakeWindow(roi_def, _FakeVideoLabel(rect=(10, 20, 100, 50)))

    roi_def.roi_lang_combo.setCurrentIndex(4)  # japan
    entry = win._create_roi_entry_from_ui()
    assert entry is not None
    assert entry["ocr_lang"] == "japan"

    roi_def.roi_lang_combo.setCurrentIndex(0)  # 自动:不写键,配置保持干净
    entry = win._create_roi_entry_from_ui()
    assert "ocr_lang" not in entry


def test_lang_writeback_and_sync():
    roi_def = _make_roi_def()
    win = _FakeWindow(roi_def, _FakeVideoLabel(rect=(10, 20, 100, 50)))
    win.roi_data = [{
        "start_time": "0:00:00.400", "end_time": "0:00:00.800",
        "start_frame": 10, "end_frame": 20,
        "type": "rect", "points": [10, 20, 100, 50],
    }]
    win.roi_list_widget.roi_list_widget.currentItem = lambda: object()
    win.roi_list_widget.roi_list_widget.row = lambda item: 0

    win.on_roi_lang_changed("chinese_cht")
    assert win.roi_data[0]["ocr_lang"] == "chinese_cht"
    win.on_roi_lang_changed("")
    assert "ocr_lang" not in win.roi_data[0]

    # 兜底同步:面板所见为准写回选中条目。
    roi_def.roi_lang_combo.setCurrentIndex(4)  # japan
    win._sync_selected_roi_panel_flags()
    assert win.roi_data[0]["ocr_lang"] == "japan"
    roi_def.roi_lang_combo.setCurrentIndex(0)
    win._sync_selected_roi_panel_flags()
    assert "ocr_lang" not in win.roi_data[0]


# ---------------------------------------------------------------------------
# 引擎管理器:get_engine_for_lang 缓存/合并选项/切换清理
# ---------------------------------------------------------------------------

def test_get_engine_for_lang_follows_global_for_empty(lang_engine):
    assert get_engine_for_lang("") is lang_engine
    assert get_engine_for_lang(None) is lang_engine
    # 与全局同语言:共享单例,不建新实例。
    assert get_engine_for_lang("ch") is lang_engine


def test_roi_ocr_lang_extraction():
    assert mgr.roi_ocr_lang({"ocr_lang": "japan"}) == "japan"
    assert mgr.roi_ocr_lang({"ocr_lang": None}) == ""
    assert mgr.roi_ocr_lang({}) == ""
    assert mgr.roi_ocr_lang("not-a-dict") == ""
    assert mgr.roi_ocr_lang(None) == ""


def test_get_engine_for_lang_builds_and_caches(lang_engine):
    japan = get_engine_for_lang("japan")
    assert japan is not lang_engine
    assert japan.lang == "japan"
    cht = get_engine_for_lang("chinese_cht")
    assert cht is not japan and cht is not lang_engine
    assert cht.lang == "chinese_cht"
    # 缓存命中:同语言返回同一实例。
    assert get_engine_for_lang("japan") is japan
    assert len(mgr._lang_engines) == 2


def test_set_engine_switch_clears_lang_cache(lang_engine):
    japan = get_engine_for_lang("japan")
    assert mgr._lang_engines
    set_engine("lang_probe", {"lang": "en"})
    assert not mgr._lang_engines
    assert japan.cleaned  # 旧语言引擎已释放
    en_singleton = get_engine()
    assert en_singleton.lang == "en"


def test_engine_without_lang_override_shares_singleton():
    OCREngineRegistry.register(NoLangEngine)
    set_engine("nolang_probe", {"lang": "ch"})
    singleton = get_engine()
    assert get_engine_for_lang("japan") is singleton
    assert not mgr._lang_engines
    OCREngineRegistry._engines.pop("nolang_probe", None)


# ---------------------------------------------------------------------------
# 逐帧路由:run_batch_ocr 与 OcrOptimizer 批量路径
# ---------------------------------------------------------------------------

def test_run_batch_ocr_routes_per_roi(lang_engine):
    japan = get_engine_for_lang("japan")
    frames = [
        ({"ocr_lang": "japan"}, np.zeros((4, 4, 3), np.uint8), 1, "roi_0", 0.0),
        ({"type": "rect"}, np.zeros((4, 4, 3), np.uint8), 2, "roi_1", 0.0),
        ({"ocr_lang": "japan"}, np.zeros((4, 4, 3), np.uint8), 3, "roi_0", 0.0),
    ]
    results = list(run_batch_ocr(iter(frames), str(tmp_dir()), save_json=False))
    assert len(results) == 3
    assert lang_engine.predict_calls == 1
    assert japan.predict_calls == 2


def test_optimizer_batch_path_groups_by_lang(lang_engine):
    japan = get_engine_for_lang("japan")
    optimizer = OcrOptimizer(
        work_dir=str(tmp_dir()), visualize=False, in_memory_mode=True,
        save_ocr_json=False, ocr_engine_id="lang_probe",
        engine_options={"lang": "ch"})
    img = np.zeros((8, 8, 3), np.uint8)
    samples = [
        ({"ocr_lang": "japan"}, img, 1, "roi_0", 0.0),
        ({"type": "rect"}, img, 2, "roi_1", 0.0),
        ({"ocr_lang": "japan"}, img, 3, "roi_0", 0.0),
    ]
    results = optimizer._run_batch_ocr_on_samples(samples)
    assert results is not None and len(results) == 3
    # 双语各走各的引擎:日语实例一个原生 batch(2 帧),单例一个(1 帧)。
    assert japan.batch_calls == 1
    assert lang_engine.batch_calls == 1


# ---------------------------------------------------------------------------
# 边界精修:_ThreadResources.ocr_for_lang
# ---------------------------------------------------------------------------

@pytest.fixture
def tiny_video(tmp_path):
    path = str(tmp_path / "tiny.avi")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 25, (64, 48))
    assert writer.isOpened()
    for _ in range(4):
        writer.write(np.full((48, 64, 3), 60, np.uint8))
    writer.release()
    return path


def test_thread_resources_ocr_for_lang(tiny_video, lang_engine):
    res = _ThreadResources(tiny_video, 25.0, "lang_probe", {"lang": "ch"},
                           0.6, lambda: False)
    try:
        # StandaloneOCR 约定持独立实例(非管理器单例),断言语言而非同一性。
        assert res.ocr.engine.lang == "ch"
        japan = res.ocr_for_lang({"ocr_lang": "japan"})
        assert isinstance(japan, StandaloneOCR)
        assert japan.engine is not res.ocr.engine
        assert japan.engine.lang == "japan"
        assert res.ocr_for_lang({"ocr_lang": "japan"}) is japan  # 缓存
        assert res.ocr_for_lang({}) is res.ocr  # 无覆盖 → 默认引擎
    finally:
        res.close()
    assert japan.engine.cleaned


# ---------------------------------------------------------------------------
# 轨迹管线:ocr_lang 随规格透传
# ---------------------------------------------------------------------------

def test_collect_motion_roi_specs_carries_lang():
    rois = [
        {"write_pose_tags": True, "type": "rect", "points": [10, 10, 100, 40],
         "start_frame": 0, "end_frame": 50, "ocr_lang": "japan"},
        {"write_pose_tags": True, "type": "rect", "points": [10, 60, 100, 40],
         "start_frame": 0, "end_frame": 50},
        {"write_pose_tags": False, "type": "rect", "points": [0, 0, 5, 5],
         "start_frame": 0, "end_frame": 50, "ocr_lang": "en"},
    ]
    specs = collect_motion_roi_specs(rois)
    assert [s.get("ocr_lang") for s in specs] == ["japan", ""]


def tmp_dir() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="vso_lang_test_")
