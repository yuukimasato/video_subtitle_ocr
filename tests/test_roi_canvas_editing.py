# tests/test_roi_canvas_editing.py
"""components/video_display.py 编辑模式纯函数层的单元测试。

不实例化 QWidget（headless 环境没有 QApplication），只验证拖动编辑的几何
数学：控制点命中、整体移动、矩形缩放、多边形顶点拖动，以及画面边界与
最小尺寸钳制。
"""

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QPolygon

from components.video_display import (
    ROI_MIN_EDGE_PX,
    apply_poly_move,
    apply_poly_vertex_move,
    apply_rect_move,
    apply_rect_resize,
    clamp_move_delta,
    hit_poly_vertex,
    hit_rect_handle,
    rect_handle_points,
    shape_contains,
)

BOUNDS = QRect(0, 0, 320, 240)


def _identity(pt: QPoint) -> QPoint:
    """测试用映射：视频坐标 == 组件坐标。"""
    return QPoint(pt.x(), pt.y())


# ---------------------------------------------------------------------------
# 控制点布局与命中
# ---------------------------------------------------------------------------

def test_rect_handle_points_cover_8_positions():
    handles = rect_handle_points(QRect(10, 20, 30, 40))
    assert set(handles) == {"tl", "tm", "tr", "ml", "mr", "bl", "bm", "br"}
    assert handles["tl"] == QPoint(10, 20)
    assert handles["tm"] == QPoint(25, 20)
    assert handles["br"] == QPoint(40, 60)
    assert handles["bm"] == QPoint(25, 60)


def test_hit_rect_handle_within_radius():
    rect = QRect(100, 100, 50, 40)
    assert hit_rect_handle(rect, QPoint(100, 100), _identity) == "tl"
    assert hit_rect_handle(rect, QPoint(150, 120), _identity) == "mr"
    # 距离所有控制点都很远 → 未命中
    assert hit_rect_handle(rect, QPoint(0, 0), _identity) is None


def test_hit_poly_vertex():
    poly = QPolygon([QPoint(10, 10), QPoint(50, 10), QPoint(30, 60)])
    assert hit_poly_vertex(poly, QPoint(50, 10), _identity) == 1
    assert hit_poly_vertex(poly, QPoint(200, 200), _identity) is None


def test_shape_contains_rect_and_poly():
    rect = QRect(10, 10, 40, 20)
    assert shape_contains(rect, QPoint(20, 20))
    assert not shape_contains(rect, QPoint(200, 20))
    poly = QPolygon([QPoint(0, 0), QPoint(100, 0), QPoint(100, 40), QPoint(0, 40)])
    assert shape_contains(poly, QPoint(50, 20))
    assert not shape_contains(poly, QPoint(50, 60))


# ---------------------------------------------------------------------------
# 整体移动
# ---------------------------------------------------------------------------

def test_clamp_move_delta_identity_inside_bounds():
    delta = QPoint(3, 4)
    bbox = QRect(50, 50, 20, 20)
    assert clamp_move_delta(bbox, delta, BOUNDS) == delta


def test_rect_move_clamped_to_bounds():
    rect = QRect(0, 0, 50, 40)
    moved = apply_rect_move(rect, QPoint(-30, 10), BOUNDS)
    assert moved.left() == 0  # 左移被钳制在画面左边界
    assert moved.top() == 10
    moved2 = apply_rect_move(rect, QPoint(400, 0), BOUNDS)
    assert moved2.right() == BOUNDS.right()


def test_poly_move_clamped_to_bounds():
    poly = QPolygon([QPoint(0, 0), QPoint(40, 0), QPoint(40, 20), QPoint(0, 20)])
    moved = apply_poly_move(poly, QPoint(-100, 5), BOUNDS)
    assert moved.boundingRect().left() == 0
    assert moved.boundingRect().top() == 5


# ---------------------------------------------------------------------------
# 矩形缩放
# ---------------------------------------------------------------------------

def test_rect_resize_left_edge_clamps_to_min_size():
    orig = QRect(100, 100, 60, 40)
    # 左边拖过头（越过右边缘）：钳制到最小边长，右边缘锚定不动
    resized = apply_rect_resize(orig, "ml", QPoint(300, 100), BOUNDS)
    assert resized.width() >= ROI_MIN_EDGE_PX
    assert resized.right() == orig.right()


def test_rect_resize_left_edge_clamps_to_frame():
    orig = QRect(100, 100, 60, 40)
    resized = apply_rect_resize(orig, "ml", QPoint(-50, 100), BOUNDS)
    assert resized.left() == 0
    assert resized.right() == orig.right()


def test_rect_resize_corner_moves_only_dragged_edges():
    orig = QRect(10, 10, 100, 80)
    resized = apply_rect_resize(orig, "br", QPoint(200, 50), BOUNDS)
    assert (resized.right(), resized.bottom()) == (200, 50)
    assert (resized.left(), resized.top()) == (10, 10)
    resized2 = apply_rect_resize(orig, "tl", QPoint(5, 5), BOUNDS)
    assert (resized2.left(), resized2.top()) == (5, 5)
    assert resized2.right() == orig.right() and resized2.bottom() == orig.bottom()


# ---------------------------------------------------------------------------
# 多边形顶点拖动
# ---------------------------------------------------------------------------

def test_poly_vertex_move_clamped_and_input_untouched():
    poly = QPolygon([QPoint(10, 10), QPoint(50, 10), QPoint(30, 60)])
    moved = apply_poly_vertex_move(poly, 2, QPoint(400, 30), BOUNDS)
    assert moved[2] == QPoint(319, 30)
    # 原多边形不被修改
    assert poly[2] == QPoint(30, 60)
