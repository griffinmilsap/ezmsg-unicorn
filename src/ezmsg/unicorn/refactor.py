import os
import sys
import asyncio
import typing

import ezmsg.core as ez
import numpy as np

from ezmsg.util.messages.axisarray import AxisArray

_UNICORN_PORT = 1
_UNICORN_EEG_CHANNELS_COUNT = 8
_UNICORN_HEADER_LENGTH = 2
_UNICORN_BYTES_PER_BATTERY_LEVEL_CHANNEL = 1
_UNICORN_BATTERY_LEVEL_LENGTH = _UNICORN_BYTES_PER_BATTERY_LEVEL_CHANNEL
_UNICORN_BATTERY_LEVEL_OFFSET = _UNICORN_HEADER_LENGTH
_UNICORN_BYTES_PER_EEG_CHANNEL = 3
_UNICORN_PAYLOAD_LENGTH = 45
_UNICORN_ACC_CHANNELS_COUNT = 3
_UNICORN_GYROSCOPE_CHANNELS_COUNT = 3
_UNICORN_BYTES_PER_ACC_CHANNEL = 2
_UNICORN_FS = 250.0

_UNICORN_EEG_OFFSET = _UNICORN_BATTERY_LEVEL_OFFSET + _UNICORN_BATTERY_LEVEL_LENGTH
_UNICORN_ACC_LENGTH = _UNICORN_ACC_CHANNELS_COUNT * _UNICORN_BYTES_PER_ACC_CHANNEL
_UNICORN_ACC_OFFSET = _UNICORN_EEG_OFFSET + (_UNICORN_EEG_CHANNELS_COUNT * _UNICORN_BYTES_PER_EEG_CHANNEL)
_UNICORN_BYTES_PER_GYR_CHANNEL = 2
_UNICORN_GYR_LENGTH = _UNICORN_GYROSCOPE_CHANNELS_COUNT * _UNICORN_BYTES_PER_GYR_CHANNEL
_UNICORN_GYR_OFFSET = _UNICORN_ACC_OFFSET + _UNICORN_ACC_LENGTH

_UNICORN_START = b'\x61\x7C\x87'
_UNICORN_STOP = b"\x63\x5C\xC5"

_CALIBRATE_EEG = lambda x: x * (4500000.0 / 50331642.0)
_CALIBRATE_ACC = lambda x: x * (1.0 / 4096.0)
_CALIBRATE_GYR = lambda x: x * (1.0 / 32.8)

class UnicornConnectionSettings(ez.Settings):
    # if addr == None; don't connect to any device.
    address: typing.Optional[str] = None # "XX:XX:XX:XX:XX:XX"
    n_samp: int = 50

class UnicornConnectionState(ez.State):
    cur_settings: UnicornConnectionSettings
    incoming: asyncio.Queue[bytes]

