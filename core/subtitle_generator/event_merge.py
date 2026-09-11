# core/subtitle_generator/event_merge.py
import logging
import re
import Levenshtein
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QCoreApplication

from .data_grouping import strip_flicker_punctuation

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate

class _EventMergeMixin:
    """ASS event filtering / near-duplicate merging helpers moved from core/subtitle_generator.py (L151-L345)."""

    def _canonical_subtitle_plain_for_compare(self, body: str) -> str:
        """去除 ASS \\n/\\N 断行并把疑似 OCR 单行噪声（如尾随单字 ''中''）从对比用字符串中摘掉。"""
        normalized = body.replace("\r", "")
        segs = re.split(r"(?i)\\[nN]", normalized)
        out: List[str] = []
        for seg in segs:
            z = re.sub(r"\s+", "", seg.strip())
            if not z:
                continue
            if len(z) == 1 and out and sum(len(x) for x in out) >= self.DIALOG_MERGE_MIN_OVERLAP_LEN:
                continue
            out.append(z)
        return "".join(out)

    def _parse_pos_from_tags(self, tags: str) -> Optional[Tuple[float, float]]:
        # tags like "{\\an5\\pos(978,529)}" or "{\\an5\\pos(978.0,529.0)\\frz(8.0)}"
        m = re.search(r"\\pos\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", tags or "")
        if not m:
            return None
        try:
            return float(m.group(1)), float(m.group(2))
        except Exception:
            return None

    def _scene_tags_close(self, tags_a: str, tags_b: str) -> bool:
        pa = self._parse_pos_from_tags(tags_a)
        pb = self._parse_pos_from_tags(tags_b)
        if pa is None or pb is None:
            return tags_a == tags_b
        tol = int(max(0, self.SCENE_POS_TOLERANCE_PX))
        return abs(pa[0] - pb[0]) <= tol and abs(pa[1] - pb[1]) <= tol

    def _is_noise_body(self, body: str) -> bool:
        b = (body or "").replace("\r", "").strip()
        if not b:
            return True
        # Remove ASS explicit line breaks for judgement
        z = re.sub(r"(?i)\\[nN]", "", b)
        z = re.sub(r"\s+", "", z)
        if not z:
            return True
        if z in ("()", "（）", "[]", "【】", "{}"):
            return True
        if re.fullmatch(r"[\(\)\[\]\{\}（）【】]+", z):
            return True
        if re.fullmatch(r"[0-9]+", z):
            return True
        if re.fullmatch(r"[0-9]+[A-Za-z]+", z) or re.fullmatch(r"[A-Za-z]+[0-9]+", z):
            # 常见 OCR 垃圾：短促闪烁的编号/序号
            return len(z) <= 4
        if len(z) == 1 and z not in ("，", "。", "！", "？", ".", "!", "?"):
            return True
        return False

    def _filter_events(self, events: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not events:
            return []
        out: List[Dict[str, str]] = []
        for ev in events:
            body = ev.get("body", "")
            style = str(ev.get("style", ""))
            if style == "Scene":
                dur = self._event_duration_sec(ev)
                canon = self._canonical_subtitle_plain_for_compare(body)
                if self._is_noise_body(body):
                    continue
                if dur > 0 and dur < float(self.SCENE_EVENT_MIN_DURATION_SEC) and len(canon) < int(self.SCENE_EVENT_MIN_TEXT_LEN):
                    continue
            else:
                # 过滤全局明显垃圾
                if self._is_noise_body(body):
                    continue
            out.append(ev)
        return out

    def _dialogue_bodies_similar(
        self, body_a: str, body_b: str, gap_sec: Optional[float] = None
    ) -> bool:
        ca = strip_flicker_punctuation(self._canonical_subtitle_plain_for_compare(body_a))
        cb = strip_flicker_punctuation(self._canonical_subtitle_plain_for_compare(body_b))
        if not ca or not cb:
            return False
        # 子串包含只适合解释同一条字幕的闪烁/缺读，要求两事件在时间上几乎
        # 相接；间隔较大时（如「真的假的？」结束后 0.7s 出现的新台词「真的」）
        # 子串关系不能作为合并依据。Levenshtein 高相似不受间隔护栏约束。
        substring_allowed = gap_sec is None or gap_sec <= self.DIALOG_MERGE_SUBSTRING_MAX_GAP_SEC
        if len(ca) < self.DIALOG_MERGE_MIN_OVERLAP_LEN or len(cb) < self.DIALOG_MERGE_MIN_OVERLAP_LEN:
            shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
            if substring_allowed and shorter in longer:
                return True
        ratio = Levenshtein.ratio(ca, cb)
        if ratio >= self.DIALOG_MERGE_MIN_RATIO:
            return True
        if substring_allowed and len(ca) >= self.DIALOG_MERGE_MIN_OVERLAP_LEN and len(cb) >= self.DIALOG_MERGE_MIN_OVERLAP_LEN:
            return ca in cb or cb in ca
        return False

    def _pick_majority_dialogue_body(self, bodies: Sequence[str]) -> str:
        from collections import Counter

        # 投票键做尾部省略号归一化："好熱" 与 "好熱…" 是同一条字幕的抖动读法，
        # 合并后取更长的原始读法（保留完整省略号），而不是让丢标点的多数读法
        # 把完整文本顶掉。
        keyed = [
            (strip_flicker_punctuation(self._canonical_subtitle_plain_for_compare(b)), b)
            for b in bodies
        ]
        counter = Counter(c for c, _ in keyed)
        best_votes = max(counter.values())
        top_canons = sorted(
            (c for c, v in counter.items() if v == best_votes),
            key=lambda s: (-len(s), s),
        )
        best_canon = top_canons[0]
        candidates = [raw for canon, raw in keyed if canon == best_canon]
        return max(candidates, key=lambda s: len(s))

    def _merge_temporal_near_duplicate_events(self, events: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if len(events) < 2:
            return events
        events_sorted = sorted(
            events,
            key=lambda e: (
                str(e["roi"]),
                self._parse_ass_time_to_seconds(e["start_time"]),
            ),
        )
        merged: List[Dict[str, str]] = []
        i = 0
        while i < len(events_sorted):
            grp = [events_sorted[i]]
            i += 1
            while i < len(events_sorted):
                prev = grp[-1]
                cur = events_sorted[i]
                if (
                    prev["roi"] != cur["roi"]
                    or prev["style"] != cur["style"]
                ):
                    break
                # Scene: allow small position jitter when merging.
                if prev["style"] == "Scene":
                    if not self._scene_tags_close(prev.get("tags", ""), cur.get("tags", "")):
                        break
                else:
                    if prev.get("tags", "") != cur.get("tags", ""):
                        break
                gap = self._dialogue_time_gap_seconds(prev["end_time"], cur["start_time"])
                if gap > self.DIALOG_MERGE_MAX_GAP_SEC or gap < -0.12:
                    break
                if not self._dialogue_bodies_similar(prev["body"], cur["body"], gap_sec=gap):
                    break
                grp.append(cur)
                i += 1
            if len(grp) == 1:
                merged.append(dict(grp[0]))
                continue
            body = self._pick_majority_dialogue_body([g["body"] for g in grp])
            tags = grp[0].get("tags", "")
            if grp[0].get("style") == "Scene":
                # pick a representative position (median) to stabilize pos jitter
                tags = self._median_scene_tags(grp)
            merged.append(
                {
                    "roi": grp[0]["roi"],
                    "start_time": grp[0]["start_time"],
                    "end_time": grp[-1]["end_time"],
                    "style": grp[0]["style"],
                    "tags": tags,
                    "body": body,
                }
            )
        if len(merged) < len(events):
            logger.info(
                _tr("OCRToASSOptimizer", "Merged {} fragmented ASS lines into {} dialogue events.").format(
                    len(events), len(merged)
                )
            )
        return self._clamp_same_roi_event_overlaps(merged)

    def _clamp_same_roi_event_overlaps(
        self, events: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """同一 ROI、同样式的相邻事件端点齐平：前一条结束不越过下一条开始。

        帧级时间推导（末帧时间 + 帧距）在取样/精修后可能比下一组首帧时间多出
        几毫秒，重叠会让播放器在切换帧上同时画出两条字幕；统一截到下一条的
        开始时间，得到与人工字幕一致的首尾相接时间轴。只处理小重叠（毫秒级
        误差），不触碰真正跨条的事件。
        """
        events_sorted = sorted(
            events,
            key=lambda e: (
                str(e["roi"]),
                self._parse_ass_time_to_seconds(e["start_time"]),
            ),
        )
        for prev, cur in zip(events_sorted, events_sorted[1:]):
            if prev.get("roi") != cur.get("roi") or prev.get("style") != cur.get("style"):
                continue
            prev_end = self._parse_ass_time_to_seconds(prev["end_time"])
            cur_start = self._parse_ass_time_to_seconds(cur["start_time"])
            if 0 < prev_end - cur_start <= 0.2:
                prev["end_time"] = cur["start_time"]
        return events_sorted

    def _median_scene_tags(self, grp: Sequence[Dict[str, str]]) -> str:
        r"""Representative tag for a group of Scene events (median position).

        Returns the raw tag string of the event whose parsed position is the
        median, so tags beyond \pos (e.g. rotation from ROI pose metadata)
        survive the merge instead of being silently dropped.
        """
        if not grp:
            return ""
        parsed = [
            (self._parse_pos_from_tags(g.get("tags", "")), g.get("tags", ""))
            for g in grp
        ]
        poss = [p for p, _ in parsed if p is not None]
        if not poss:
            return grp[0].get("tags", "")
        xs = sorted(p[0] for p in poss)
        ys = sorted(p[1] for p in poss)
        mx = xs[len(xs) // 2]
        my = ys[len(ys) // 2]
        # Keep the tags of the event whose position is closest to the median.
        def _dist(p: Tuple[float, float]) -> float:
            return abs(p[0] - mx) + abs(p[1] - my)
        best_tags = grp[0].get("tags", "")
        best_dist = None
        for p, tags in parsed:
            if p is None:
                continue
            d = _dist(p)
            if best_dist is None or d < best_dist:
                best_dist = d
                best_tags = tags
        return best_tags
