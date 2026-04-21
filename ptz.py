#!/usr/bin/env python3
"""
PTZ Controller – ONVIF / PyQt6
pip install onvif-zeep PyQt6
"""
import atexit
import json
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from json import JSONDecodeError

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QIcon, QKeyEvent, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from onvif import ONVIFCamera

# ── Config ────────────────────────────────────────────────────────────────────
try:
    CAM_CONF = json.loads(open('.secret/ptz.json', 'r', encoding='utf-8').read())
    CAM_HOST = CAM_CONF['CAM_HOST']
    CAM_PORT = CAM_CONF['CAM_PORT']
    CAM_USER = CAM_CONF['CAM_USER']
    CAM_PASS = CAM_CONF['CAM_PASS']
    PAN_SPEED = CAM_CONF.get('PAN_SPEED', 0.25)
    TILT_SPEED = CAM_CONF.get('TILT_SPEED', 0.25)
    ZOOM_SPEED = CAM_CONF.get('ZOOM_SPEED', 0.25)
except FileNotFoundError:
    print('Create ptz.json in folder .secret')
    exit(1)
except JSONDecodeError:
    print('Make sure ptz.json is correctly formatted.')
    exit(2)
except KeyError:
    print('Make sure ptz.json has all needed items: CAM_HOST, CAM_PORT, CAM_USER, CAM_PASS')
    exit(2)
except Exception as e:
    print(f"{e.__class__}\n{str(e)}")
    exit(3)


# ── ONVIF ─────────────────────────────────────────────────────────────────────
class ONVIFController:
    def __init__(self):
        self.ptz   = None
        self.token = None
        self._req  = None
        # max_workers=1 : move() et stop() sont toujours séquentiels,
        # jamais concurrents — stop arrive toujours après move.
        self._exec = ThreadPoolExecutor(max_workers=1)

    def connect(self):
        cam        = ONVIFCamera(CAM_HOST, CAM_PORT, CAM_USER, CAM_PASS)
        self.ptz   = cam.create_ptz_service()
        media      = cam.create_media_service()
        self.token = media.GetProfiles()[0].token
        self._req  = self.ptz.create_type("ContinuousMove")
        self._req.ProfileToken = self.token
        self._req.Velocity = {
            "PanTilt": {"x": 0.0, "y": 0.0},
            "Zoom":    {"x": 0.0},
        }

    def move(self, pan=0.0, tilt=0.0, zoom=0.0):
        if not self.ptz:
            return
        self._req.Velocity["PanTilt"]["x"] = pan
        self._req.Velocity["PanTilt"]["y"] = tilt
        self._req.Velocity["Zoom"]["x"]    = zoom
        try:
            self.ptz.ContinuousMove(self._req)
        except Exception:
            pass

    def stop(self):
        if not self.ptz:
            return
        req = self.ptz.create_type("Stop")
        req.ProfileToken = self.token
        req.PanTilt = req.Zoom = True
        try:
            self.ptz.Stop(req)
        except Exception:
            pass

    def stop_sync(self):
        """Arrêt bloquant — pour atexit / fermeture."""
        if not self.ptz:
            return
        req = self.ptz.create_type("Stop")
        req.ProfileToken = self.token
        req.PanTilt = req.Zoom = True
        self.ptz.Stop(req)

    def submit_move(self, pan, tilt, zoom):
        self._exec.submit(self.move, pan, tilt, zoom)

    def submit_stop(self):
        self._exec.submit(self.stop)


