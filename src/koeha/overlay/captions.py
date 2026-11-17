from collections import OrderedDict
from dataclasses import replace

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from koeha.core.i18n import tr
from koeha.core.settings import OVERLAY_LIVE_FULL, OVERLAY_LIVE_OFF, OVERLAY_LIVE_RAW, Settings
from koeha.overlay.playback import OverlayView

STYLE_FIELDS = (
    "subtitle_style", "typeface", "text_size", "source_text_color", "target_text_color",
    "text_outline_enabled", "source_outline_color", "target_outline_color", "text_outline_width",
    "background_color", "panel_opacity", "frame_color", "frame_width", "corner_radius",
    "original_visible", "text_shadow", "overlay_fit_width", "overlay_max_width",
)
PRESETS = {
    "frosted": dict(background_color="#161b22", panel_opacity=.89, frame_color="#55778391",
                    frame_width=1.0, corner_radius=13, text_outline_enabled=False,
                    source_text_color="#c0c5cd", target_text_color="#ffffff", text_shadow=True),
    "opaque": dict(background_color="#090a0b", panel_opacity=1.0, frame_color="#55778391",
                   frame_width=1.0, corner_radius=3, text_outline_enabled=False,
                   source_text_color="#e2e8f0", target_text_color="#ffffff", text_shadow=True),
    "bare": dict(panel_opacity=0.0, frame_width=0.0, corner_radius=0,
                 text_outline_enabled=True, text_outline_width=1.5,
                 source_outline_color="#d9000000", target_outline_color="#d9000000",
                 source_text_color="#f8fafc", target_text_color="#7dd3fc", text_shadow=True),
}
LIVE_MINT = QColor("#a5e5c6")


def matching_preset(settings):
    """Name the preset whose values the style currently has, so undoing an edit restores its selection."""
    def same(left, right):
        return left.lower() == right.lower() if isinstance(left, str) else left == right
    for name, values in PRESETS.items():
        if all(same(getattr(settings, key), value) for key, value in values.items()):
            return name
    return None


