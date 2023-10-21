
import typing
import asyncio
import socket

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
_UNICORN_EEG_OFFSET = _UNICORN_BATTERY_LEVEL_OFFSET + _UNICORN_BATTERY_LEVEL_LENGTH
_UNICORN_FS = 250.0

_CALIBRATE_EEG = lambda x: x * (4500000.0 / 50331642.0)


# In order to function with UnicornDashboard, 
# all settings should have defaults
class UnicornDeviceSettings(ez.Settings):
    # if addr == None; don't connect to any device.
    addr: typing.Optional[str] = None # "XX:XX:XX:XX:XX:XX"


class UnicornDeviceState(ez.State):
    device_settings: typing.Optional[UnicornDeviceSettings] = None
    connect_event: asyncio.Event
    disconnect_event: asyncio.Event


class UnicornDevice(ez.Unit):

    SETTINGS: UnicornDeviceSettings
    STATE: UnicornDeviceState
        
    INPUT_SETTINGS = ez.InputStream(UnicornDeviceSettings)

    OUTPUT_SIGNAL = ez.OutputStream(AxisArray)
    OUTPUT_BATTERY = ez.OutputStream(float)

    async def initialize(self) -> None:
        self.STATE.connect_event = asyncio.Event()
        self.STATE.disconnect_event = asyncio.Event()
        await self.reconnect(self.SETTINGS)

    @ez.subscriber(INPUT_SETTINGS)
    async def on_settings(self, msg: UnicornDeviceSettings) -> None:
        await self.reconnect(msg)

    # Settings can be applied during unit creation, or sent at runtime
    # by other publishers.  Any time we get a new device settings, we 
    # disconnect from current device and attempt to connect to the new device
    async def reconnect(self, settings: UnicornDeviceSettings) -> None:
        self.STATE.disconnect_event.set()
        self.STATE.device_settings = settings
        self.STATE.connect_event.set()

    @ez.publisher(OUTPUT_SIGNAL)
    @ez.publisher(OUTPUT_BATTERY)
    async def handle_device(self) -> typing.AsyncGenerator:

        while True:
            await self.STATE.connect_event.wait()
            self.STATE.connect_event.clear()
            self.STATE.disconnect_event.clear()

            if self.STATE.device_settings.addr in (None, ''):
                ez.logger.debug(f"no device address specified")
                continue

            ez.logger.debug(f"opening RFCOMM connection on {self.STATE.device_settings.addr}")

            try: 
                # We choose to do this instead of using pybluez so that we can interact
                # with RFCOMM using non-blocking async calls.  Currently, this is only
                # supported on linux with python built with bluetooth support.
                sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, proto = socket.BTPROTO_RFCOMM)
                sock.connect((self.STATE.device_settings.addr, _UNICORN_PORT))
                reader, writer = await asyncio.open_connection(sock = sock)

                ez.logger.debug(f"starting stream")
                writer.write(b'\x61\x7C\x87') # Start Acquisition
                await writer.drain()
            
                while True:

                    if self.STATE.disconnect_event.is_set():
                        break
                    
                    payload = await reader.read(45)

                    eeg_data = np.array([[
                        _CALIBRATE_EEG(int.from_bytes(
                            payload[
                                _UNICORN_EEG_OFFSET +  i      * _UNICORN_BYTES_PER_EEG_CHANNEL :
                                _UNICORN_EEG_OFFSET + (i + 1) * _UNICORN_BYTES_PER_EEG_CHANNEL
                            ], 
                            byteorder='big', 
                            signed=True
                        )) for i in range(_UNICORN_EEG_CHANNELS_COUNT)
                    ]])

                    battery_level = (100.0 / 1.3) * ((payload[_UNICORN_BATTERY_LEVEL_OFFSET] & 0x0F) * 1.3 / 15.0)

                    samp_idx = int.from_bytes(payload[39:43], byteorder = 'little', signed = False)
                    time = samp_idx / _UNICORN_FS

                    time_axis = AxisArray.Axis.TimeAxis(
                        fs = _UNICORN_FS,
                        offset = time
                    )
                    
                    eeg_message = AxisArray(
                        data = eeg_data,
                        dims = ['time', 'ch'],
                        axes = {'time': time_axis}
                    )

                    yield self.OUTPUT_SIGNAL, eeg_message
                    yield self.OUTPUT_BATTERY, battery_level

            finally:
                ez.logger.debug(f"stopping stream")
                writer.write(b"\x63\x5C\xC5") # Stop acquisition
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                        
