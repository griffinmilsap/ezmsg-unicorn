import os
import sys
import asyncio
import time

import ezmsg.core as ez


try:
    # Python libraries that handle Bluetooth Classic RFCOMM connections
    # are in short supply these days (2024)..  
    # Bleak is great and modern but only handles bluetooth low-energy (BLE)
    # PyBluez is not maintained and wheels are not compiled for modern MacOS (ARM)
    # Python has native RFCOMM support, but the Python that ships with MacOS and Windows
    # is not currently compiled with bluetooth support...
    # I hate to depend on a library as large as Qt for one silly module to handle
    # RFCOMM bluetooth communication, but this is where we are with such a dated technology.
    # @gtec - If you're reading this, please consider a BLE firmware upgrade
    from PyQt5.QtWidgets import QApplication
    from PyQt5 import QtBluetooth
except ImportError:
    ez.logger.error(f'Install PyQt5 for Unicorn communications')
    raise

from .protocol import UnicornProtocol

from .connection import(
    UnicornConnection,
    UnicornConnectionSettings,
    UnicornConnectionState,
)


class QtUnicornConnectionState(UnicornConnectionState):
    loop: asyncio.AbstractEventLoop


class QtUnicornConnection(UnicornConnection):
    STATE: QtUnicornConnectionState

    async def initialize(self) -> None:
        self.STATE.loop = asyncio.get_running_loop()
        await super().initialize()

    @ez.main
    def main_thread(self) -> None:
        # QApplications need to be handled from the main thread.
        # and PyQt predates asyncio so .. this is where we ended up.

        # https://doc.qt.io/qtforpython-6.2/PySide6/QtBluetooth/index.html#macos-specific
        if sys.platform == 'darwin':
            os.environ['QT_EVENT_DISPATCHER_CORE_FOUNDATION'] = '1'

        app = QApplication([])

        read_length = UnicornProtocol.PAYLOAD_LENGTH * self.STATE.cur_settings.n_samp

        sock = QtBluetooth.QBluetoothSocket(
            QtBluetooth.QBluetoothServiceInfo.RfcommProtocol # type: ignore
        )

        def socket_error(_) -> None:
            ez.logger.warning(sock.errorString())

        def disconnected() -> None:
            ez.logger.info('timeout on unicorn connection. disconnected.')

        def connected() -> None:
            sock.write(UnicornProtocol.START_MSG)

        def received():
            while sock.bytesAvailable() >= read_length:
                block = sock.read(read_length)
                assert len(block) == read_length
                self.STATE.loop.call_soon_threadsafe(
                    self.STATE.incoming.put_nowait, block
                )

        sock.error.connect(socket_error)
        sock.connected.connect(connected)
        sock.readyRead.connect(received)
        sock.disconnected.connect(disconnected)

        sock.connectToService(
            QtBluetooth.QBluetoothAddress(
                self.STATE.cur_settings.address
            ), 
            UnicornProtocol.PORT
        )

        while True:
            app.processEvents()
            time.sleep(0.01)