# ── Fenêtre principale ────────────────────────────────────────────────────────
class PTZWindow(QMainWindow):

    # Signal pour mettre à jour le statut depuis un thread ONVIF
    _sig_status = pyqtSignal(str, str)  # (texte, couleur CSS)

    # ── Mapping clavier ───────────────────────────────────────────────────────
    # Pavé numérique 7/8/9/4/6/1/2/3 + flèches + +/-
    _KEY_MAP = {
        Qt.Key.Key_7:     (-PAN_SPEED,  TILT_SPEED,  0),
        Qt.Key.Key_8:     ( 0,           TILT_SPEED,  0),
        Qt.Key.Key_9:     ( PAN_SPEED,   TILT_SPEED,  0),
        Qt.Key.Key_4:     (-PAN_SPEED,   0,           0),
        Qt.Key.Key_6:     ( PAN_SPEED,   0,           0),
        Qt.Key.Key_1:     (-PAN_SPEED,  -TILT_SPEED,  0),
        Qt.Key.Key_2:     ( 0,          -TILT_SPEED,  0),
        Qt.Key.Key_3:     ( PAN_SPEED,  -TILT_SPEED,  0),
        Qt.Key.Key_Up:    ( 0,           TILT_SPEED,  0),
        Qt.Key.Key_Down:  ( 0,          -TILT_SPEED,  0),
        Qt.Key.Key_Left:  (-PAN_SPEED,   0,           0),
        Qt.Key.Key_Right: ( PAN_SPEED,   0,           0),
        Qt.Key.Key_Plus:  ( 0,  0,  ZOOM_SPEED),
        Qt.Key.Key_Minus: ( 0,  0, -ZOOM_SPEED),
    }
    _STOP_KEYS = {Qt.Key.Key_5, Qt.Key.Key_Clear}  # 5 numpad (NumLock on/off)

    def __init__(self, ctrl: ONVIFController):
        super().__init__()
        self.ctrl        = ctrl
        self._moving     = False
        self._active_key = None          # touche clavier actuellement tenue

        self._sig_status.connect(self._update_status)
        self.setWindowTitle("PTZ")
        self._build_ui()
        self._build_tray()
        QTimer.singleShot(400, self._connect)

    # ── Construction UI ───────────────────────────────────────────────────────

    def _build_ui(self):
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root = QVBoxLayout(root_widget)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        # Ligne statut
        srow = QHBoxLayout()
        srow.addWidget(QLabel("<b>PTZ</b>"))
        srow.addSpacing(6)
        self._status_lbl = QLabel("Connexion...")
        self._status_lbl.setStyleSheet("color: gray")
        srow.addWidget(self._status_lbl)
        srow.addStretch()
        root.addLayout(srow)

        # Grille directionnelle
        grid = QGridLayout()
        grid.setSpacing(3)

        DIRS = [
            ("NW", 0, 0, -PAN_SPEED,  TILT_SPEED,  0),
            ("N",  0, 1,  0,           TILT_SPEED,  0),
            ("NE", 0, 2,  PAN_SPEED,   TILT_SPEED,  0),
            ("W",  1, 0, -PAN_SPEED,   0,           0),
            ("E",  1, 2,  PAN_SPEED,   0,           0),
            ("SW", 2, 0, -PAN_SPEED,  -TILT_SPEED,  0),
            ("S",  2, 1,  0,          -TILT_SPEED,  0),
            ("SE", 2, 2,  PAN_SPEED,  -TILT_SPEED,  0),
        ]
        for label, r, c, pan, tilt, zoom in DIRS:
            b = QPushButton(label)
            b.setFixedSize(54, 40)
            b.pressed.connect(lambda p=pan, t=tilt, z=zoom: self._on_move(p, t, z))
            b.released.connect(self._on_stop)
            grid.addWidget(b, r, c)

        stop_b = QPushButton("STOP")
        stop_b.setFixedSize(54, 40)
        stop_b.clicked.connect(self._on_stop)
        grid.addWidget(stop_b, 1, 1)

        root.addLayout(grid)

        # Zoom
        zrow = QHBoxLayout()
        zrow.addWidget(QLabel("Zoom"))
        zrow.addSpacing(4)
        for label, z in (("+", ZOOM_SPEED), ("−", -ZOOM_SPEED)):
            b = QPushButton(label)
            b.setFixedSize(60, 26)
            b.pressed.connect(lambda zoom=z: self._on_move(0, 0, zoom))
            b.released.connect(self._on_stop)
            zrow.addWidget(b)
        zrow.addStretch()
        root.addLayout(zrow)

        # Options
        orow = QHBoxLayout()
        self._topmost_cb = QCheckBox("Topmost")
        self._topmost_cb.toggled.connect(self._set_topmost)
        orow.addWidget(self._topmost_cb)
        hide_b = QPushButton("Masquer")
        hide_b.setFixedHeight(24)
        hide_b.clicked.connect(self.hide)
        orow.addWidget(hide_b)
        root.addLayout(orow)

        rec_b = QPushButton("Reconnecter")
        rec_b.setFixedHeight(24)
        rec_b.clicked.connect(self._reconnect)
        root.addWidget(rec_b)

        # Taille fixée d'après le layout naturel
        self.adjustSize()
        self.setFixedSize(self.size())

    def _build_tray(self):
        # Icône dessinée en code
        pm = QPixmap(32, 32)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#00b4d8")))
        p.drawRoundedRect(2, 8, 28, 16, 4, 4)
        p.drawRoundedRect(10, 3, 12, 8, 3, 3)
        p.setBrush(QBrush(QColor("#111")))
        p.drawEllipse(8, 10, 16, 12)
        p.setBrush(QBrush(QColor("#00b4d8")))
        p.drawEllipse(12, 14, 8, 5)
        p.end()

        self._tray = QSystemTrayIcon(QIcon(pm), self)
        m = QMenu()
        m.addAction("Afficher", self._show_window)
        m.addSeparator()
        m.addAction("Quitter", self._quit)
        self._tray.setContextMenu(m)
        self._tray.activated.connect(
            lambda r: self._show_window()
            if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self._tray.show()

    # ── Mouvement ─────────────────────────────────────────────────────────────

    def _on_move(self, pan, tilt, zoom):
        self._moving = True
        self.ctrl.submit_move(pan, tilt, zoom)

    def _on_stop(self):
        if not self._moving:
            return
        self._moving = False
        self.ctrl.submit_stop()

    # ── Connexion ─────────────────────────────────────────────────────────────

    def _connect(self):
        def _do():
            try:
                self.ctrl.connect()
                self._sig_status.emit("Connecté", "green")
            except Exception:
                self._sig_status.emit("Erreur", "red")
        threading.Thread(target=_do, daemon=True).start()

    def _reconnect(self):
        self._update_status("Connexion...", "gray")
        self._connect()

    def _update_status(self, text: str, color: str):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}")

    # ── Options ───────────────────────────────────────────────────────────────

    def _set_topmost(self, on: bool):
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _show_window(self):
        self.show()
        self.activateWindow()
        self.raise_()

    # ── Clavier ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        key = event.key()
        if key in self._STOP_KEYS:
            self._active_key = None
            self._on_stop()
        elif key in self._KEY_MAP:
            self._active_key = key
            self._on_move(*self._KEY_MAP[key])

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        # Libère uniquement si c'est la touche active (ignore les autres)
        if event.key() == self._active_key:
            self._active_key = None
            self._on_stop()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._quit()
        event.accept()

    def _quit(self):
        try:
            self.ctrl.stop_sync()
        except Exception:
            pass
        QApplication.instance().quit()


# ── Entrée ────────────────────────────────────────────────────────────────────
def main():
    ctrl = ONVIFController()
    atexit.register(lambda: ctrl.stop_sync() if ctrl.ptz else None)
    signal.signal(
        signal.SIGINT,
        lambda s, f: (ctrl.stop_sync() if ctrl.ptz else None, sys.exit(0)),
    )

    app = QApplication(sys.argv)
    # Ne pas quitter quand la fenêtre est masquée (tray actif)
    app.setQuitOnLastWindowClosed(False)

    win = PTZWindow(ctrl)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
