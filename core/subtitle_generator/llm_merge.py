# core/subtitle_generator/llm_merge.py
import logging
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QCoreApplication

from concurrent.futures import ThreadPoolExecutor, as_completed

from core.subtitle_llm_polish import (
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    _MAX_CONCURRENT_REQUESTS_CAP,
    SubtitlePolisherConfig,
    deepseek_merge_fragment_text,
)

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate

class _LlmMergeMixin:
    """LLM-assisted fragmented-event merge helpers moved from core/subtitle_generator.py (L347-L462)."""

    def _llm_merge_fragmented_events(
        self,
        events: List[Dict[str, str]],
        cfg: SubtitlePolisherConfig,
        *,
        cancel_check: Callable[[], bool] = lambda: False,
        polish_progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> List[Dict[str, str]]:
        """
        Use LLM to merge fragmented events by choosing the most complete/accurate body
        and merging time range (min start, max end). This targets short "Scene" fragments.

        分两阶段执行：先纯逻辑分组（无 LLM），再对多事件组并发调用
        deepseek_merge_fragment_text（互不依赖），最后按原顺序组装结果。
        """
        if not events or cfg is None or not getattr(cfg, "fragment_merge_enabled", False):
            return events
        if not getattr(cfg, "api_key", ""):
            return events

        # Sort by ROI then start time.
        events_sorted = sorted(
            events,
            key=lambda e: (
                str(e.get("roi", "")),
                self._parse_ass_time_to_seconds(str(e.get("start_time", "0:00:00.00"))),
            ),
        )

        # ── 阶段 1：分组（纯逻辑）。与旧实现逐条 break 条件一致。──
        groups: List[List[Dict[str, str]]] = []
        i = 0
        while i < len(events_sorted):
            cur = events_sorted[i]
            # Only attempt for Scene by default (most jittery fragments).
            if str(cur.get("style", "")) != "Scene":
                groups.append([cur])
                i += 1
                continue

            grp = [cur]
            i += 1
            while i < len(events_sorted):
                nxt = events_sorted[i]
                if nxt.get("roi") != cur.get("roi") or nxt.get("style") != cur.get("style"):
                    break
                if not self._scene_tags_close(str(cur.get("tags", "")), str(nxt.get("tags", ""))):
                    break
                try:
                    gap = self._dialogue_time_gap_seconds(str(grp[-1]["end_time"]), str(nxt["start_time"]))
                except Exception:
                    break
                # Keep a fairly tight window to avoid merging different sentences.
                if gap > 0.35 or gap < -0.12:
                    break
                # If next is pure noise, don't merge it in.
                if self._is_noise_body(str(nxt.get("body", ""))):
                    break
                grp.append(nxt)
                i += 1
            groups.append(grp)

        multi_indices = [gi for gi, grp in enumerate(groups) if len(grp) > 1]
        if not multi_indices:
            return events_sorted

        if cancel_check():
            return events_sorted

        # ── 阶段 2：多事件组并发请求 LLM（组与组之间互不依赖）。──
        max_workers = max(
            1,
            min(
                int(getattr(cfg, "max_concurrent_requests", DEFAULT_MAX_CONCURRENT_REQUESTS) or 1),
                _MAX_CONCURRENT_REQUESTS_CAP,
                len(multi_indices),
            ),
        )
        llm_results: Dict[int, Optional[str]] = {}
        llm_fragment_calls = 0

        def _report_call_done() -> None:
            nonlocal llm_fragment_calls
            llm_fragment_calls += 1
            if polish_progress_callback:
                # Step 4 / LLM: reserve 92–93 for fragment-merge API calls
                pct = min(93, 91 + llm_fragment_calls)
                polish_progress_callback(
                    pct,
                    _tr(
                        "OCRToASSOptimizer",
                        "Step 4/4: DeepSeek merging fragmented Scene subtitles (call {})...",
                    ).format(llm_fragment_calls),
                )

        if max_workers <= 1 or len(multi_indices) == 1:
            for gi in multi_indices:
                if cancel_check():
                    break
                grp = groups[gi]
                cands = [g.get("body", "") for g in grp]
                llm_results[gi] = deepseek_merge_fragment_text(cfg, cands, cancel_check=cancel_check)
                _report_call_done()
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for gi in multi_indices:
                    if cancel_check():
                        break
                    cands = [g.get("body", "") for g in groups[gi]]
                    fut = executor.submit(
                        deepseek_merge_fragment_text, cfg, cands, cancel_check=cancel_check
                    )
                    futures[fut] = gi
                for fut in as_completed(futures):
                    llm_results[futures[fut]] = fut.result()
                    _report_call_done()

        if cancel_check():
            return events_sorted

        # ── 阶段 3：按原顺序组装。──
        merged: List[Dict[str, str]] = []
        for gi, grp in enumerate(groups):
            if len(grp) == 1:
                merged.append(dict(grp[0]))
                continue
            cur = grp[0]
            cands = [g.get("body", "") for g in grp]
            best = llm_results.get(gi)
            if not best:
                # Fallback: choose the longest non-empty candidate.
                best = max((str(x) for x in cands), key=lambda s: len(s.strip()), default=str(grp[-1].get("body", "")))

            merged.append(
                {
                    "roi": cur["roi"],
                    "start_time": cur["start_time"],
                    "end_time": grp[-1]["end_time"],
                    "style": cur["style"],
                    # Use a representative position (median) to stabilize pos jitter.
                    "tags": self._median_scene_tags(grp) or cur.get("tags", ""),
                    "body": best,
                }
            )

        if len(merged) < len(events):
            logger.info(
                _tr("OCRToASSOptimizer", "DeepSeek merged fragmented events: {} -> {}.").format(len(events), len(merged))
            )
        return merged

    def _merge_strategy_params_snapshot(self) -> Dict[str, float]:
        return {
            "merge_max_gap_sec": float(self.DIALOG_MERGE_MAX_GAP_SEC),
            "merge_min_ratio": float(self.DIALOG_MERGE_MIN_RATIO),
            "merge_min_overlap_len": float(int(self.DIALOG_MERGE_MIN_OVERLAP_LEN)),
        }

    def _apply_merge_strategy_params(self, params: Dict[str, float]) -> None:
        if "merge_max_gap_sec" in params:
            self.DIALOG_MERGE_MAX_GAP_SEC = float(params["merge_max_gap_sec"])
        if "merge_min_ratio" in params:
            self.DIALOG_MERGE_MIN_RATIO = float(params["merge_min_ratio"])
        if "merge_min_overlap_len" in params:
            self.DIALOG_MERGE_MIN_OVERLAP_LEN = int(params["merge_min_overlap_len"])