def wrap_text(text, metrics, width):
    """Greedy measured wrapping, splitting unspaced tokens without losing glyphs."""
    text = " ".join(str(text).split())
    if not text:
        return []
    lines, current = [], ""
    for token in text.split(" "):
        candidate = (current + " " + token).strip()
        if metrics.horizontalAdvance(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        for char in token:
            candidate = current + char
            if current and metrics.horizontalAdvance(candidate) > width:
                lines.append(current)
                current = char
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines


def _with_alpha(color, factor):
    result = QColor(color)
    result.setAlphaF(max(0.0, min(1.0, result.alphaF() * factor)))
    return result


class CaptionCanvas(QWidget):
    contentChanged = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = replace(settings or Settings())
        self.view = OverlayView()
        self._glyph_paths = OrderedDict()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Caption preview")

    @property
    def original(self):
        return self.view.original

    @property
    def translation(self):
        return self.view.translation

    def apply_settings(self, settings):
        self.settings = replace(settings)
        self.updateGeometry()
        self.update()
        self.contentChanged.emit()

    def set_caption(self, original, translation=""):
        self.set_view(OverlayView(original=original, translation=translation))

    def set_view(self, view):
        if view == self.view:
            return
        self.view = view
        lines = (view.original, view.translation, view.live_original, view.live_translation)
        self.setAccessibleName("\n".join(filter(None, lines)))
        self.updateGeometry()
        self.update()
        self.contentChanged.emit()

    def _build_entries(self):
        """Build render items split into main committed lines and optional live strip lines."""
        settings = self.settings
        view = self.view
        if view.idle_hidden:
            return [], []

        main_items = []
        has_translation = bool(view.translation or view.translation_pending)
        if view.original and (settings.original_visible or not has_translation):
            size = max(10, round(settings.text_size * .65)) if has_translation else settings.text_size
            main_items.append((
                "source", view.original, size, QFont.Weight.DemiBold, False,
                QColor(settings.source_text_color), QColor(settings.source_outline_color), 0,
            ))
        if view.translation:
            alpha = 0.78 if view.translation_draft else 1.0
            main_items.append((
                "target", view.translation, settings.text_size, QFont.Weight.DemiBold,
                view.translation_draft,
                _with_alpha(QColor(settings.target_text_color), alpha),
                _with_alpha(QColor(settings.target_outline_color), alpha),
                0,
            ))
        elif view.translation_pending:
            main_items.append((
                "pending", tr("Translating…"), max(10, round(settings.text_size * .76)),
                QFont.Weight.Medium, True,
                _with_alpha(QColor(settings.target_text_color), 0.55),
                _with_alpha(QColor(settings.target_outline_color), 0.55),
                0,
            ))

        live_items = []
        if view.live_original:
            live_text = "● " + view.live_original
            stable_len = (2 + len(view.live_stable)) if view.live_stable and view.live_original.startswith(view.live_stable) else 0
            live_items.append((
                "live_source", live_text, max(10, round(settings.text_size * .58)),
                QFont.Weight.Medium, False,
                _with_alpha(LIVE_MINT, 0.92), QColor(settings.source_outline_color), stable_len,
            ))
        if view.live_translation:
            suffix = " …" if view.live_translation_stale else ""
            alpha = 0.55 if view.live_translation_stale else 0.72
            live_items.append((
                "live_target", "~ " + view.live_translation + suffix,
                max(10, round(settings.text_size * .68)), QFont.Weight.Medium, True,
                _with_alpha(QColor(settings.target_text_color), alpha),
                _with_alpha(QColor(settings.target_outline_color), alpha),
                0,
            ))
        elif view.live_translating:
            live_items.append((
                "live_pending", "~ " + tr("Translating…"),
                max(10, round(settings.text_size * .62)), QFont.Weight.Normal, True,
                _with_alpha(QColor(settings.target_text_color), 0.45),
                _with_alpha(QColor(settings.target_outline_color), 0.45),
                0,
            ))
        return main_items, live_items

    def _font_for(self, size, weight, italic):
        font = QFont(self.settings.typeface)
        font.setPixelSize(size)
        font.setWeight(weight)
        font.setItalic(italic)
        return font

    def content_width(self, max_width, min_width=240):
        """Measure the snug pill width that fits all active lines without wrapping unnecessarily."""
        settings = self.settings
        stroke = settings.text_outline_width if settings.text_outline_enabled else 0
        main_items, live_items = self._build_entries()
        if not main_items and not live_items:
            return min(max_width, 320)
        widest = 0.0
        for _, text, size, weight, italic, _, _, _ in (*main_items, *live_items):
            metrics = QFontMetricsF(self._font_for(size, weight, italic))
            widest = max(widest, metrics.horizontalAdvance(" ".join(str(text).split())))
        return max(min(min_width, max_width), min(max_width, round(widest + 48 + stroke)))

    def _layout(self, width):
        settings = self.settings
        stroke = settings.text_outline_width if settings.text_outline_enabled else 0
        available = max(1, width - 40 - stroke)
        main_items, live_items = self._build_entries()
        sections = []
        for items in (main_items, live_items):
            if not items:
                continue
            groups = []
            for kind, text, size, weight, italic, color, outline, stable_len in items:
                font = self._font_for(size, weight, italic)
                metrics = QFontMetricsF(font)
                lines = wrap_text(text, metrics, available)
                line_height = max(metrics.height() + stroke, font.pixelSize() * 1.32)
                groups.append((kind, font, metrics, lines, line_height, color, outline, stable_len))
            sections.append(groups)

        if not sections:
            return [], 48

        height = 0.0
        for s_idx, groups in enumerate(sections):
            if s_idx > 0:
                height += 12  # divider band between committed block and live strip
            height += sum(len(g[3]) * g[4] for g in groups) + max(0, len(groups) - 1) * 4
        return sections, max(48, height + 26 + stroke)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return round(self._layout(width)[1])

    def sizeHint(self):
        max_w = max(300, self.settings.overlay_max_width)
        w = self.content_width(max_w) if self.settings.overlay_fit_width else max_w
        return QSize(w, self.heightForWidth(max(200, self.width() or w)))

    def minimumSizeHint(self):
        return QSize(80, 48)

    def _glyph_path(self, font, text):
        key = (font.key(), text)
        path = self._glyph_paths.get(key)
        if path is None:
            path = QPainterPath()
            path.addText(0, 0, font, text)
            self._glyph_paths[key] = path
            if len(self._glyph_paths) > 256:
                self._glyph_paths.popitem(last=False)
        else:
            self._glyph_paths.move_to_end(key)
        return path

    def _draw_text(self, painter, font, text, x, baseline_y, color, outline):
        settings = self.settings
        path = self._glyph_path(font, text)
        painter.save()
        painter.translate(x, baseline_y)
        if settings.text_shadow:
            painter.save()
            painter.translate(1, 2)
            painter.fillPath(path, QColor(0, 0, 0, min(180, round(color.alphaF() * 180))))
            painter.restore()
        if settings.text_outline_enabled and settings.text_outline_width:
            pen = QPen(outline, settings.text_outline_width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
        painter.fillPath(path, color)
        painter.restore()

    def paintEvent(self, event):
        if self.view.idle_hidden:
            return
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        settings = self.settings
        background = QColor(settings.background_color)
        background.setAlphaF(background.alphaF() * settings.panel_opacity)
        painter.setBrush(background)
        painter.setPen(QPen(QColor(settings.frame_color), settings.frame_width) if settings.frame_width
                       else Qt.PenStyle.NoPen)
        inset = max(.5, settings.frame_width / 2)
        panel = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        painter.drawRoundedRect(panel, settings.corner_radius, settings.corner_radius)
        sections, height = self._layout(self.width())
        if not sections:
            return
        y = max(13, (self.height() - height) / 2 + 13)
        if settings.text_outline_enabled:
            y += settings.text_outline_width / 2
        for s_idx, groups in enumerate(sections):
            if s_idx > 0:
                y += 4
                divider = QColor(255, 255, 255, 32)
                painter.save()
                painter.setPen(QPen(divider, 1.0))
                painter.drawLine(QPoint(24, round(y)), QPoint(max(24, self.width() - 24), round(y)))
                painter.restore()
                y += 8
            for kind, font, metrics, lines, line_height, color, outline, stable_len in groups:
                consumed = 0
                for line in lines:
                    x = max(20, (self.width() - metrics.horizontalAdvance(line)) / 2)
                    if kind == "live_source" and stable_len > consumed:
                        split = min(len(line), stable_len - consumed)
                        stable_part = line[:split]
                        tail_part = line[split:]
                        if stable_part:
                            self._draw_text(painter, font, stable_part, x, y + metrics.ascent(),
                                            QColor("#eef3fa"), outline)
                            x += metrics.horizontalAdvance(stable_part)
                        if tail_part:
                            self._draw_text(painter, font, tail_part, x, y + metrics.ascent(), color, outline)
                    else:
                        self._draw_text(painter, font, line, x, y + metrics.ascent(), color, outline)
                    consumed += len(line) + 1
                    y += line_height
                y += 4


class OverlayScene(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0, QColor("#202731"))
        base.setColorAt(.63, QColor("#0b0c0e"))
        painter.fillRect(self.rect(), base)
        glow = QRadialGradient(self.width() * .75, self.height() * .3, self.width() * .5)
        glow.setColorAt(0, QColor(49, 55, 65, 102))
        glow.setColorAt(1, QColor(49, 55, 65, 0))
        painter.fillRect(self.rect(), glow)
        painter.setPen(QColor(255, 255, 255, 6))
        for x in range(0, self.width(), 36):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 36):
            painter.drawLine(0, y, self.width(), y)


class Overlay(QWidget):
    settingChanged = Signal(dict)
    hideRequested = Signal()

    EDGE_MARGIN = 10

    def __init__(self, settings=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("KOEHA Captions")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumWidth(300)
        self.settings = replace(settings or Settings())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.canvas = CaptionCanvas(self.settings, self)
        layout.addWidget(self.canvas)
        self._build_toolbar()
        self.toolbar.hide()

        self.resize(self.settings.overlay_max_width, 150)
        self.canvas.set_caption("KOEHA · Waiting for audio")
        self.canvas.contentChanged.connect(self._fit_size)
        self.drag = None
        self.resize_edge = None
        self.resize_origin = None
        self.placed = False
        self._fitting = False
        self._anchor_center_x = None
        self._apply_click_through(self.settings.overlay_click_through)

    def _build_toolbar(self):
        bar = QWidget(self)
        self.toolbar = bar
        bar.setStyleSheet(
            "QWidget { background: rgba(15, 19, 22, 220); border: 1px solid #3a4450; border-radius: 7px; }"
            "QPushButton { background: transparent; border: 0; color: #eef3fa; padding: 2px 7px; "
            "font-size: 11px; border-radius: 5px; }"
            "QPushButton:hover { background: rgba(255, 255, 255, 30); }"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(2)
        self.live_cycle_btn = QPushButton()
        self.live_cycle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.live_cycle_btn.clicked.connect(self._cycle_live_mode)
        self.lock_btn = QPushButton(tr("Lock"))
        self.lock_btn.setToolTip(tr("Click-through lock (unlock from Overlay Studio)"))
        self.lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_btn.clicked.connect(lambda: self.settingChanged.emit({"overlay_click_through": True}))
        self.close_btn = QPushButton("×")
        self.close_btn.setToolTip(tr("Hide overlay"))
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.hideRequested.emit)
        for btn in (self.live_cycle_btn, self.lock_btn, self.close_btn):
            row.addWidget(btn)
        bar.adjustSize()
        self._refresh_toolbar()
        return bar

    def _refresh_toolbar(self):
        labels = {
            OVERLAY_LIVE_OFF: tr("Live: Off"),
            OVERLAY_LIVE_RAW: tr("Live: Raw"),
            OVERLAY_LIVE_FULL: tr("Live: Full"),
        }
        self.live_cycle_btn.setText(labels.get(self.settings.overlay_live_mode, tr("Live: Full")))
        self.toolbar.adjustSize()
        self._position_toolbar()

    def _position_toolbar(self):
        self.toolbar.move(max(12, self.width() - self.toolbar.width() - 14), 4)
        self.toolbar.raise_()

    def _cycle_live_mode(self):
        order = (OVERLAY_LIVE_FULL, OVERLAY_LIVE_RAW, OVERLAY_LIVE_OFF)
        current = self.settings.overlay_live_mode
        next_mode = order[(order.index(current) + 1) % len(order)] if current in order else OVERLAY_LIVE_FULL
        self.settingChanged.emit({"overlay_live_mode": next_mode})

    def _apply_click_through(self, locked):
        locked = bool(locked)
        flag_on = bool(self.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        attr_on = self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if flag_on != locked or attr_on != locked:
            was_visible = self.isVisible()
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
            flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            if locked:
                flags |= Qt.WindowType.WindowTransparentForInput
            self.setWindowFlags(flags)
            if was_visible:
                self.show()
        if locked:
            self.toolbar.hide()

    def apply_settings(self, settings):
        self.settings = replace(settings)
        self._apply_click_through(self.settings.overlay_click_through)
        self._refresh_toolbar()
        self.canvas.apply_settings(self.settings)

    def set_caption(self, original, translation=""):
        self.canvas.set_caption(original, translation)

    def set_view(self, view):
        self.canvas.set_view(view)

    def _target_width(self):
        max_w = max(300, self.settings.overlay_max_width)
        if not self.settings.overlay_fit_width:
            return max_w
        return max(300, self.canvas.content_width(max_w - 20, min_width=280) + 20)

    def _fit_size(self):
        if self._fitting:
            return
        self._fitting = True
        try:
            available = self.screen().availableGeometry()
            target_w = min(self._target_width(), available.width())
            target_h = min(self.canvas.heightForWidth(max(200, target_w - 20)) + 20, available.height())
            if self.width() != target_w or self.height() != target_h:
                center_x = self._anchor_center_x if self._anchor_center_x is not None else self.x() + self.width() // 2
                self.resize(target_w, target_h)
                if self.placed:
                    new_x = max(available.left(), min(center_x - target_w // 2, available.right() - target_w + 1))
                    new_y = self.y()
                    if self.isVisible() and new_y + target_h - 1 > available.bottom():
                        new_y = max(available.top(), available.bottom() - target_h + 1)
                    if (new_x, new_y) != (self.x(), self.y()):
                        self.move(new_x, new_y)
            elif self.isVisible() and self.geometry().bottom() > available.bottom():
                self.move(self.x(), max(available.top(), available.bottom() - target_h + 1))
            self._position_toolbar()
        finally:
            self._fitting = False

    def _fit_height(self):
        self._fit_size()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toolbar"):
            self._position_toolbar()
        if hasattr(self, "canvas") and not self._fitting:
            QTimer.singleShot(0, self._fit_size)

    def moveEvent(self, event):
        super().moveEvent(event)
        if not self._fitting and self.placed:
            self._anchor_center_x = self.x() + self.width() // 2

    def showEvent(self, event):
        self._fit_size()
        # Place at bottom center once; afterwards keep where the user dragged it unless it is off-screen.
        if not self.placed or QGuiApplication.screenAt(self.geometry().center()) is None:
            screen = self.screen().availableGeometry()
            self.move(screen.center().x() - self.width() // 2, screen.bottom() - self.height() - 70)
            self._anchor_center_x = self.x() + self.width() // 2
            self.placed = True
        super().showEvent(event)

    def enterEvent(self, event):
        if not self.settings.overlay_click_through and not self.canvas.view.idle_hidden:
            self._position_toolbar()
            self.toolbar.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.toolbar.hide()
        self.unsetCursor()
        super().leaveEvent(event)

    def _edge_at(self, pos):
        if pos.x() <= self.EDGE_MARGIN:
            return "left"
        if pos.x() >= self.width() - self.EDGE_MARGIN:
            return "right"
        return None

    def mousePressEvent(self, event):
        if self.settings.overlay_click_through:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self.resize_edge = edge
                self.resize_origin = (event.globalPosition().toPoint().x(), self.x(), self.width())
            else:
                self.drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.settings.overlay_click_through:
            return
        if self.resize_edge is not None and self.resize_origin is not None:
            start_x, win_x, start_w = self.resize_origin
            dx = event.globalPosition().toPoint().x() - start_x
            new_w = start_w + (-dx * 2 if self.resize_edge == "left" else dx * 2)
            new_w = max(300, min(1600, new_w))
            self._anchor_center_x = win_x + start_w // 2
            self.resize(new_w, self.height())
            self.move(self._anchor_center_x - new_w // 2, self.y())
            return
        if self.drag is not None:
            self.move(event.globalPosition().toPoint() - self.drag)
            self._anchor_center_x = self.x() + self.width() // 2
            return
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._edge_at(event.position().toPoint())
                       else Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, event):
        if self.resize_edge is not None:
            new_max = max(300, min(1600, self.width()))
            self.resize_edge = None
            self.resize_origin = None
            self.settingChanged.emit({"overlay_max_width": new_max})
        self.drag = None
