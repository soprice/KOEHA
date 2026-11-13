"""Engine inventory with explicit local checks and targeted repairs."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from koeha.core.i18n import tr
from koeha.models.catalog import Progress
from koeha.models.engines import ENGINES, installed


class EnginesPanel(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.rows = {}
        self.reports = {}
        self.busy = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        hint = QLabel(tr("Check installed files before repairing. Model weights are managed separately in Models."))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search engines…"))
        self.search.setAccessibleName(tr("Search engines"))
        self.search.textChanged.connect(self.filter_rows)
        layout.addWidget(self.search)
        for engine in ENGINES:
            panel = QFrame()
            panel.setObjectName("card")
            content = QVBoxLayout(panel)
            content.setContentsMargins(14, 12, 14, 12)
            top = QHBoxLayout()
            name = QLabel(engine.name)
            name.setObjectName("panelTitle")
            top.addWidget(name)
            version = QLabel(engine.version)
            version.setObjectName("versionBadge")
            top.addWidget(version)
            top.addStretch()
            check = QPushButton(tr("Check"))
            check.clicked.connect(lambda checked=False, eid=engine.id: controller.check_runtime(eid))
            top.addWidget(check)
            delete = QPushButton(tr("Delete"))
            delete.setObjectName("stop")
            delete.setVisible(installed(controller.root, engine.id))
            delete.clicked.connect(lambda checked=False, eid=engine.id: self.delete_engine_action(eid))
            top.addWidget(delete)
            repair = QPushButton(tr("Repair" if installed(controller.root, engine.id) else "Download"))
            repair.setObjectName("primary")
            repair.setEnabled(not installed(controller.root, engine.id))
            repair.clicked.connect(lambda checked=False, eid=engine.id: controller.check_runtime(eid, repair=True))
            top.addWidget(repair)
            cancel = QPushButton(tr("Cancel"))
            cancel.clicked.connect(lambda checked=False, eid=engine.id: controller.cancel("engine:" + eid))
            cancel.hide()
            top.addWidget(cancel)
            content.addLayout(top)
            description = QLabel(tr(engine.description))
            description.setObjectName("muted")
            description.setWordWrap(True)
            content.addWidget(description)
            state = QLabel(tr("Not checked" if installed(controller.root, engine.id) else "Not installed"))
            state.setObjectName("muted")
            state.setWordWrap(True)
            state.setTextFormat(Qt.TextFormat.PlainText)
            content.addWidget(state)
            details = QPlainTextEdit()
            details.setReadOnly(True)
            details.setAccessibleName(tr("Check details"))
            details.setMaximumHeight(120)
            details.hide()
            content.addWidget(details)
            bar = QProgressBar()
            bar.setRange(0, 0)
            bar.setTextVisible(False)
            bar.hide()
            content.addWidget(bar)
            self.rows[engine.id] = dict(panel=panel, check=check, delete=delete, repair=repair, cancel=cancel,
                                        state=state, details=details, bar=bar, engine=engine)
            layout.addWidget(panel)
        layout.addStretch()
        controller.engine_progress.connect(self.on_progress)
        controller.engine_checked.connect(self.on_report)
        controller.engine_busy.connect(self.on_busy)
        controller.models_changed.connect(self.refresh_rows)

    def filter_rows(self, text):
        for row in self.rows.values():
            engine = row["engine"]
            row["panel"].setVisible(text.casefold() in (engine.name + " " + tr(engine.description)).casefold())

    def on_progress(self, engine_id, update):
        row = self.rows[engine_id]
        row["state"].setText(" · ".join(tr(part) for part in (update.phase, update.task, update.detail) if part)
                             if isinstance(update, Progress) else tr(str(update)))
        if isinstance(update, Progress) and update.fraction is not None:
            row["bar"].setRange(0, 1000)
            row["bar"].setValue(round(update.fraction * 1000))
        else:
            row["bar"].setRange(0, 0)

    def on_report(self, engine_id, report):
        self.reports[engine_id] = report
        row = self.rows[engine_id]
        state = ((tr("Healthy") + f" · {report.checked_files} " + tr("files checked"))
                 if report.healthy else f"{len(report.issues)} " + tr("issues found"))
        if report.healthy and not report.verified:
            state += "\n" + tr("Installed before hash verification. Repair reinstalls it from verified "
                               "packages (cached downloads are reused and re-checked).")
        if report.busy:
            state = tr("Busy: another KOEHA window is installing, checking, repairing or deleting "
                       "this runtime. Check again when it finishes.")
        row["state"].setText(state)
        row["details"].setPlainText("\n\n".join(
            (issue.get("package", "") + ": " if issue.get("package") else "") + issue["detail"]
            for issue in report.issues))
        row["details"].setVisible(not report.healthy and not report.busy)

    def on_busy(self, engine_id, active):
        if active:
            self.busy.add(engine_id)
            self.reports.pop(engine_id, None)
        else:
            self.busy.discard(engine_id)
        self.refresh_rows()
        self.rows[engine_id]["cancel"].setVisible(active)
        self.rows[engine_id]["bar"].setVisible(active)

    def refresh_rows(self):
        for eid, row in self.rows.items():
            is_installed = installed(self.controller.root, eid)
            row["check"].setEnabled(not self.busy)
            row["delete"].setVisible(is_installed)
            row["delete"].setEnabled(not self.busy)
            report = self.reports.get(eid)
            row["repair"].setEnabled(not self.busy and (report is None or not report.busy) and (
                (report is not None and not (report.healthy and report.verified)) or not is_installed))
            row["repair"].setText(tr("Repair" if is_installed else "Download"))

    def delete_engine_action(self, engine_id):
        if self.busy:
            return
        engine = self.rows[engine_id]["engine"]
        prompt = tr("Delete the installed {engine} runtime?").format(engine=engine.name)
        if QMessageBox.question(self, tr("Delete runtime"), prompt) == QMessageBox.StandardButton.Yes:
            if self.controller.delete_runtime(engine_id):
                self.reports.pop(engine_id, None)
                row = self.rows[engine_id]
                row["state"].setText(tr("Not installed"))
                row["details"].clear()
                row["details"].hide()
                self.refresh_rows()