class UnicornConnection(ez.Unit):
    SETTINGS: UnicornConnectionSettings
    STATE: UnicornConnectionState

    INPUT_SETTINGS = ez.InputStream(UnicornConnectionSettings)
    
    OUTPUT_GYROSCOPE = ez.OutputStream(AxisArray)
    OUTPUT_SIGNAL = ez.OutputStream(AxisArray)
    OUTPUT_BATTERY = ez.OutputStream(float)
    OUTPUT_ACCELEROMETER = ez.OutputStream(AxisArray)

    @ez.subscriber(INPUT_SETTINGS)
    async def on_settings(self, msg: UnicornConnectionSettings) -> None:
        await self.reconnect(msg)

    # Settings can be applied during unit creation, or sent at runtime
    # by other publishers.  Any time we get a new device settings, we
    # disconnect from current device and attempt to connect to the new device
    async def reconnect(self, settings: UnicornConnectionSettings) -> None:
        self.STATE.cur_settings = settings

    async def initialize(self) -> None:
        self.STATE.incoming = asyncio.Queue()
        await self.reconnect(self.SETTINGS)

    @ez.publisher(OUTPUT_GYROSCOPE)
    @ez.publisher(OUTPUT_SIGNAL)
    @ez.publisher(OUTPUT_BATTERY)
    @ez.publisher(OUTPUT_ACCELEROMETER)
    async def decode_and_publish(self) -> typing.AsyncGenerator:

        while True:
            block = await self.STATE.incoming.get()
            n_samp = len(block) / _UNICORN_PAYLOAD_LENGTH

            battery_level = 0
            bounds = np.arange(n_samp + 1) * _UNICORN_PAYLOAD_LENGTH
            eeg_frames, accel_frames, gyro_frames = [], [], []

            for start, stop in zip(bounds[:-1], bounds[1:]):
                payload = block[int(start):int(stop)]
                
                eeg_frames.append(np.array([
                    int.from_bytes(
                        payload[
                            _UNICORN_EEG_OFFSET +  i      * _UNICORN_BYTES_PER_EEG_CHANNEL :
                            _UNICORN_EEG_OFFSET + (i + 1) * _UNICORN_BYTES_PER_EEG_CHANNEL
                        ],
                        byteorder='big',
                        signed=True
                    ) for i in range(_UNICORN_EEG_CHANNELS_COUNT)
                ]))

                # Extract accelerometer data
                accel_frames.append(np.array([
                    int.from_bytes(
                        payload[
                            _UNICORN_ACC_OFFSET + i * _UNICORN_BYTES_PER_ACC_CHANNEL :
                            _UNICORN_ACC_OFFSET + (i + 1) * _UNICORN_BYTES_PER_ACC_CHANNEL
                        ],
                        byteorder='big',
                        signed=True
                    ) for i in range(_UNICORN_ACC_CHANNELS_COUNT)
                ]))

                # Extract gyroscope data
                gyro_frames.append(np.array([
                    int.from_bytes(
                        payload[
                            _UNICORN_GYR_OFFSET + i * _UNICORN_BYTES_PER_GYR_CHANNEL :
                            _UNICORN_GYR_OFFSET + (i + 1) * _UNICORN_BYTES_PER_GYR_CHANNEL
                        ],
                        byteorder='big',
                        signed=True
                    ) for i in range(_UNICORN_GYROSCOPE_CHANNELS_COUNT)
                ]))

                battery_level = (100.0 / 1.3) * ((payload[_UNICORN_BATTERY_LEVEL_OFFSET] & 0x0F) * 1.3 / 15.0)

            samp_idx = int.from_bytes(block[39:43], byteorder = 'little', signed = False)
            time = samp_idx / _UNICORN_FS

            time_axis = AxisArray.Axis.TimeAxis(
                fs = _UNICORN_FS,
                offset = time
            )
                
            eeg_message = AxisArray(
                data = _CALIBRATE_EEG(np.array(eeg_frames)),
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            acc_message = AxisArray(
                data = _CALIBRATE_ACC(np.array(accel_frames)),
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            gyr_message = AxisArray(
                data = _CALIBRATE_GYR(np.array(gyro_frames)),
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            yield self.OUTPUT_SIGNAL, eeg_message
            yield self.OUTPUT_BATTERY, battery_level
            yield self.OUTPUT_GYROSCOPE, gyr_message
            yield self.OUTPUT_ACCELEROMETER, acc_message


import socket

class NativeUnicornConnectionState(UnicornConnectionState):
    connect_event: asyncio.Event
    disconnect_event: asyncio.Event 

class NativeUnicornConnection(UnicornConnection):
    STATE: NativeUnicornConnectionState

    HAS_NATIVE_BLUETOOTH_SUPPORT = hasattr(socket, 'AF_BLUETOOTH')

    async def initialize(self) -> None:
        self.STATE.connect_event = asyncio.Event()
        self.STATE.disconnect_event = asyncio.Event()
        await super().initialize()

    async def reconnect(self, settings: UnicornConnectionSettings) -> None:
        self.STATE.disconnect_event.set()
        await super().reconnect(settings)
        self.STATE.connect_event.set()

    @ez.task
    async def handle_device(self) -> None:
        while True:
            await self.STATE.connect_event.wait()
            self.STATE.connect_event.clear()
            self.STATE.disconnect_event.clear()

            if self.STATE.cur_settings.address in (None, '', 'simulator'):
                ez.logger.debug(f"no device address specified")
                continue

            ez.logger.debug(f"opening RFCOMM connection on {self.STATE.cur_settings.address}")

            while True:
                try:
                    # We choose to do this instead of using pybluez so that we can interact
                    # with RFCOMM using non-blocking async calls.  Currently, this is only
                    # supported on linux with python built with bluetooth support.
                    # NOTE: sock.connect is blocking and could take a long time to return...
                    #     - this unit should probably live in its own process because of this...
                    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, proto = socket.BTPROTO_RFCOMM) # type: ignore
                    sock.connect((self.STATE.cur_settings.address, _UNICORN_PORT))
                    reader, writer = await asyncio.open_connection(sock = sock)
                except Exception as e:
                    ez.logger.debug(f'could not open RFCOMM connection to {self.STATE.cur_settings.address}: {e}')
                    if self.STATE.disconnect_event.is_set():
                        break
                    else:
                        await asyncio.sleep(60.0)
                        continue

                read_length = _UNICORN_PAYLOAD_LENGTH * self.STATE.cur_settings.n_samp

                try:
                    ez.logger.debug(f"starting stream")
                    writer.write(_UNICORN_START)
                    await writer.drain()

                    while True:

                        if self.STATE.disconnect_event.is_set():
                            break

                        try:
                            block = await reader.readexactly(read_length)
                            self.STATE.incoming.put_nowait(block)

                        except TimeoutError:
                            ez.logger.warning('timeout on unicorn connection. disconnected.')
                            break

                finally:
                    if not writer.is_closing():
                        ez.logger.debug(f"stopping stream")
                        writer.write(_UNICORN_STOP)
                        await writer.drain()
                        writer.close()
                        await writer.wait_closed()

                if self.STATE.disconnect_event.is_set():
                    break


import time

from PyQt5.QtWidgets import QApplication
from PyQt5 import QtBluetooth

class QtUnicornConnectionState(UnicornConnectionState):
    loop: asyncio.AbstractEventLoop

class QtUnicornConnection(UnicornConnection):
    STATE: QtUnicornConnectionState

    async def initialize(self) -> None:
        self.STATE.loop = asyncio.get_running_loop()
        await super().initialize()

    @ez.main
    def main_thread(self) -> None:

        if sys.platform == 'darwin':
            os.environ['QT_EVENT_DISPATCHER_CORE_FOUNDATION'] = '1'

        app = QApplication([])

        read_length = _UNICORN_PAYLOAD_LENGTH * self.STATE.cur_settings.n_samp

        sock = QtBluetooth.QBluetoothSocket(
            QtBluetooth.QBluetoothServiceInfo.RfcommProtocol # type: ignore
        )

        def socket_error(_) -> None:
            ez.logger.warning(sock.errorString())

        def disconnected() -> None:
            ez.logger.info('timeout on unicorn connection. disconnected.')

        def connected() -> None:
            sock.write(_UNICORN_START)

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
            _UNICORN_PORT
        )

        while True:
            app.processEvents()
            time.sleep(0.01)

from ezmsg.panel.tabbedapp import Tab, TabbedApp
from ezmsg.panel.timeseriesplot import TimeSeriesPlot

Unicorn = NativeUnicornConnection
if not NativeUnicornConnection.HAS_NATIVE_BLUETOOTH_SUPPORT:
    Unicorn = QtUnicornConnection

class UnicornDashboard(ez.Collection, TabbedApp):
    SETTINGS: UnicornConnectionSettings

    UNICORN = Unicorn()
    PLOT = TimeSeriesPlot()

    def configure(self) -> None:
        self.UNICORN.apply_settings(self.SETTINGS)

    @property
    def title(self) -> str:
        return 'Unicorn'

    @property
    def tabs(self) -> typing.List[Tab]:
        return [
            self.PLOT,
        ]
    
    def network(self) -> ez.NetworkDefinition:
        return (
            (self.UNICORN.OUTPUT_SIGNAL, self.PLOT.INPUT_SIGNAL),
        )


if __name__ == '__main__':

    from ezmsg.panel.application import Application, ApplicationSettings

    app = Application(
        ApplicationSettings(
            port = 0
        )
    )

    unicorn = UnicornDashboard(
        UnicornConnectionSettings(
            address = '60:B6:47:E1:26:9E'
        )
    )

    app.panels = {
        'unicorn': unicorn.app
    }

    ez.run(
        APP = app,
        UNICORN = unicorn
    )



