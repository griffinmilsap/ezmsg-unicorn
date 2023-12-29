
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
_UNICORN_PAYLOAD_LENGTH = 45

_CALIBRATE_EEG = lambda x: x * (4500000.0 / 50331642.0)


# In order to function with UnicornDashboard, 
# all settings should have defaults
class UnicornDeviceSettings(ez.Settings):
    # if addr == None; don't connect to any device.
    address: typing.Optional[str] = None # "XX:XX:XX:XX:XX:XX"
    n_samp: int = 50


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

        if not hasattr(socket, 'AF_BLUETOOTH'):
            ez.logger.error('Python was not built with Bluetooth support.')
            return

        while True:
            await self.STATE.connect_event.wait()
            self.STATE.connect_event.clear()
            self.STATE.disconnect_event.clear()

            if (
                self.STATE.device_settings is None or \
                self.STATE.device_settings.address in (None, '', 'simulator')
            ):
                ez.logger.debug(f"no device address specified")
                continue

            ez.logger.debug(f"opening RFCOMM connection on {self.STATE.device_settings.address}")

            try: 
                # We choose to do this instead of using pybluez so that we can interact
                # with RFCOMM using non-blocking async calls.  Currently, this is only
                # supported on linux with python built with bluetooth support.
                # NOTE: sock.connect is blocking and could take a long time to return...
                #     - this unit should probably live in its own process because of this...
                sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, proto = socket.BTPROTO_RFCOMM) # type: ignore
                sock.connect((self.STATE.device_settings.address, _UNICORN_PORT))
                reader, writer = await asyncio.open_connection(sock = sock)
            except Exception as e:
                ez.logger.warning(f'could not open RFCOMM connection to {self.STATE.device_settings.address}: {e}')
                continue

            try:
                ez.logger.debug(f"starting stream")
                writer.write(b'\x61\x7C\x87') # Start Acquisition
                await writer.drain()
            
                while True:

                    if self.STATE.disconnect_event.is_set():
                        break
                    
                    block = await reader.readexactly(_UNICORN_PAYLOAD_LENGTH * self.STATE.device_settings.n_samp)

                    battery_level = 0
                    bounds = np.arange(self.STATE.device_settings.n_samp + 1) * _UNICORN_PAYLOAD_LENGTH
                    eeg_frames = []
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

                    yield self.OUTPUT_SIGNAL, eeg_message
                    yield self.OUTPUT_BATTERY, battery_level

            finally:
                ez.logger.debug(f"stopping stream")
                writer.write(b"\x63\x5C\xC5") # Stop acquisition
                await writer.drain()
                writer.close()
                await writer.wait_closed()

                        
if __name__ == '__main__':

    import argparse

    from ezmsg.util.debuglog import DebugLog

    parser = argparse.ArgumentParser(
        description = 'Unicorn Device'
    )

    device_group = parser.add_argument_group('device')

    device_group.add_argument(
        '-a', '--address',
        type = str,
        help = 'bluetooth address of Unicorn to stream data from (XX:XX:XX:XX:XX:XX)',
    )

    device_group.add_argument(
        '--n_samp',
        type = int,
        help = 'number of data frames per message',
        default = 50
    )

    class Args:
        address: typing.Optional[str]
        n_samp: int

    args = parser.parse_args(namespace = Args)

    if args.address in (None, '', 'simulator'):
        ez.logger.error('no device to connect to; exiting.')
        
    else:
        DEVICE = UnicornDevice(
            UnicornDeviceSettings(
                args.address,
                args.n_samp
            )
        )

        LOG = DebugLog()

        ez.run(
            device = DEVICE,
            log = LOG,
            connections=(
                (DEVICE.OUTPUT_SIGNAL, LOG.INPUT),
                (DEVICE.OUTPUT_BATTERY, LOG.INPUT)
            )
        )
