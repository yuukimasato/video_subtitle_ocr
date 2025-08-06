# components/video_display.py
import sys
from typing import Optional, List, Dict, Tuple, Union

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QBrush, QColor, QPolygon
from PySide6.QtCore import QPoint, Qt, QRect, QSize, Signal

class VideoFrameLabel(QLabel):
    roi_drawn = Signal()
    def __init__(self):
        super().__init__()
        self._base_pixmap: Optional[QPixmap] = None
        self._scaled_pixmap_size: QSize = QSize(0, 0)
        self._rois_to_draw: List[Union[QRect, QPolygon]] = []
        self._selected_roi_shape: Optional[Union[QRect, QPolygon]] = None
        self._draw_mode: str = 'rect'
        self._is_drawing: bool = False
        self._current_drawing_rect: Optional[QRect] = None
        self._current_drawing_poly_points: List[QPoint] = []
        self.setMouseTracking(True)
    def set_draw_mode(self, mode: str):
        if mode in ['rect', 'poly']: self.clear_current_drawing(); self._draw_mode = mode; self.roi_drawn.emit()
    def get_draw_mode(self) -> str: return self._draw_mode
    def set_base_pixmap(self, pixmap: QPixmap): self._base_pixmap = pixmap; self.update()
    def get_base_pixmap(self) -> Optional[QPixmap]: return self._base_pixmap
    def get_scaled_pixmap_size(self) -> QSize: return self._scaled_pixmap_size
    def mousePressEvent(self, event):
        if not self._base_pixmap: return
        pos = event.position().toPoint()
        if self._draw_mode == 'rect':
            if event.button() == Qt.LeftButton: self._is_drawing = True; self._current_drawing_rect = QRect(pos, pos); self.update()
        elif self._draw_mode == 'poly':
            if event.button() == Qt.LeftButton:
                if not self._is_drawing: self._current_drawing_poly_points = []; self._is_drawing = True
                self._current_drawing_poly_points.append(pos); self.roi_drawn.emit(); self.update()
            elif event.button() == Qt.RightButton:
                if self._is_drawing and len(self._current_drawing_poly_points) > 2: self._is_drawing = False; self.roi_drawn.emit(); self.update()
    def mouseMoveEvent(self, event):
        if self._draw_mode == 'rect' and self._is_drawing: self._current_drawing_rect = QRect(self._current_drawing_rect.topLeft(), event.position().toPoint()).normalized()
        self.update()
    def mouseReleaseEvent(self, event):
        if self._draw_mode == 'rect' and event.button() == Qt.LeftButton and self._is_drawing:
            self._is_drawing = False
            if self._current_drawing_rect and self._current_drawing_rect.width() < 5: self._current_drawing_rect = None
            self.roi_drawn.emit(); self.update()
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            if not self._base_pixmap: return
            scaled_pixmap = self._base_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._scaled_pixmap_size = scaled_pixmap.size()
            x = (self.width() - self._scaled_pixmap_size.width()) // 2; y = (self.height() - self._scaled_pixmap_size.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)
            for shape in self._rois_to_draw:
                is_selected = (shape == self._selected_roi_shape)
                brush_color = QColor(255, 0, 0, 70) if is_selected else QColor(0, 255, 255, 50)
                pen = QPen(Qt.red, 2, Qt.SolidLine) if is_selected else QPen(Qt.cyan, 1, Qt.DashLine)
                painter.setBrush(QBrush(brush_color)); painter.setPen(pen)
                if isinstance(shape, QRect): painter.drawRect(shape)
                elif isinstance(shape, QPolygon): painter.drawPolygon(shape)
            painter.setBrush(QBrush(QColor(255, 255, 0, 60))); painter.setPen(QPen(Qt.yellow, 2, Qt.SolidLine))
            if self._draw_mode == 'rect' and self._current_drawing_rect: painter.drawRect(self._current_drawing_rect)
            elif self._draw_mode == 'poly' and self._current_drawing_poly_points:
                for pt in self._current_drawing_poly_points: painter.drawEllipse(pt, 3, 3)
                if self._is_drawing:
                    painter.drawPolyline(QPolygon(self._current_drawing_poly_points))
                    if self.underMouse(): painter.drawLine(self._current_drawing_poly_points[-1], self.mapFromGlobal(self.cursor().pos()))
                else: painter.drawPolygon(QPolygon(self._current_drawing_poly_points))
        except Exception as e:
            print(f"绘图错误: {str(e)}")
        finally:
            painter.end()
    def is_roi_ready(self) -> bool:
        if self._draw_mode == 'rect': return self._current_drawing_rect is not None and self._current_drawing_rect.width() > 5
        elif self._draw_mode == 'poly': return not self._is_drawing and len(self._current_drawing_poly_points) > 2
        return False
    def get_current_drawing_roi(self) -> Optional[Tuple[int, int, int, int]]:
        if self._current_drawing_rect: return (self._current_drawing_rect.x(), self._current_drawing_rect.y(), self._current_drawing_rect.width(), self._current_drawing_rect.height())
        return None
    def get_current_drawing_poly(self) -> Optional[List[QPoint]]: return self._current_drawing_poly_points if not self._is_drawing and len(self._current_drawing_poly_points) > 2 else None
    def set_rois_to_draw(self, shapes: List[Union[QRect, QPolygon]], selected_shape: Optional[Union[QRect, QPolygon]]): self._rois_to_draw = shapes; self._selected_roi_shape = selected_shape; self.update()
    def clear_current_drawing(self): self._current_drawing_rect = None; self._current_drawing_poly_points = []; self._is_drawing = False; self.update()
    def clear_all_rois(self): self.clear_current_drawing(); self._rois_to_draw = []; self._selected_roi_shape = None; self.update()

class VideoDisplayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.video_label = VideoFrameLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: black;")
        self.layout.addWidget(self.video_label, 1)

        timeline_layout = QHBoxLayout()
        self.timeline_slider = QSlider(Qt.Horizontal)
        
        time_display_layout = QVBoxLayout()
        self.frame_label = QLabel("0/0")
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        time_display_layout.addWidget(self.frame_label)
        time_display_layout.addWidget(self.time_label)
        
        timeline_layout.addWidget(self.timeline_slider)
        timeline_layout.addLayout(time_display_layout)
        self.layout.addLayout(timeline_layout)

