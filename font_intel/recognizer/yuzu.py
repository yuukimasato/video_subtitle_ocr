# font_intel/recognizer/yuzu.py
"""YuzuMarker.FontDetection 候选生成器（T3.1）——torch 完全可选。

模型仓与权重获取方式
--------------------
- 模型：**YuzuMarker.FontDetection**（JeffersonQin，MIT 许可）——首个
  CJK 字体识别 + 样式提取模型（ResNet 骨干，PyTorch）。
  代码仓：https://github.com/JeffersonQin/YuzuMarker.FontDetection
- 权重：HuggingFace Hub 模型仓 ``JeffersonQin/YuzuMarker.FontDetection``
  （上游 README 注明模型文件存放于仓库根目录）。**首次使用时延迟下载**
  到本地缓存 ``$XDG_CACHE_HOME/video_subtitle_ocr/models/yuzu/``（默认
  ``~/.cache/...``，``VSO_FONT_YUZU_WEIGHTS`` 环境变量可整体覆盖权重
  路径；权重文件名随上游发布变化，``DEFAULT_WEIGHTS_FILENAME`` 仅为本
  项目缓存命名约定）。``HF_ENDPOINT`` 环境变量可指向镜像（如
  https://hf-mirror.com）；``huggingface_hub`` 可用时优先经其下载，
  缺失时 urllib 直连 ``{HF_ENDPOINT}/{repo}/resolve/main/{filename}``
  兜底——hub 不进 requirements（不强依赖）。
- 标签表：类别索引 → 字体名的 ``labels.json``（与权重同目录），缺失时
  记为可恢复错误，绝不猜标签。

有界重试下载（硬约束落地）
--------------------------
工作区 AGENTS.md 要求服务端调用走 ``core/llm_client`` 的 429 有界退避；
但那是 **chat API 语义**（tenacity 指数退避 + Retry-After，等待预算
300s）。模型权重下载是静态文件 GET，无 429/Retry-After 语义，不适用
该封装——按方案 §3.2 的同一思想自实现小函数（``bounded_download``/
``ensure_weights``）：最多 3 次尝试、单次 socket 超时上限、总时长
（含重试间隔）预算上限；全程不抛异常，耗尽返回 None（**可恢复错误**：
``reset()`` 后下次调用可重试），绝不阻塞主流水线。

torch 可选与优雅降级
--------------------
torch / torchvision 全部**延迟导入**（函数体内），模块导入零 torch
依赖。未安装 torch 时 ``is_available()`` 为 False、``identify()``
返回空候选——font_intel 其余部分与主流水线零影响（torch 按方案放可选
``requirements-fontintel.txt`` / deb ``Suggests``，本任务不改
requirements，注释说明即可）。

诚实定位（方案 §3.2 / §9）
--------------------------
本测试环境无 torch：真实加载/推理需上游仓库的模型定义代码与权重齐备，
推理路径以**接口 + mock 测试锁定**，不要求本环境真实跑通。yuzu 模型
只吃图像（crop），``identify`` 的 ``text`` 参数按统一识别器接口保留
（仅做非空校验），不参与模型输入。Top-1 准确率约 49%：输出 Top-N 候选
交 ``glyph_rerank`` 重排与人工确认，不承诺全自动准确。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import time
import urllib.request
from typing import Any, Callable, List, Optional, Tuple

from font_intel.recognizer.base import (
    DEFAULT_TOP_N,
    FontCandidate,
    FontRecognizer,
)

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────

#: 识别器来源标识（写入 FontCandidate.source）。
RECOGNIZER_SOURCE = "yuzu"

#: HuggingFace 模型仓（上游 MIT；权重存放于仓库根目录）。
DEFAULT_MODEL_REPO = "JeffersonQin/YuzuMarker.FontDetection"

#: 本项目缓存内的权重/标签默认文件名（缓存命名约定，非上游承诺名）。
DEFAULT_WEIGHTS_FILENAME = "font_detection.ckpt"
DEFAULT_LABELS_FILENAME = "labels.json"

DEFAULT_HF_ENDPOINT = "https://huggingface.co"

# 有界重试下载预算：最多 3 次、单次超时 30s、总时长（含重试间隔）90s。
DOWNLOAD_MAX_ATTEMPTS = 3
DOWNLOAD_TIMEOUT_PER_ATTEMPT = 30.0
DOWNLOAD_TOTAL_BUDGET_SECONDS = 90.0
# 线性退避间隔基数（第 n 次失败后等待 n * 该值，钳到剩余预算）。
_RETRY_BACKOFF_SECONDS = 2.0

# 上游模型输入边长（ResNet 骨干的正方形输入）。
IMG_SIZE = 384

# 加载状态机（state 属性取值）：failed 为可恢复错误（reset() 清除）。
STATE_UNLOADED = "unloaded"
STATE_LOADING = "loading"
STATE_LOADED = "loaded"
STATE_FAILED = "failed"


# ── 路径与环境 ───────────────────────────────────────────────────


def default_cache_dir() -> str:
    """权重本地缓存目录（尊重 XDG_CACHE_HOME 与 VSO_FONT_YUZU_WEIGHTS）。"""
    cache_home = os.environ.get("XDG_CACHE_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(cache_home, "video_subtitle_ocr", "models", "yuzu")


def default_weights_path() -> str:
    env = os.environ.get("VSO_FONT_YUZU_WEIGHTS", "").strip()
    if env:
        return env
    return os.path.join(default_cache_dir(), DEFAULT_WEIGHTS_FILENAME)


def hf_endpoint() -> str:
    """HF 端点：``HF_ENDPOINT`` 镜像支持，缺省官方。"""
    return os.environ.get("HF_ENDPOINT", "").strip().rstrip("/") or DEFAULT_HF_ENDPOINT


def hf_download_url(repo: str, filename: str) -> str:
    """直连下载 URL（resolve/main；镜像经 HF_ENDPOINT 生效）。"""
    return f"{hf_endpoint()}/{repo}/resolve/main/{filename}"


def _torch_available() -> bool:
    """torch 可导入与否（find_spec 探测：不执行模块代码、零副作用）。"""
    try:
        return importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):  # pragma: no cover - 异常 sys.meta_path
        return False


def _load_hf_hub():
    """huggingface_hub 可用则返回模块（延迟导入），否则 None（不强依赖）。"""
    try:
        import huggingface_hub  # noqa: WPS433 — 可选依赖按需导入

        return huggingface_hub
    except Exception:
        return None


# ── 有界重试下载 ─────────────────────────────────────────────────


def bounded_download(
    url: str,
    dest_path: str,
    *,
    max_attempts: int = DOWNLOAD_MAX_ATTEMPTS,
    per_attempt_timeout: float = DOWNLOAD_TIMEOUT_PER_ATTEMPT,
    total_budget: float = DOWNLOAD_TOTAL_BUDGET_SECONDS,
    opener: Optional[Callable] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Optional[str]:
    """单文件 HTTP GET 的有界重试下载（urllib 直连）。

    与 ``core/llm_client`` 的 429 chat 退避的关系（硬约束说明）：llm_client
    面向 chat API（tenacity 指数退避 + Retry-After 语义、总等待预算
    300s）；权重下载是静态文件 GET，无 429/Retry-After 语义，不适用该
    封装——故按方案 §3.2 同一思想自实现本函数：最多 ``max_attempts`` 次、
    单次 socket 超时上限、总时长（含重试间隔）预算上限。**全程不抛
    异常**，耗尽返回 None（可恢复：调用方可稍后重试）。先写
    ``.part`` 临时文件、成功后原子替换，失败不留半截文件冒充权重。
    """
    open_url = opener or urllib.request.urlopen
    deadline = now() + max(0.0, float(total_budget))
    tmp_path = dest_path + ".part"
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        if now() >= deadline:
            break
        try:
            with open_url(url, timeout=float(per_attempt_timeout)) as resp, \
                    open(tmp_path, "wb") as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    if now() > deadline:
                        raise TimeoutError("download exceeded total budget")
                    fh.write(chunk)
            os.replace(tmp_path, dest_path)
            return dest_path
        except Exception as exc:
            logger.debug("权重下载第 %d 次尝试失败: %s", attempt, exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:  # pragma: no cover - 临时文件清理失败无害
                pass
        wait = min(_RETRY_BACKOFF_SECONDS * attempt, max(0.0, deadline - now()))
        if wait > 0:
            sleep(wait)
    return None


def ensure_weights(
    weights_path: str,
    *,
    allow_download: bool = True,
    max_attempts: int = DOWNLOAD_MAX_ATTEMPTS,
    per_attempt_timeout: float = DOWNLOAD_TIMEOUT_PER_ATTEMPT,
    total_budget: float = DOWNLOAD_TOTAL_BUDGET_SECONDS,
    opener: Optional[Callable] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Optional[str]:
    """确保权重就位：已存在直接返回；缺失时有界重试下载。

    ``huggingface_hub`` 可用时经其下载（断点续传与缓存语义由 hub 处理），
    失败/缺失回落 urllib 直连（支持 ``HF_ENDPOINT`` 镜像）。整个函数
    受同一有界预算约束（≤max_attempts 次、≤total_budget 秒）。失败返回
    None（可恢复错误），绝不抛异常、绝不阻塞主流水线。
    """
    if os.path.isfile(weights_path):
        return weights_path
    if not allow_download:
        logger.info("yuzu 权重未就位且未允许下载（allow_download=False）")
        return None
    parent = os.path.dirname(os.path.abspath(weights_path)) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        logger.warning("yuzu 权重缓存目录创建失败: %s (%s)", parent, exc)
        return None

    hub = _load_hf_hub()
    url = hf_download_url(DEFAULT_MODEL_REPO, DEFAULT_WEIGHTS_FILENAME)
    deadline = now() + max(0.0, float(total_budget))
    attempts = 0
    while attempts < max(1, int(max_attempts)) and now() < deadline:
        attempts += 1
        if hub is not None:
            try:
                got = hub.hf_hub_download(
                    repo_id=DEFAULT_MODEL_REPO,
                    filename=DEFAULT_WEIGHTS_FILENAME,
                    local_dir=parent,
                )
                if got and os.path.isfile(str(got)):
                    if os.path.abspath(str(got)) != os.path.abspath(weights_path):
                        os.replace(str(got), weights_path)
                    return weights_path
            except Exception as exc:
                logger.warning("hf_hub_download 第 %d 次尝试失败: %s", attempts, exc)
        else:
            remaining = deadline - now()
            got = bounded_download(
                url, weights_path,
                max_attempts=1,
                per_attempt_timeout=min(
                    float(per_attempt_timeout), max(0.1, remaining)),
                total_budget=max(0.1, remaining),
                opener=opener, sleep=sleep, now=now,
            )
            if got:
                return got
        wait = min(_RETRY_BACKOFF_SECONDS * attempts, max(0.0, deadline - now()))
        if wait > 0:
            sleep(wait)
    logger.warning(
        "yuzu 权重下载有界重试耗尽（attempts=%d，预算 %.0fs）——返回可恢复"
        "错误状态，不阻塞主流水线；稍后重试或经 VSO_FONT_YUZU_WEIGHTS "
        "手工放置权重。", attempts, float(total_budget),
    )
    return None


# ── 识别器 ───────────────────────────────────────────────────────


class YuzuFontRecognizer(FontRecognizer):
    """YuzuMarker.FontDetection 候选生成器（Top-N + softmax 置信度）。

    构造零重活：不加载模型、不联网、不 import torch；加载与下载全部
    延迟到首次 ``identify()``。加载失败为**可恢复错误**：失败状态在本
    进程内粘滞（字幕组循环不会反复触发下载），``reset()`` 清除后可重试。
    """

    source = RECOGNIZER_SOURCE

    def __init__(
        self,
        weights_path: Optional[str] = None,
        labels_path: Optional[str] = None,
        allow_download: bool = True,
    ) -> None:
        path = str(weights_path).strip() if weights_path else ""
        self.weights_path = path or default_weights_path()
        if labels_path and str(labels_path).strip():
            self.labels_path = str(labels_path)
        else:
            self.labels_path = os.path.join(
                os.path.dirname(os.path.abspath(self.weights_path)) or ".",
                DEFAULT_LABELS_FILENAME,
            )
        self.allow_download = bool(allow_download)
        self._model: Any = None
        self._labels: Optional[List[str]] = None
        self._state = STATE_UNLOADED
        self.last_error = ""

    # -- availability ----------------------------------------------

    @classmethod
    def is_available(
        cls, weights_path: Optional[str] = None, allow_download: bool = True
    ) -> bool:
        """静态判定：torch 可导入 且（权重已存在 或 允许延迟下载）。

        只做 find_spec 与文件存在性检查——**绝不 import torch、绝不联网**。
        """
        if not _torch_available():
            return False
        path = weights_path or default_weights_path()
        if os.path.isfile(path):
            return True
        return bool(allow_download)

    @property
    def available(self) -> bool:
        return type(self).is_available(self.weights_path, self.allow_download)

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        """清除失败状态（可恢复错误的显式重试入口）。"""
        self._state = STATE_UNLOADED
        self._model = None
        self._labels = None
        self.last_error = ""

    # -- identification --------------------------------------------

    def identify(
        self, text: str, crop_image: Any, top_n: int = DEFAULT_TOP_N
    ) -> List[FontCandidate]:
        """识别字块图像字体，返回 Top-N 候选；任何失败路径返回 []。"""
        try:
            return self._identify(text, crop_image, top_n)
        except Exception as exc:  # 接口契约：绝不向上抛
            self._fail(f"inference_failed: {exc}")
            return []

    def _identify(
        self, text: str, crop_image: Any, top_n: int
    ) -> List[FontCandidate]:
        try:
            top_n = max(1, int(top_n))
        except (TypeError, ValueError):
            top_n = DEFAULT_TOP_N
        if not _torch_available():
            # torch 缺失：可预期降级（不算错误重试对象，但记录状态便于诊断）。
            self._fail("torch_unavailable: torch 未安装（可选依赖，识别跳过）")
            return []
        if not isinstance(text, str) or not text.strip():
            return []
        if crop_image is None:
            return []
        if self._state == STATE_FAILED:
            return []  # 失败粘滞：不反复触发下载/加载（reset() 可重试）
        if self._state != STATE_LOADED and not self._ensure_loaded():
            return []
        results = self._infer_top_n(self._model, crop_image, top_n)
        return [
            FontCandidate(str(name), float(score), RECOGNIZER_SOURCE,
                          self._font_path_for(name))
            for name, score in (results or [])[:top_n]
        ]

    def _font_path_for(self, name: str) -> Optional[str]:
        """按候选名在本机缓存目录中尽力猜测字体文件（不存在则 None）。"""
        guess = os.path.join(
            os.path.dirname(os.path.abspath(self.weights_path)),
            f"{name}.ttf",
        )
        return guess if os.path.isfile(guess) else None

    # -- loading -----------------------------------------------------

    def _ensure_loaded(self) -> bool:
        if self._state == STATE_LOADED:
            return True
        if self._state == STATE_FAILED:
            return False
        self._state = STATE_LOADING
        try:
            import torch  # 延迟导入：torch 完全可选（此处必可用，已探测）
        except Exception as exc:  # pragma: no cover - find_spec 已排除
            self._fail(f"torch_import_failed: {exc}")
            return False
        weights = ensure_weights(
            self.weights_path, allow_download=self.allow_download)
        if not weights:
            self._fail(
                "weights_download_failed（可恢复：reset() 后重试，或经 "
                "VSO_FONT_YUZU_WEIGHTS 手工放置权重）")
            return False
        labels = self._load_labels()
        if labels is None:
            self._fail("labels_missing: labels.json 缺失或非法（不猜标签）")
            return False
        model = self._load_model(weights)
        if model is None:
            self._fail("model_load_failed（可恢复：核对上游权重与模型定义）")
            return False
        self._model, self._labels = model, labels
        self._state = STATE_LOADED
        logger.info("yuzu 识别器加载完成: weights=%s", weights)
        return True

    def _load_labels(self) -> Optional[List[str]]:
        try:
            with open(self.labels_path, "r", encoding="utf-8") as fh:
                labels = json.load(fh)
        except Exception:
            return None
        if not isinstance(labels, list) or not labels:
            return None
        return [str(item) for item in labels]

    @staticmethod
    def _load_model(weights_path: str) -> Any:
        """加载模型权重（延迟导入 torch）。

        真实推理需要 YuzuMarker.FontDetection 上游仓库的模型定义代码
        （MIT，见模块 docstring）与配套权重；此处以 ``torch.load`` 整体
        反序列化的最佳努力尝试为准，失败返回 None（可恢复错误，不抛
        异常）。测试经 monkeypatch 替换 ``_infer_top_n``/本方法锁定接口。
        """
        try:
            import torch

            return torch.load(str(weights_path), map_location="cpu")
        except Exception as exc:
            logger.warning("yuzu 模型加载失败: %s", exc)
            return None

    # -- inference ---------------------------------------------------

    def _infer_top_n(self, model: Any, crop_image: Any, top_n: int
                     ) -> List[Tuple[str, float]]:
        """前处理（crop → 模型输入）+ 推理 + Top-N ``(name, score)``。

        本环境无 torch：本方法以 mock 测试锁定接口。真实实现路径：
        BGR/灰度 ndarray → RGB → 缩放到 ``IMG_SIZE`` → 归一化 →
        ``torch.softmax`` 后 ``topk``，类别索引经 ``labels.json``
        （``self._labels``）映射为字体名（索引越界回落索引字符串，
        不猜名字）。推理异常由调用方 ``identify`` 统一兜底为空候选。
        """
        import numpy as np
        import torch

        arr = np.asarray(crop_image)
        if arr.ndim not in (2, 3) or min(arr.shape[:2]) < 2:
            return []
        img = arr[:, :, :3] if arr.ndim == 3 else np.stack([arr] * 3, axis=-1)
        img = img.astype(np.float32) / 255.0
        # 最近邻缩放到模型输入尺寸（不引额外依赖；上游用双线性，
        # 识别任务对插值方式不敏感）。
        ys = np.linspace(0, img.shape[0] - 1, IMG_SIZE).astype(int)
        xs = np.linspace(0, img.shape[1] - 1, IMG_SIZE).astype(int)
        tensor = torch.from_numpy(img[ys][:, xs].transpose(2, 0, 1)[None])
        with torch.no_grad():
            logits = model(tensor)
        probs = torch.softmax(logits[0], dim=-1)
        k = min(int(top_n), int(probs.shape[-1]))
        top = torch.topk(probs, k=k)
        labels = self._labels or []
        out: List[Tuple[str, float]] = []
        for idx, val in zip(top.indices.tolist(), top.values.tolist()):
            i = int(idx)
            name = labels[i] if 0 <= i < len(labels) else str(i)
            out.append((name, float(val)))
        return out

    # -- diagnostics -------------------------------------------------

    def _fail(self, reason: str) -> None:
        first = self._state != STATE_FAILED
        self._state = STATE_FAILED
        self.last_error = str(reason)
        if first:
            logger.warning("yuzu 识别器进入可恢复失败状态: %s", reason)
        else:  # 同因重复失败（如逐字幕组循环）只在 debug 级别续记。
            logger.debug("yuzu 识别器维持可恢复失败状态: %s", reason)
