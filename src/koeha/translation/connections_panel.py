"""Translation profile editor and asynchronous model discovery."""
import threading
import uuid
from dataclasses import replace

import httpx
from PySide6.QtCore import QObject, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from koeha.core.i18n import tr
from koeha.translation.connection_profiles import (
    HTTP_WARNING,
    PROVIDERS,
    ConnectionProfile,
    base_url,
    uses_remote_http,
)
from koeha.translation.translator import get_key, list_models, request_error_message, save_key
from koeha.ui.widgets import ElidedComboBox


class ModelProbe(QObject):
    ready = Signal(int, object, str)

    def fetch(self, request_id, url, key):
        def work():
            try:
                models, error = list_models(url, key), ""
            except httpx.HTTPStatusError as exc:
                models = []
                error = tr("Authentication failed. Enter the server API key.") if (
                    exc.response.status_code in {401, 403}) else request_error_message(exc, key)
            except Exception as exc:
                models, error = [], tr(request_error_message(exc, key))
            try:
                self.ready.emit(request_id, models, error)
            except RuntimeError:
                pass  # the editor was destroyed while the request was finishing
        threading.Thread(target=work, name="koeha-model-list", daemon=True).start()


class TranslationConnections(QWidget):
    profilesChanged = Signal()
    saveRequested = Signal()
    modelDiscoveryChanged = Signal(str, bool)

    def __init__(self, settings):
        super().__init__()
        self.profiles = [replace(p) for p in settings.translation_profiles] or [ConnectionProfile(
            "default", "Default", url=settings.translation_url, model=settings.translation_model)]
        self.active_id = ""
        self._keys = {}
        self._edited_keys = set()
        self._key_errors = {}
        self._models_by_profile = {}
        self._deleted = set()
        self._legacy_url = settings.translation_url if not settings.translation_profiles else ""
        self._request_id = 0
        self._probe = ModelProbe(self)
        self._probe.ready.connect(self._models_ready)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QLabel(tr("API connection profiles"))
        heading.setObjectName("section")
        layout.addWidget(heading)
        form = QFormLayout()
        self.saved = ElidedComboBox()
        self.saved.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.saved.setMinimumContentsLength(10)
        self.saved.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout()
        row.addWidget(self.saved, 1)
        self.new_button = QPushButton(tr("New profile"))
        self.new_button.clicked.connect(self.new_profile)
        row.addWidget(self.new_button)
        self.delete_button = QPushButton(tr("Delete profile"))
        self.delete_button.clicked.connect(self.delete_profile)
        row.addWidget(self.delete_button)
        form.addRow(tr("Saved profiles"), row)
        self.name = QLineEdit()
        self.provider = ElidedComboBox()
        for value, (title, _) in PROVIDERS.items():
            self.provider.addItem(tr(title), value)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("http://127.0.0.1:1234/v1")
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(12)
        self.model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model.lineEdit().setPlaceholderText(tr("Load models or enter a model ID"))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(tr("Optional for local servers"))
        for title, widget in [("Profile name", self.name), ("Provider", self.provider),
                              ("Translation API base URL", self.endpoint), ("API key", self.api_key)]:
            widget.setAccessibleName(tr(title))
            form.addRow(tr(title), widget)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        self.load_button = QPushButton(tr("Load models"))
        self.load_button.clicked.connect(self.load_models)
        model_row.addWidget(self.load_button)
        self.model.setAccessibleName(tr("Translation model"))
        form.addRow(tr("Translation model"), model_row)
        layout.addLayout(form)
        self.http_warning = QLabel(tr(HTTP_WARNING))
        self.http_warning.setWordWrap(True)
        self.http_warning.setObjectName("warning")
        layout.addWidget(self.http_warning)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("muted")
        layout.addWidget(self.hint)
        actions = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        actions.addWidget(self.status, 1)
        self.save_button = QPushButton(tr("Save profile"))
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.saveRequested.emit)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.saved.currentIndexChanged.connect(lambda: self.select_profile(self.saved.currentData()))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        for widget in (self.name, self.endpoint, self.api_key):
            widget.textChanged.connect(self._edited)
        self.model.currentTextChanged.connect(self._edited)
        self.select_profile(settings.translation_profile or self.profiles[0].id)

    def _stash(self):
        if not self.active_id:
            return
        profile = next(p for p in self.profiles if p.id == self.active_id)
        profile.name = self.name.text().strip()
        profile.provider = self.provider.currentData()
        profile.url = self.endpoint.text().strip().rstrip("/")
        profile.model = self.model.currentText().strip()

    def _refresh(self):
        with QSignalBlocker(self.saved):
            self.saved.clear()
            for profile in self.profiles:
                self.saved.addItem(profile.name or tr("Unnamed profile"), profile.id)
            self.saved.setCurrentIndex(self.saved.findData(self.active_id))
        self.delete_button.setEnabled(len(self.profiles) > 1)
        self.saved.setToolTip(self.saved.currentText())
        self.profilesChanged.emit()

    def select_profile(self, profile_id):
        if profile_id == self.active_id or not any(p.id == profile_id for p in self.profiles):
            return
        self._stash()
        self.active_id = profile_id
        profile = next(p for p in self.profiles if p.id == profile_id)
        if profile_id not in self._keys or profile_id in self._key_errors:
            try:
                credential = profile.credential_id
                if self._legacy_url and profile_id == "default":
                    credential = self._legacy_url
                self._keys[profile_id] = get_key(credential)
                self._key_errors.pop(profile_id, None)
            except Exception as error:
                self._keys[profile_id] = ""
                self._key_errors[profile_id] = str(error)
        widgets = [self.name, self.provider, self.endpoint, self.model, self.api_key]
        blockers = [QSignalBlocker(widget) for widget in widgets]
        self.name.setText(profile.name)
        self.provider.setCurrentIndex(self.provider.findData(profile.provider))
        self.endpoint.setText(profile.url)
        self.model.clear()
        self.model.addItems(self.model_ids)
        self.model.setEditText(profile.model)
        self.api_key.setText(self._keys[profile_id])
        del blockers
        self._request_id += 1
        self._show_discovery()
        self.status.setText(tr("Unsaved profile changes") if profile_id in self._edited_keys
                            else self._key_errors.get(profile_id, ""))
        self._update_hint()
        self.http_warning.setVisible(uses_remote_http(profile.url))
        self._refresh()

    def _update_hint(self):
        hints = {
            "custom": "Use an OpenAI-compatible server. API keys are stored in Windows Credential Manager.",
            "lmstudio": "Start the LM Studio server, then load models. Enter an API key if authentication is enabled.",
            "ollama": "Start Ollama and pull a chat model, then load the model list. API key is optional.",
        }
        self.hint.setText(tr(hints[self.provider.currentData()]))

    def _edited(self):
        if self.sender() is self.api_key:
            self._key_errors.pop(self.active_id, None)
            self._keys[self.active_id] = self.api_key.text()
            self._edited_keys.add(self.active_id)
        if self.sender() in (self.endpoint, self.api_key):
            self._invalidate_model_list()
        self._stash()
        self.http_warning.setVisible(uses_remote_http(self.endpoint.text()))
        self.status.setText(tr("Unsaved profile changes"))
        self._refresh()

    @property
    def model_ids(self):
        return self._models_by_profile.get(self.active_id, ())

    def _invalidate_model_list(self):
        self._request_id += 1
        self._models_by_profile.pop(self.active_id, None)
        selected = self.model.currentText()
        with QSignalBlocker(self.model):
            self.model.clear()
            self.model.setEditText(selected)
        self._show_discovery()

    def _provider_changed(self):
        provider = self.provider.currentData()
        title, url = PROVIDERS[provider]
        self.endpoint.setText(url)
        self.model.clear()
        self.api_key.clear()
        self._invalidate_model_list()
        if self.name.text().startswith(tr("New connection")):
            names = {p.name for p in self.profiles if p.id != self.active_id}
            name, suffix = title, 2
            while name in names:
                name, suffix = f"{title} {suffix}", suffix + 1
            self.name.setText(name)
        self._update_hint()
        self._edited()

    def new_profile(self):
        self._stash()
        names = {p.name for p in self.profiles}
        name, suffix = tr("New connection"), 2
        while name in names:
            name, suffix = f"{tr('New connection')} {suffix}", suffix + 1
        profile = ConnectionProfile(uuid.uuid4().hex, name)
        self.profiles.append(profile)
        self._keys[profile.id] = ""
        self.select_profile(profile.id)
        self.status.setText(tr("Unsaved profile changes"))
        self.name.setFocus()
        self.name.selectAll()

    def delete_profile(self):
        if len(self.profiles) <= 1:
            return
        removed = self.active_id
        self.active_id = ""
        self.profiles = [p for p in self.profiles if p.id != removed]
        self._deleted.add(removed)
        self._keys.pop(removed, None)
        self._edited_keys.discard(removed)
        self._key_errors.pop(removed, None)
        self._models_by_profile.pop(removed, None)
        self.select_profile(self.profiles[0].id)
        self.status.setText(tr("Unsaved profile changes"))

    def settings_patch(self):
        self._stash()
        profiles = [replace(p, url=base_url(p.url, p.provider)) for p in self.profiles]
        active = next(p for p in profiles if p.id == self.active_id)
        return dict(translation_profiles=profiles, translation_profile=active.id,
                    translation_url=active.url, translation_model=active.model)

    def save_credentials(self):
        if self._key_errors:
            raise ValueError("Could not read stored credentials. Re-enter the API key before saving.")
        for profile in self.profiles:
            migrate_legacy = self._legacy_url and profile.id == "default" and self._keys.get(profile.id)
            if profile.id in self._edited_keys or migrate_legacy:
                save_key(profile.credential_id, self._keys[profile.id])

    def committed(self):
        for profile_id in list(self._deleted):
            save_key("profile:" + profile_id, "")
            self._deleted.remove(profile_id)
        self._legacy_url = ""
        self._edited_keys.clear()
        self.status.setText(tr("Profiles saved"))

    def load_models(self):
        if not self.load_button.isEnabled():
            return
        try:
            url = base_url(self.endpoint.text(), self.provider.currentData())
        except ValueError as error:
            self._show_discovery(str(error))
            return
        self._request_id += 1
        self._show_discovery(tr("Connecting…"), loading=True)
        self._probe.fetch(self._request_id, url, self.api_key.text())

    def _show_discovery(self, message="", *, loading=False):
        self.load_button.setEnabled(not loading)
        if message:
            self.status.setText(message)
        self.modelDiscoveryChanged.emit(message, loading)

    def _models_ready(self, request_id, models, error):
        if request_id != self._request_id:
            return
        if error:
            self._show_discovery(tr("Connection failed") + ": " + error)
            return
        self._models_by_profile[self.active_id] = tuple(models)
        selected = self.model.currentText()
        with QSignalBlocker(self.model):
            self.model.clear()
            self.model.addItems(models)
            self.model.setEditText(selected or (models[0] if models else ""))
        self._edited()
        self._show_discovery(tr("{count} models").format(count=len(models)) if models else tr(
            "No models found. Load or download a chat model on the server."))
