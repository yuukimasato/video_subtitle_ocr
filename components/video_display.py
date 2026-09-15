# components/video_display.py
import logging
from typing import Dict, List, Optional, Tuple, Union

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider
from PySide6.QtGui import QPixmap, QPainter, QPen, QBrush, QColor, QPolygon
from PySide6.QtCore import QPoint, Qt, QRect, QSize, Signal, QCoreApplication

logger = logging.getLogger(__name__)

# 编辑模式控制点的命中/绘制半径（组件像素，恒定视觉大小），
# 以及矩形 ROI 的最小边长（视频像素，与 is_roi_ready 的阈值一致）。
ROI_HANDLE_RADIUS_PX = 7.0
ROI_MIN_EDGE_PX = 5

RoiShape = Union[QRect, QPolygon]
RoiItem = Tuple[int, RoiShape]


def rect_handle_points(rect: QRect) -> Dict[str, QPoint]:
    """矩形的 8 个控制点（视频像素坐标），id ∈ _RECT_HANDLES。"""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    mx, my = x + w // 2, y + h // 2
    return {
        "tl": QPoint(x, y), "tm": QPoint(mx, y), "tr": QPoint(x + w, y),
        "ml": QPoint(x, my), "mr": QPoint(x + w, my),
        "bl": QPoint(x, y + h), "bm": QPoint(mx, y + h), "br": QPoint(x + w, y + h),
    }


def _within_handle_radius(a: QPoint, b: QPoint) -> bool:
    dx = a.x() - b.x()
    dy = a.y() - b.y()
    return dx * dx + dy * dy <= ROI_HANDLE_RADIUS_PX * ROI_HANDLE_RADIUS_PX


def hit_rect_handle(rect: QRect, widget_pos: QPoint, video_to_widget) -> Optional[str]:
    """返回被按中的矩形控制点 id（组件像素距离判定），未按中返回 None。"""
    for name, vpt in rect_handle_points(rect).items():
        wpt = video_to_widget(vpt)
        if wpt is not None and _within_handle_radius(wpt, widget_pos):
            return name
    return None


def hit_poly_vertex(poly: QPolygon, widget_pos: QPoint, video_to_widget) -> Optional[int]:
    """返回被按中的多边形顶点序号，未按中返回 None。"""
    for i, vpt in enumerate(poly):
        wpt = video_to_widget(vpt)
        if wpt is not None and _within_handle_radius(wpt, widget_pos):
            return i
    return None


def shape_contains(shape: RoiShape, video_pos: QPoint) -> bool:
    if isinstance(shape, QRect):
        return shape.contains(video_pos)
    return shape.containsPoint(video_pos, Qt.OddEvenFill)


def clamp_move_delta(bbox: QRect, delta: QPoint, bounds: QRect) -> QPoint:
    """把平移量限制在 bbox 仍完全位于 bounds 内的范围内。"""
    dx = max(bounds.left() - bbox.left(), min(delta.x(), bounds.right() - bbox.right()))
    dy = max(bounds.top() - bbox.top(), min(delta.y(), bounds.bottom() - bbox.bottom()))
    return QPoint(dx, dy)


def apply_rect_move(orig: QRect, delta: QPoint, bounds: QRect) -> QRect:
    return orig.translated(clamp_move_delta(orig, delta, bounds))


def apply_poly_move(orig: QPolygon, delta: QPoint, bounds: QRect) -> QPolygon:
    return orig.translated(clamp_move_delta(orig.boundingRect(), delta, bounds))


def apply_rect_resize(orig: QRect, handle: str, pos: QPoint, bounds: QRect) -> QRect:
    """按住 handle 拖到 pos 后的新矩形：对边锚定，且满足最小边长与画面边界。"""
    left, top, right, bottom = orig.left(), orig.top(), orig.right(), orig.bottom()
    if handle in ("tl", "ml", "bl"):
        left = min(max(pos.x(), bounds.left()), right - ROI_MIN_EDGE_PX)
    if handle in ("tr", "mr", "br"):
        right = max(min(pos.x(), bounds.right()), left + ROI_MIN_EDGE_PX)
    if handle in ("tl", "tm", "tr"):
        top = min(max(pos.y(), bounds.top()), bottom - ROI_MIN_EDGE_PX)
    if handle in ("bl", "bm", "br"):
        bottom = max(min(pos.y(), bounds.bottom()), top + ROI_MIN_EDGE_PX)
    return QRect(QPoint(left, top), QPoint(right, bottom))


