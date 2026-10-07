"""プルダウン（QComboBox）を押しても開かないのを防ぎ、そのときの状態を記録する。

macOS で実際のマウスで選ぶと、閉じたはずのプルダウンが Qt の中では「開いている」まま残り、次の押下がそのプルダウンに
吸われて閉じるだけで終わることがある（2026-10-04 の記録では毎回。合成のクリックでは起きない）。
そこで、プルダウンを押したときにほかのプルダウンが残っていれば、すぐにそれを閉じて、押したプルダウンを開く。
それでも押して少し待って開いていなければ（アプリが「裏」扱いになったなど）、窓を前に出してから開く。
どちらも user_data_dir()/popup_log.txt にそのときの状態を書く（原因を突き止めるため）。
"""
import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QComboBox

from tor_app.paths import user_data_dir

CHECK_MS = 250             # 押してから、開いたかを確かめるまでの時間
LOG_MAX_BYTES = 200_000    # 記録がこれを超えたら古い分を捨てる


def _name(obj) -> str:
    if obj is None:
        return "なし"
    title = obj.windowTitle() if hasattr(obj, "windowTitle") else ""
    return f"{type(obj).__name__}" + (f"「{title}」" if title else "")


class ComboPopupGuard(QObject):
    def __init__(self, app: QApplication):
        super().__init__(app)
        # 確かめる予定のプルダウン → 押したあとに一度でも開いたか（同じ押下が親の部品にも届くので 1 回にする。
        # 素早く選んで閉じた場合は、確かめるときには閉じているので、開いたことを覚えておく）
        self._pending: dict[int, bool] = {}
        app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and self._pending and isinstance(obj.parent(), QComboBox):
            if id(obj.parent()) in self._pending:   # プルダウンの一覧の窓（親がプルダウン）が出た
                self._pending[id(obj.parent())] = True
        elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            # 見えないプルダウンが開いたままだと、押下は押した部品ではなくそのプルダウンに届くので、押した所の部品を見る
            target = QApplication.widgetAt(event.globalPosition().toPoint())
            combo = target if isinstance(target, QComboBox) else None
            if (combo is not None and id(combo) not in self._pending and combo.isEnabled()
                    and not combo.isEditable() and not combo.view().isVisible()):
                stale = QApplication.activePopupWidget()
                if stale is not None:
                    # 残ったプルダウンが押下を吸うので、閉じてから押したプルダウンを開く（押下はここで止める）
                    info = self._popup_info(stale, combo)
                    self._close_popup(stale)
                    self._log(combo, f"{self._state(obj)}\n  残っていたプルダウン: {info}", "残っていたプルダウンを閉じて開いた")
                    QTimer.singleShot(0, combo.showPopup)
                    return True
                self._pending[id(combo)] = False
                before = self._state(obj)
                QTimer.singleShot(CHECK_MS, lambda c=combo, b=before: self._check(c, b))
        return False

    def _check(self, combo: QComboBox, before: str) -> None:
        opened = self._pending.pop(id(combo), False)
        if opened or sip.isdeleted(combo) or not combo.isVisible() or combo.view().isVisible():
            return
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            return   # まだ押したまま（押して離すと開く操作の途中）
        self._log(combo, before)
        window = combo.window()
        window.raise_()
        window.activateWindow()
        combo.showPopup()

    @staticmethod
    def _close_popup(popup) -> None:
        popup.hide()
        if QApplication.activePopupWidget() is popup:
            # 隠れているのに開いている扱いのままなら、いったん出してから隠す（隠すときに「開いている」から外れる）
            popup.show()
            popup.hide()

    @staticmethod
    def _popup_info(popup, combo: QComboBox) -> str:
        owner = popup.parent()
        label = (owner.toolTip().split("\n", 1)[0] or owner.currentText()) if isinstance(owner, QComboBox) else _name(owner)
        handle = popup.windowHandle()
        return (f"{_name(popup)} 持ち主={label}{'（押したもの）' if owner is combo else ''} 見えている={popup.isVisible()} "
                f"窓が見えている={handle.isVisible() if handle else None} "
                f"隠れた印={popup.testAttribute(Qt.WidgetAttribute.WA_WState_Hidden)} 位置={popup.geometry().getRect()}")

    @staticmethod
    def _state(receiver) -> str:
        app = QApplication.instance()
        return (f"アプリの状態={QGuiApplication.applicationState().name} 受け取った部品={_name(receiver)} "
                f"前面の窓={_name(app.activeWindow())} 開いているプルダウン={_name(app.activePopupWidget())} "
                f"モーダル={_name(app.activeModalWidget())} フォーカス={_name(app.focusWidget())} "
                f"フォーカスの窓={_name(QGuiApplication.focusWindow())}")

    def _log(self, combo: QComboBox, before: str, what: str = "開かなかったので開き直した") -> None:
        try:
            path = user_data_dir() / "popup_log.txt"
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                path.write_text(path.read_text(encoding="utf-8")[-LOG_MAX_BYTES // 2:], encoding="utf-8")
            label = combo.toolTip().split("\n", 1)[0] or combo.currentText()
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {what}: {label}\n"
                        f"  押したとき: {before}\n  確かめたとき: {self._state(None)}\n")
        except OSError:
            pass
