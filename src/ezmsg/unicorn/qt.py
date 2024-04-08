import os
import sys
import asyncio
import time

import ezmsg.core as ez

from PyQt5.QtWidgets import QApplication
from PyQt5 import QtBluetooth

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
    def handle_device(self) -> None:
        # QApplications need to be handled from the main thread.
        # and PyQt predates asyncio so .. this is where we ended up.

        # https://doc.qt.io/qtforpython-6.2/PySide6/QtBluetooth/index.html#macos-specific
        if sys.platform == 'darwin':
            os.environ['QT_EVENT_DISPATCHER_CORE_FOUNDATION'] = '1'

        app = QApplication([])

        # NOTE: There appear to be some race conditions in this code
        # that affect reconnecting and disconnecting.  I don't like it either
        # but I'm out of time to debug this functionality. -Griff

        while True: # Application Loop

            while True: # pain
                app.processEvents()
                time.sleep(0.01)

                # Maybe needs to be threadsafe
                if self.STATE.reconnect_event.is_set():
                    break

            self.STATE.loop.call_soon_threadsafe(
                self.STATE.reconnect_event.clear
            )

            if self.STATE.cur_settings.address in (None, '', 'simulator'):
                continue

            ez.logger.info( 'Connecting to device!' )

            while True: # Reconnection Loop; keep trying to connect if device disconnects

                sock = QtBluetooth.QBluetoothSocket(
                    QtBluetooth.QBluetoothServiceInfo.RfcommProtocol # type: ignore
                )

                try:

                    read_length = UnicornProtocol.PAYLOAD_LENGTH * self.STATE.cur_settings.n_samp

                    def socket_error(_) -> None:
                        ez.logger.warning(sock.errorString())

                    def disconnected() -> None:
                        # FIXME: Gracefully disconnect
                        ez.logger.info('timeout on unicorn connection. disconnected.')

                    def connected() -> None:
                        ez.logger.debug(f"starting stream")
                        sock.write(UnicornProtocol.START_MSG)
                        reply = sock.read(len(UnicornProtocol.START_MSG)) # 0x00, 0x00, 0x00

                    def received():
                        while sock.isReadable() and sock.bytesAvailable() >= read_length:
                            block = sock.read(read_length)
                            if block:
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
                        if not sock.state() or self.STATE.reconnect_event.is_set():
                            break

                finally:
                    # As written, this tends to try to write 
                    # after the socket is closed... which causes Qt to crash
                    # Writing occurs once processEvents is called, but I think
                    # sock.close occurs immediately.
                    # Fortunately, the Unicorn seems to gracefully revert to
                    # idle state once the socket closes without needing to 
                    # actually send the stop message
                    # if sock.isWritable():
                    #     ez.logger.info(f"stopping stream")
                    #     sock.write(UnicornProtocol.STOP_MSG)
                    sock.close()

                if self.STATE.reconnect_event.is_set():
                    break # Out of reconnection loop
