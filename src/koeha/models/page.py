"""The Models page: download, verify and repair models, with setup progress."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QProgressBar,
)

from koeha.core.i18n import tr
from koeha.models.catalog import (
    MODELS,
    Progress,
    _size,
    by_id,
    has_model_files,
    is_ready,
    known_runtime_damage,
    model_disk_bytes,
)
from koeha.ui.components import button, card, label
from koeha.ui.motion import fade_in, set_progress


class ModelsPage:
    """MainWindow mixin. The Models page: download, verify and repair models, with setup progress.

    Relies on the shell for self.controller, self.page, self.navigate, self.show_error and
    self.update_activity; see koeha.ui.main_window."""

    def build_models(self):
        page, layout = self.page("Models", "Download a model to use it. Downloads only start when you choose them.")
        self.model_delete_buttons = getattr(self, "model_delete_buttons", {})
        for model in MODELS:
            panel, content = card()
            row = QHBoxLayout()
            row.addWidget(label(model.name), 1)
            delete_btn = button("Delete", lambda checked=False, mid=model.id: self.delete_model_action(mid), "stop")
            delete_btn.setToolTip(tr("Delete the downloaded model files from this computer"))
            delete_btn.hide()
            row.addWidget(delete_btn)
            action = button("Download", lambda checked=False, mid=model.id: self.model_action(mid), "primary")
            action.setToolTip(tr("Download the model and its required runtime only when clicked"))
            row.addWidget(action)
            content.addLayout(row)
            content.addWidget(label(f"{model.size}  ·  {model.license}  ·  Local recognition", "muted"))
            content.addWidget(label(model.repo, "muted"))
            state = label("Not installed", "muted")
            content.addWidget(state)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setAccessibleName(tr("Setup progress") + " · " + model.name)
            bar.hide()
            content.addWidget(bar)
            self.model_controls[model.id] = (action, state, bar)
            self.model_delete_buttons[model.id] = delete_btn
            layout.addWidget(panel)
        layout.addStretch()
        return page

    def model_action(self, model_id):
        if model_id in self.controller.jobs:
            self.controller.cancel(model_id)
        else:
            model = by_id(model_id)
            broken = next((runtime for runtime in (model.runtime, "vad")
                           if known_runtime_damage(self.controller.root, runtime)), None)
            if broken:
                self.navigate(4)
                self.settings_sections.select_title("Engines & Frameworks")
                self.controller.check_runtime(broken, repair=True)
                return
            self.controller.prepare(model_id)

    def delete_model_action(self, model_id):
        if self.active or self.controller.jobs:
            self.show_error("Stop the session and wait for current setup work before changing engines or models.")
            return
        model = by_id(model_id)
        size_bytes = model_disk_bytes(self.controller.root, model)
        size_note = f" ({_size(size_bytes)})" if size_bytes > 0 else ""
        prompt = tr("Delete downloaded files for {model}{size}?").format(model=model.name, size=size_note)
        if QMessageBox.question(self, tr("Delete model"), prompt) == QMessageBox.StandardButton.Yes:
            _, state, _ = self.model_controls[model_id]
            state.setProperty("setup_message", None)
            if self.controller.delete_model(model_id):
                self.refresh_models()

    def refresh_models(self):
        delete_buttons = getattr(self, "model_delete_buttons", {})
        for model in MODELS:
            action, state, _ = self.model_controls[model.id]
            delete_btn = delete_buttons.get(model.id)
            if model.id in self.controller.jobs:
                if delete_btn is not None:
                    delete_btn.hide()
                continue
            ready = is_ready(self.controller.root, model)
            has_files = ready or has_model_files(self.controller.root, model)
            broken = any(known_runtime_damage(self.controller.root, runtime) for runtime in (model.runtime, "vad"))
            action.setText(tr("Repair runtime" if broken else "Verify / repair" if ready else "Download"))
            if delete_btn is not None:
                delete_btn.setVisible(has_files)
                delete_btn.setEnabled(not self.active and not bool(self.controller.jobs))
            # A cancelled or failed setup keeps its last message until the next attempt.
            if state.property("setup_message") is None:
                state.setText(tr("Runtime needs repair" if broken else "Ready · local inference" if ready else "Not installed"))

    def on_preparing(self, model_id, active):
        action, state, bar = self.model_controls[model_id]
        delete_btn = getattr(self, "model_delete_buttons", {}).get(model_id)
        action.setObjectName("" if active else "primary")
        action.style().unpolish(action)
        action.style().polish(action)
        if active:
            action.setText(tr("Cancel setup"))
            if delete_btn is not None:
                delete_btn.hide()
            state.setProperty("setup_message", None)
            set_progress(bar, None)
            bar.show()
            fade_in(bar)
            self.activity_jobs.add(model_id)
        else:
            bar.hide()
            self.activity_jobs.discard(model_id)
        self.update_activity()

    def on_engine_activity(self, engine_id, active):
        job = "engine:" + engine_id
        if active:
            self.activity_jobs.add(job)
        else:
            self.activity_jobs.discard(job)
        self.update_activity()

    def on_progress(self, model_id, update):
        _, state, bar = self.model_controls[model_id]
        if isinstance(update, Progress):
            parts = [f"{update.step}/{update.steps}" if update.steps else "", tr(update.phase),
                     tr(update.task), tr(update.detail)]
            state.setText("  ·  ".join(part for part in parts if part))
            state.setProperty("setup_message", None)
            set_progress(bar, update.fraction)
        else:
            # Plain messages are cancellations or errors; keep them visible after setup ends.
            state.setText(tr(str(update)))
            state.setProperty("setup_message", str(update))
