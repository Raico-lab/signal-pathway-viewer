"""PyQt6.sip の互換（ブラウザ版）: 消した部品かどうかだけ。"""


def isdeleted(obj) -> bool:
    return bool(getattr(obj, "_deleted", False))


def delete(obj) -> None:
    destroy = getattr(obj, "_destroy", None)
    if destroy is not None:
        destroy()
