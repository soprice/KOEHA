"""Create, edit and select translation prompts separately from connection credentials."""
import uuid
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from koeha.core.i18n import tr
from koeha.ui.components import card, label
from koeha.ui.widgets import ElidedComboBox


class PromptProfiles(QWidget):
    profilesChanged = Signal()
    profileSelected = Signal(object)
    saveRequested = Signal()

    def __init__(self, settings):
        super().__init__()
        self.profiles = [replace(p) for p in settings.prompt_profiles]
        self.active_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        profile_card, profile_layout = card()
        layout.addWidget(profile_card, 1)
        self.saved = ElidedComboBox()
        self.saved.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.saved.setMinimumContentsLength(10)
        self.saved.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.saved.setAccessibleName(tr("Prompt profile"))
        row = QHBoxLayout()
        row.addWidget(self.saved, 1)
        self.new_button = QPushButton(tr("New profile"))
        self.new_button.clicked.connect(self.new_profile)
        row.addWidget(self.new_button)
        self.delete_button = QPushButton(tr("Delete profile"))
        self.delete_button.clicked.connect(self.delete_profile)
        row.addWidget(self.delete_button)
        self.name = QLineEdit()
        self.name.setAccessibleName(tr("Profile name"))
        form = QFormLayout()
        form.addRow(tr("Saved profiles"), row)
        form.addRow(tr("Profile name"), self.name)
        profile_layout.addLayout(form)
        self.prompt = self._text_field(
            profile_layout, "System prompt",
            "Set rules, e.g. “Keep subtitles short.” Use {target_language} and {source_language} for languages set in Live.",
            150, expandable=True)
        self.detail_fields = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.detail_fields.setSpacing(12)
        profile_layout.addLayout(self.detail_fields)
        context_layout = QVBoxLayout()
        context_layout.setSpacing(4)
        self.detail_fields.addLayout(context_layout, 1)
        self.context = self._text_field(
            context_layout, "Scene context",
            "Optional background, e.g. two siblings are arguing.",
            78)
        glossary_layout = QVBoxLayout()
        glossary_layout.setSpacing(4)
        self.detail_fields.addLayout(glossary_layout, 1)
        self.glossary = self._text_field(
            glossary_layout, "Glossary",
            "Preferred translations, one pair per line, e.g. 勇者 = Hero.",
            78)
        self.footer = QWidget()
        actions = QHBoxLayout(self.footer)
        actions.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        actions.addWidget(self.status, 1)
        self.save_button = QPushButton(tr("Save translation prompts"))
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.saveRequested.emit)
        actions.addWidget(self.save_button)
        self.saved.currentIndexChanged.connect(lambda: self.select_profile(self.saved.currentData()))
        self.name.textChanged.connect(self._edited)
        self.prompt.textChanged.connect(self._edited)
        self.context.textChanged.connect(self._mark_edited)
        self.glossary.textChanged.connect(self._mark_edited)
        self.select_profile(settings.prompt_profile)

    @staticmethod
    def _text_field(layout, title, help_text, height, *, expandable=False):
        heading = label(title, "panelTitle")
        edit = QPlainTextEdit()
        edit.setAccessibleName(tr(title))
        edit.setAccessibleDescription(tr(help_text))
        heading.setBuddy(edit)
        layout.addWidget(heading)
        layout.addWidget(label(help_text, "muted"))
        if expandable:
            edit.setMinimumHeight(height)
            layout.addWidget(edit, 1)
        else:
            edit.setFixedHeight(height)
            layout.addStretch()
            layout.addWidget(edit)
        return edit

    def resizeEvent(self, event):
        super().resizeEvent(event)
        direction = (QBoxLayout.Direction.LeftToRight if event.size().width() >= 600
                     else QBoxLayout.Direction.TopToBottom)
        self.detail_fields.setDirection(direction)

    def _stash(self):
        if self.active_id:
            profile = next(p for p in self.profiles if p.id == self.active_id)
            profile.name = self.name.text().strip()
            profile.system_prompt = self.prompt.toPlainText()
            profile.context = self.context.toPlainText()
            profile.glossary = self.glossary.toPlainText()

    def _refresh(self):
        with QSignalBlocker(self.saved):
            self.saved.clear()
            for profile in self.profiles:
                self.saved.addItem(profile.name or tr("Unnamed profile"), profile.id)
            self.saved.setCurrentIndex(self.saved.findData(self.active_id))
        self.saved.setToolTip(self.saved.currentText())
        self.delete_button.setEnabled(len(self.profiles) > 1)
        self.profilesChanged.emit()

    def select_profile(self, profile_id):
        if profile_id == self.active_id or not any(p.id == profile_id for p in self.profiles):
            return
        if self.active_id:
            self._mark_edited()
        self._stash()
        self.active_id = profile_id
        profile = next(p for p in self.profiles if p.id == profile_id)
        with (QSignalBlocker(self.name), QSignalBlocker(self.prompt),
              QSignalBlocker(self.context), QSignalBlocker(self.glossary)):
            self.name.setText(profile.name)
            self.prompt.setPlainText(profile.system_prompt)
            self.context.setPlainText(profile.context)
            self.glossary.setPlainText(profile.glossary)
        self._refresh()
        self.profileSelected.emit(profile)

    def _edited(self):
        self._stash()
        self._mark_edited()
        self._refresh()

    def _mark_edited(self):
        self.status.setText(tr("Unsaved prompt changes"))

    def new_profile(self):
        self._stash()
        selected = next(p for p in self.profiles if p.id == self.active_id)
        names = {p.name.casefold() for p in self.profiles}
        name, suffix = tr("New translation style"), 2
        while name.casefold() in names:
            name, suffix = f"{tr('New translation style')} {suffix}", suffix + 1
        profile = replace(selected, id=uuid.uuid4().hex, name=name)
        self.profiles.append(profile)
        self.select_profile(profile.id)
        self._mark_edited()
        self.name.setFocus()
        self.name.selectAll()

    def delete_profile(self):
        if len(self.profiles) <= 1:
            return
        self.profiles = [p for p in self.profiles if p.id != self.active_id]
        self.active_id = ""
        self.select_profile(self.profiles[0].id)
        self._mark_edited()

    def settings_patch(self):
        self._stash()
        return dict(prompt_profile=self.active_id, prompt_profiles=[replace(p) for p in self.profiles])

    def committed(self):
        self.status.setText(tr("Translation prompts saved"))