def apply_poly_vertex_move(poly: QPolygon, vertex_index: int, pos: QPoint, bounds: QRect) -> QPolygon:
    px = max(bounds.left(), min(pos.x(), bounds.right()))
    py = max(bounds.top(), min(pos.y(), bounds.bottom()))
    moved = QPolygon(poly)
    moved[vertex_index] = QPoint(px, py)
    return moved


class VideoFrameLabel(QLabel):
    """Displays the current frame and lets the user draw and edit ROI shapes.

    All ROI shapes (saved overlays, the in-progress drawing and the shape
    being drag-edited) are stored in ORIGINAL VIDEO pixel coordinates. They
    are mapped to the widget only inside paintEvent / mouse handlers via the
    current letterbox geometry, so shapes stay aligned when the window is
    resized, maximized or the splitter is moved after drawing.

    绘制模式三种：'rect'（拖动画矩形）、'poly'（点击顶点画多边形）、
    'edit'（拖动调整已有 ROI：矩形移动/8 点缩放，多边形移动/顶点拖动）。
    """

    roi_drawn = Signal()
    roi_clicked = Signal(int)                     # 编辑模式按中已有形状（roi 索引）
    roi_geometry_edited = Signal(int, str, list)  # (roi 索引, 'rect'|'poly', points)

    def __init__(self):
        super().__init__()
        self._base_pixmap: Optional[QPixmap] = None
        self._scaled_pixmap_size: QSize = QSize(0, 0)
        # Cache for the smooth-scaled frame: key is (base pixmap size, scaled size).
        self._scaled_cache: Optional[QPixmap] = None
        self._scaled_cache_key: Optional[Tuple[int, int, int, int]] = None
        # Overlay shapes as (roi index, shape) pairs, in video coordinates.
        self._roi_items: List[RoiItem] = []
        self._selected_roi_index: int = -1
        self._draw_mode: str = 'rect'
        self._is_drawing: bool = False
        self._current_drawing_rect: Optional[QRect] = None
        self._current_drawing_poly_points: List[QPoint] = []
        # 拖动编辑状态：{'index', 'kind': 'move'|'resize'|'vertex',
        #  'handle'/'vertex', 'orig', 'shape'(实时), 'orig_video_pos'}
        self._edit_state: Optional[Dict] = None
        self._geometry_cache: Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int, float, float]]] = None
        self.setMouseTracking(True)

    def set_draw_mode(self, mode: str):
        if mode in ('rect', 'poly', 'edit'):
            self.clear_current_drawing()
            self._edit_state = None
            self._draw_mode = mode
            self.roi_drawn.emit()

    def get_draw_mode(self) -> str: return self._draw_mode
    def set_base_pixmap(self, pixmap: QPixmap): self._base_pixmap = pixmap; self._scaled_cache = None; self._scaled_cache_key = None; self.update()
    def get_base_pixmap(self) -> Optional[QPixmap]: return self._base_pixmap
    def get_scaled_pixmap_size(self) -> QSize: return self._scaled_pixmap_size

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The widget size feeds the scaled size, so drop the stale scaled frame.
        self._scaled_cache = None
        self._scaled_cache_key = None

    def _get_scaled_pixmap(self) -> Optional[QPixmap]:
        """Smooth-scaled copy of the base pixmap, cached per (base size, scaled size)."""
        if self._base_pixmap is None:
            return None
        key = (
            self._base_pixmap.width(), self._base_pixmap.height(),
            self._scaled_pixmap_size.width(), self._scaled_pixmap_size.height(),
        )
        if self._scaled_cache is not None and self._scaled_cache_key == key:
            return self._scaled_cache
        self._scaled_cache = self._base_pixmap.scaled(
            self._scaled_pixmap_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._scaled_cache_key = key
        return self._scaled_cache

    def _video_size(self) -> QSize:
        return self._base_pixmap.size() if self._base_pixmap else QSize(0, 0)

    def _video_bounds(self) -> Optional[QRect]:
        size = self._video_size()
        if size.width() <= 0 or size.height() <= 0:
            return None
        return QRect(0, 0, size.width(), size.height())

    def _pixmap_geometry(self) -> Optional[Tuple[int, int, float, float]]:
        """Letterbox geometry for the current widget size (cached).

        Returns (offset_x, offset_y, scale_x, scale_y) mapping video pixels to
        widget pixels, or None when no frame is loaded. The scaled size is
        computed arithmetically (same result as KeepAspectRatio) so that
        mapping helpers do not rescale the pixmap on every call.
        """
        if not self._base_pixmap or self._base_pixmap.width() <= 0:
            self._geometry_cache = None
            return None
        widget_size = self.size()
        video_size = self._video_size()
        cached = self._geometry_cache
        if cached is not None and cached[0] == (widget_size.width(), widget_size.height(), video_size.width(), video_size.height()):
            return cached[1]

        video_ratio = video_size.width() / float(video_size.height())
        widget_ratio = widget_size.width() / float(widget_size.height())
        if widget_size.width() <= 0 or widget_size.height() <= 0:
            scaled_w = scaled_h = 0
        elif video_ratio > widget_ratio:
            scaled_w = widget_size.width()
            scaled_h = int(round(scaled_w / video_ratio))
        else:
            scaled_h = widget_size.height()
            scaled_w = int(round(scaled_h * video_ratio))
        self._scaled_pixmap_size = QSize(scaled_w, scaled_h)
        offset_x = (widget_size.width() - scaled_w) // 2
        offset_y = (widget_size.height() - scaled_h) // 2
        scale_x = scaled_w / float(video_size.width())
        scale_y = scaled_h / float(video_size.height())
        geom = (offset_x, offset_y, scale_x, scale_y)
        self._geometry_cache = (
            (widget_size.width(), widget_size.height(), video_size.width(), video_size.height()),
            geom,
        )
        return geom

    def _widget_to_video(self, pos: QPoint) -> Optional[QPoint]:
        geom = self._pixmap_geometry()
        if geom is None: return None
        offset_x, offset_y, scale_x, scale_y = geom
        video_w, video_h = self._video_size().width(), self._video_size().height()
        if scale_x <= 0 or scale_y <= 0: return None
        vx = int(round((pos.x() - offset_x) / scale_x))
        vy = int(round((pos.y() - offset_y) / scale_y))
        vx = max(0, min(vx, video_w - 1))
        vy = max(0, min(vy, video_h - 1))
        return QPoint(vx, vy)

    def _video_to_widget(self, point: QPoint) -> Optional[QPoint]:
        geom = self._pixmap_geometry()
        if geom is None: return None
        offset_x, offset_y, scale_x, scale_y = geom
        wx = int(round(point.x() * scale_x + offset_x))
        wy = int(round(point.y() * scale_y + offset_y))
        return QPoint(wx, wy)

    def _selected_item(self) -> Optional[RoiItem]:
        for idx, shape in self._roi_items:
            if idx == self._selected_roi_index:
                return (idx, shape)
        return None

    def mousePressEvent(self, event):
        if not self._base_pixmap: return
        widget_pos = event.position().toPoint()
        pos = self._widget_to_video(widget_pos)
        if pos is None: return
        if self._draw_mode == 'rect':
            if event.button() == Qt.LeftButton: self._is_drawing = True; self._current_drawing_rect = QRect(pos, pos); self.update()
        elif self._draw_mode == 'poly':
            if event.button() == Qt.LeftButton:
                if not self._is_drawing: self._current_drawing_poly_points = []; self._is_drawing = True
                self._current_drawing_poly_points.append(pos); self.roi_drawn.emit(); self.update()
            elif event.button() == Qt.RightButton:
                if self._is_drawing and len(self._current_drawing_poly_points) > 2: self._is_drawing = False; self.roi_drawn.emit(); self.update()
                elif self._is_drawing:
                    # Too few points to close the path: abandon the in-progress polygon.
                    self.clear_current_drawing(); self.roi_drawn.emit()
        elif self._draw_mode == 'edit':
            if event.button() == Qt.LeftButton:
                self._begin_edit(widget_pos, pos)

    def _begin_edit(self, widget_pos: QPoint, video_pos: QPoint) -> None:
        """编辑模式按下：优先命中选中形状的控制点，其次命中形状主体（移动）。"""
        selected = self._selected_item()
        if selected is not None:
            idx, shape = selected
            if isinstance(shape, QRect):
                handle = hit_rect_handle(shape, widget_pos, self._video_to_widget)
                if handle is not None:
                    self._edit_state = {
                        "index": idx, "kind": "resize", "handle": handle,
                        "orig": QRect(shape), "shape": QRect(shape),
                        "orig_video_pos": video_pos,
                    }
                    self.update(); return
            else:
                vertex = hit_poly_vertex(shape, widget_pos, self._video_to_widget)
                if vertex is not None:
                    self._edit_state = {
                        "index": idx, "kind": "vertex", "vertex": vertex,
                        "orig": QPolygon(shape), "shape": QPolygon(shape),
                        "orig_video_pos": video_pos,
                    }
                    self.update(); return
        # 后绘制者在上层：倒序找第一个包含按点的形状。
        for idx, shape in reversed(self._roi_items):
            if shape_contains(shape, video_pos):
                self.roi_clicked.emit(idx)
                self._selected_roi_index = idx
                shape_copy = QRect(shape) if isinstance(shape, QRect) else QPolygon(shape)
                self._edit_state = {
                    "index": idx, "kind": "move",
                    "orig": shape_copy, "shape": shape_copy,
                    "orig_video_pos": video_pos,
                }
                self.update(); return

    def mouseMoveEvent(self, event):
        if self._draw_mode == 'edit':
            # Only repaint while a drag is actually in progress; with mouse
            # tracking on, an unconditional update() would redraw the frame on
            # every hover.
            if self._edit_state is None: return
            pos = self._widget_to_video(event.position().toPoint())
            if pos is not None: self._update_edit_drag(pos)
            self.update()
            return
        # Only repaint while a shape is actually being drawn; with mouse tracking
        # on, an unconditional update() would redraw the frame on every hover.
        if not self._is_drawing: return
        if self._draw_mode == 'rect':
            pos = self._widget_to_video(event.position().toPoint())
            if pos is not None and self._current_drawing_rect is not None:
                self._current_drawing_rect = QRect(self._current_drawing_rect.topLeft(), pos).normalized()
        self.update()

    def _update_edit_drag(self, pos: QPoint) -> None:
        st = self._edit_state
        bounds = self._video_bounds()
        if st is None or bounds is None: return
        delta = pos - st["orig_video_pos"]
        if st["kind"] == 'move':
            if isinstance(st["orig"], QRect):
                st["shape"] = apply_rect_move(st["orig"], delta, bounds)
            else:
                st["shape"] = apply_poly_move(st["orig"], delta, bounds)
        elif st["kind"] == 'resize':
            st["shape"] = apply_rect_resize(st["orig"], st["handle"], pos, bounds)
        elif st["kind"] == 'vertex':
            st["shape"] = apply_poly_vertex_move(st["orig"], st["vertex"], pos, bounds)

    def mouseReleaseEvent(self, event):
        if self._draw_mode == 'rect' and event.button() == Qt.LeftButton and self._is_drawing:
            self._is_drawing = False
            if self._current_drawing_rect and (self._current_drawing_rect.width() < 5 or self._current_drawing_rect.height() < 5): self._current_drawing_rect = None
            self.roi_drawn.emit(); self.update()
        elif self._draw_mode == 'edit' and event.button() == Qt.LeftButton and self._edit_state is not None:
            self._commit_edit()

    def _commit_edit(self) -> None:
        st = self._edit_state
        self._edit_state = None
        if st is None: return
        shape, orig = st["shape"], st["orig"]
        if shape != orig:
            if isinstance(shape, QRect):
                self.roi_geometry_edited.emit(
                    st["index"], "rect",
                    [shape.x(), shape.y(), shape.width(), shape.height()],
                )
            else:
                self.roi_geometry_edited.emit(
                    st["index"], "poly", [[p.x(), p.y()] for p in shape]
                )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            if not self._base_pixmap: return
            geom = self._pixmap_geometry()
            if geom is None: return
            offset_x, offset_y, scale_x, scale_y = geom
            scaled_pixmap = self._get_scaled_pixmap()
            if scaled_pixmap is None: return
            painter.drawPixmap(offset_x, offset_y, scaled_pixmap)

            def map_rect(rect: QRect) -> QRect:
                top_left = QPoint(int(rect.x() * scale_x + offset_x), int(rect.y() * scale_y + offset_y))
                bottom_right = QPoint(int((rect.x() + rect.width()) * scale_x + offset_x) - 1,
                                      int((rect.y() + rect.height()) * scale_y + offset_y) - 1)
                return QRect(top_left, bottom_right).normalized()

            def map_poly(poly: QPolygon) -> QPolygon:
                mapped = QPolygon()
                for pt in poly:
                    mapped.append(QPoint(int(pt.x() * scale_x + offset_x), int(pt.y() * scale_y + offset_y)))
                return mapped

            for idx, shape in self._roi_items:
                # 拖动中的形状用实时编辑副本，避免等待数据写回造成的迟滞。
                live_shape = shape
                if self._edit_state is not None and self._edit_state["index"] == idx:
                    live_shape = self._edit_state["shape"]
                is_selected = (idx == self._selected_roi_index)
                brush_color = QColor(255, 0, 0, 70) if is_selected else QColor(0, 255, 255, 50)
                pen = QPen(Qt.red, 2, Qt.SolidLine) if is_selected else QPen(Qt.cyan, 1, Qt.DashLine)
                painter.setBrush(QBrush(brush_color)); painter.setPen(pen)
                if isinstance(live_shape, QRect): painter.drawRect(map_rect(live_shape))
                elif isinstance(live_shape, QPolygon): painter.drawPolygon(map_poly(live_shape))

            if self._draw_mode == 'edit':
                edit_shape = None
                if self._edit_state is not None:
                    edit_shape = self._edit_state["shape"]
                else:
                    selected = self._selected_item()
                    if selected is not None: edit_shape = selected[1]
                if edit_shape is not None:
                    painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
                    painter.setPen(QPen(Qt.red, 1, Qt.SolidLine))
                    hr = int(round(ROI_HANDLE_RADIUS_PX / 2.0)) + 1
                    if isinstance(edit_shape, QRect):
                        handle_pts: List[QPoint] = list(rect_handle_points(edit_shape).values())
                    else:
                        handle_pts = list(edit_shape)
                    for vpt in handle_pts:
                        wpt = self._video_to_widget(vpt)
                        if wpt is not None:
                            painter.drawRect(QRect(wpt.x() - hr, wpt.y() - hr, 2 * hr, 2 * hr))

            painter.setBrush(QBrush(QColor(255, 255, 0, 60))); painter.setPen(QPen(Qt.yellow, 2, Qt.SolidLine))
            if self._draw_mode == 'rect' and self._current_drawing_rect:
                painter.drawRect(map_rect(self._current_drawing_rect))
            elif self._draw_mode == 'poly' and self._current_drawing_poly_points:
                mapped_points = [self._video_to_widget(pt) for pt in self._current_drawing_poly_points]
                for pt in mapped_points:
                    if pt is not None: painter.drawEllipse(pt, 3, 3)
                if self._is_drawing:
                    visible = [pt for pt in mapped_points if pt is not None]
                    if len(visible) >= 2: painter.drawPolyline(QPolygon(visible))
                    cursor_pos = self._widget_to_video(self.mapFromGlobal(self.cursor().pos()))
                    if self.underMouse() and visible and cursor_pos is not None:
                        painter.drawLine(visible[-1], self._video_to_widget(cursor_pos))
                else:
                    painter.drawPolygon(QPolygon([pt for pt in mapped_points if pt is not None]))
        except Exception as e:
            logger.exception(QCoreApplication.translate("VideoFrameLabel", "绘制错误：{}").format(str(e)))
        finally:
            painter.end()
    def is_roi_ready(self) -> bool:
        if self._draw_mode == 'rect': return self._current_drawing_rect is not None and self._current_drawing_rect.width() > 5 and self._current_drawing_rect.height() > 5
        elif self._draw_mode == 'poly': return not self._is_drawing and len(self._current_drawing_poly_points) > 2
        return False
    def get_current_drawing_roi(self) -> Optional[Tuple[int, int, int, int]]:
        """In-progress rect in VIDEO coordinates: (x, y, w, h)."""
        if self._current_drawing_rect: return (self._current_drawing_rect.x(), self._current_drawing_rect.y(), self._current_drawing_rect.width(), self._current_drawing_rect.height())
        return None
    def get_current_drawing_poly(self) -> Optional[List[QPoint]]:
        """In-progress polygon vertices in VIDEO coordinates."""
        return self._current_drawing_poly_points if not self._is_drawing and len(self._current_drawing_poly_points) > 2 else None
    def set_rois_to_draw(self, items: List[RoiItem], selected_index: int = -1):
        """Set overlay shapes as (roi index, shape) pairs; shapes in VIDEO coordinates."""
        self._roi_items = list(items)
        self._selected_roi_index = int(selected_index)
        self.update()
    def clear_current_drawing(self): self._current_drawing_rect = None; self._current_drawing_poly_points = []; self._is_drawing = False; self.update()
    def clear_all_rois(self):
        self.clear_current_drawing()
        self._roi_items = []
        self._selected_roi_index = -1
        self._edit_state = None
        self.update()

class VideoDisplayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.video_label = VideoFrameLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: black;")
        self.main_layout.addWidget(self.video_label, 1)

        timeline_layout = QHBoxLayout()
        self.timeline_slider = QSlider(Qt.Horizontal)

        time_display_layout = QVBoxLayout()
        self.frame_label = QLabel("0/0")
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        time_display_layout.addWidget(self.frame_label)
        time_display_layout.addWidget(self.time_label)

        timeline_layout.addWidget(self.timeline_slider)
        timeline_layout.addLayout(time_display_layout)
        self.main_layout.addLayout(timeline_layout)
