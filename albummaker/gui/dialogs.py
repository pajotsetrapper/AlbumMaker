# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""The pop-ups that tell the user what a task is doing and when it is done.

PreparingDialog stays on screen, in front of the window, from the moment a server or a
site build starts until it is ready (or has failed): the user cannot miss that something
is being prepared, and is told to wait. ResultDialog is the big message that follows:
ready (with what to click next) or failed (with where to look)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .progress import RunProgress


def _label(text: str, object_name: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(wrap)
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    return label


def _bring_to_front(window: QWidget) -> None:
    """Make sure the user notices, also when AlbumMaker is behind other windows or minimized:
    raise it and make its taskbar / Dock icon flash."""
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
    QApplication.alert(window, 0)


def _fit_to_content(dialog: QDialog) -> None:
    """Resize a dialog so word-wrapped labels are never cut off.

    adjustSize() alone often misses the extra height that wrapped text needs, so the
    height is worked out from the layout for the dialog's actual width. The dialog can
    grow but never shrinks, so it does not jump around while the text changes."""
    layout = dialog.layout()
    if layout is None:
        return
    layout.activate()
    width = max(dialog.width(), dialog.minimumWidth(), layout.minimumSize().width())
    needed = max(layout.totalHeightForWidth(width), layout.totalMinimumSize().height(),
                 dialog.sizeHint().height())
    dialog.resize(width, max(dialog.height(), needed))


class PreparingDialog(QDialog):
    """'Please wait: your trips are being prepared', with a progress bar and a Stop button.
    It cannot be closed with the window's close button or Esc; Stop (or the task ending) is
    the way out."""

    def __init__(self, parent: QWidget, on_stop: Callable[[], None]):
        super().__init__(parent)
        self.setObjectName("statusDialog")
        self.setWindowTitle("AlbumMaker \u2013 please wait")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setMinimumWidth(500)
        self._seconds = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 24)
        layout.setSpacing(12)
        layout.addWidget(_label("\u23f3", "statusIconBusy", wrap=False), 0, Qt.AlignmentFlag.AlignHCenter)
        self.heading = _label("", "statusHeading")
        layout.addWidget(self.heading)
        self.notice = _label("Please wait \u2013 not ready yet", "statusNotice")
        layout.addWidget(self.notice)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        self.bar.setMinimumHeight(16)
        layout.addWidget(self.bar)
        self.detail = _label("", "statusDetail")
        layout.addWidget(self.detail)
        self.elapsed = _label("", "muted", wrap=False)
        layout.addWidget(self.elapsed)
        self.hint = _label("", "muted")
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        row.addStretch(1)
        self.stop_button = QPushButton("Cancel")
        self.stop_button.setToolTip("Stop preparing and cancel.")
        self.stop_button.clicked.connect(on_stop)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        layout.addLayout(row)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    # the close button is hidden, but Esc and the system menu must not dismiss it either
    def reject(self) -> None:
        pass

    def closeEvent(self, event) -> None:
        if event.spontaneous():
            event.ignore()
        else:
            super().closeEvent(event)

    def begin(self, progress: RunProgress) -> None:
        """Show it for a task that has just started."""
        if not self.isVisible():        # (a task that follows another keeps counting)
            self._seconds = 0
        self.stop_button.setEnabled(True)
        self.notice.setText("Please wait \u2013 not ready yet")
        self.hint.setText(progress.hint)
        self.update_progress(progress)
        self._tick()
        self._clock.start()
        self.show()
        self.adjustSize()
        _fit_to_content(self)
        _bring_to_front(self)

    def update_progress(self, progress: RunProgress) -> None:
        self.heading.setText(progress.heading)
        self.detail.setText(progress.detail)
        if progress.fraction is None:
            self.bar.setRange(0, 0)                      # busy: it cannot tell how far along it is
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, progress.fraction)) * 1000))
        if self.isVisible():
            _fit_to_content(self)       # the text may now need more lines

    def stopping(self) -> None:
        self.heading.setText("Stopping\u2026")
        self.notice.setText("Please wait a moment")
        self.stop_button.setEnabled(False)

    def end(self) -> None:
        self._clock.stop()
        self.hide()

    def _tick(self) -> None:
        minutes, seconds = divmod(self._seconds, 60)
        self.elapsed.setText(f"Time so far: {minutes}:{seconds:02d}")
        self._seconds += 1


class ResultDialog(QDialog):
    """The big 'your trips are ready' (or 'something went wrong') message.

    buttons: (text, callback, object name, closes the dialog). The first one is the default."""

    def __init__(self, parent: QWidget, ok: bool, title: str, message: str, buttons: list,
                 address: Optional[str] = None, addresses: Optional[list] = None):
        super().__init__(parent)
        self.setObjectName("statusDialog")
        self.setWindowTitle("AlbumMaker \u2013 " + ("ready" if ok else "problem"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 24)
        layout.setSpacing(12)
        layout.addWidget(_label("\u2714" if ok else "!", "statusIconOk" if ok else "statusIconError", wrap=False),
                         0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(_label(title, "statusHeadingOk" if ok else "statusHeadingError"))
        layout.addWidget(_label(message, "statusDetail"))
        # addresses: [(what it is, the address), ...]; address: one bare address (a folder, say)
        for caption, text in (addresses or []):
            layout.addWidget(_label(caption, "statusDetail"))
            box = _label(text, "addressBox")
            box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(box)
        if address:
            box = _label(address, "addressBox")
            box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(box)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        for index, (text, callback, object_name, closes) in enumerate(buttons):
            button = QPushButton(text)
            if object_name:
                button.setObjectName(object_name)
            if callback is not None:
                button.clicked.connect(callback)
            if closes:
                button.clicked.connect(self.accept)
            if index == 0:
                button.setDefault(True)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

    def present(self) -> None:
        self.show()
        self.adjustSize()
        _fit_to_content(self)
        _bring_to_front(self)
