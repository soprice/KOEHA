from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from koeha.ui.icon_paths import PATHS as BASE_PATHS

PATHS = {**BASE_PATHS, "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>'}


def icon(name, color="#9aabc0", size=20):
    document = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                f'stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
                f'{PATHS[name]}</svg>')
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(document.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
