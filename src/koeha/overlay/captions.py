from collections import OrderedDict
from dataclasses import replace

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
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
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from koeha.core.settings import Settings

STYLE_FIELDS = (
    "subtitle_style", "typeface", "text_size", "source_text_color", "target_text_color",
    "text_outline_enabled", "source_outline_color", "target_outline_color", "text_outline_width",
    "background_color", "panel_opacity", "frame_color", "frame_width", "corner_radius",
    "original_visible", "text_shadow",
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


class CaptionCanvas(QWidget):
    contentChanged = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = replace(settings or Settings())
        self.original = ""
        self.translation = ""
        self._glyph_paths = OrderedDict()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Caption preview")

    def apply_settings(self, settings):
        self.settings = replace(settings)
        self.updateGeometry()
        self.update()
        self.contentChanged.emit()

    def set_caption(self, original, translation=""):
        if (original, translation) == (self.original, self.translation):
            return
        self.original, self.translation = original, translation
        self.setAccessibleName("\n".join(filter(None, (original, translation))))
        self.updateGeometry()
        self.update()
        self.contentChanged.emit()

    def _layout(self, width):
        settings = self.settings
        stroke = settings.text_outline_width if settings.text_outline_enabled else 0
        available = max(1, width - 40 - stroke)
        groups = []
        for original, text in [(True, self.original), (False, self.translation)]:
            if not text or original and not settings.original_visible and self.translation:
                continue
            font = QFont(settings.typeface)
            font.setPixelSize(max(10, round(settings.text_size * .65)) if original and self.translation
                              else settings.text_size)
            font.setWeight(QFont.Weight.DemiBold)
            metrics = QFontMetricsF(font)
            lines = wrap_text(text, metrics, available)
            line_height = max(metrics.height() + stroke, font.pixelSize() * 1.32)
            groups.append((original, font, metrics, lines, line_height))
        height = sum(len(g[3]) * g[4] for g in groups) + max(0, len(groups) - 1) * 4
        return groups, max(48, height + 28 + stroke)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return round(self._layout(width)[1])

    def sizeHint(self):
        return QSize(560, self.heightForWidth(max(200, self.width())))

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

    def paintEvent(self, event):
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
        groups, height = self._layout(self.width())
        y = max(14, (self.height() - height) / 2 + 14)
        if settings.text_outline_enabled:
            y += settings.text_outline_width / 2
        for original, font, metrics, lines, line_height in groups:
            color = QColor(settings.source_text_color if original else settings.target_text_color)
            outline = QColor(settings.source_outline_color if original else settings.target_outline_color)
            for line in lines:
                x = max(20, (self.width() - metrics.horizontalAdvance(line)) / 2)
                path = self._glyph_path(font, line)
                painter.save()
                painter.translate(x, y + metrics.ascent())
                if settings.text_shadow:
                    painter.save()
                    painter.translate(1, 2)
                    painter.fillPath(path, QColor(0, 0, 0, 180))
                    painter.restore()
                if settings.text_outline_enabled and settings.text_outline_width:
                    pen = QPen(outline, settings.text_outline_width)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.strokePath(path, pen)
                painter.fillPath(path, color)
                painter.restore()
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
    def __init__(self, settings=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("KOEHA Captions")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.canvas = CaptionCanvas(settings, self)
        layout.addWidget(self.canvas)
        self.resize(900, 150)
        self.canvas.set_caption("KOEHA · Waiting for audio")
        self.canvas.contentChanged.connect(self._fit_height)
        self.drag = None
        self.placed = False

    def apply_settings(self, settings):
        self.canvas.apply_settings(settings)

    def set_caption(self, original, translation=""):
        self.canvas.set_caption(original, translation)

    def _fit_height(self):
        height = self.canvas.heightForWidth(self.width() - 20) + 20
        available = self.screen().availableGeometry()
        height = min(height, available.height())
        if self.height() != height:
            self.resize(self.width(), height)
        if self.isVisible() and self.geometry().bottom() > available.bottom():
            self.move(self.x(), max(available.top(), available.bottom() - height + 1))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "canvas"):
            QTimer.singleShot(0, self._fit_height)

    def showEvent(self, event):
        self._fit_height()
        # Place at bottom center once; afterwards keep where the user dragged it unless it is off-screen.
        if not self.placed or QGuiApplication.screenAt(self.geometry().center()) is None:
            screen = self.screen().availableGeometry()
            self.move(screen.center().x() - self.width() // 2, screen.bottom() - self.height() - 70)
            self.placed = True
        super().showEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            self.move(event.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, event):
        self.drag = None
