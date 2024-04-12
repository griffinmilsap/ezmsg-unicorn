import asyncio
import typing
import time

import ezmsg.core as ez
import numpy as np

from ezmsg.util.messages.axisarray import AxisArray

from .protocol import UnicornProtocol


class UnicornConnectionSettings(ez.Settings):
    # if addr == None; don't connect to any device.
    address: typing.Optional[str] = None # "XX:XX:XX:XX:XX:XX"
    n_samp: int = 50

class UnicornConnectionState(ez.State):
    cur_settings: UnicornConnectionSettings
    incoming: asyncio.Queue[bytes]
    simulator_task: typing.Optional[asyncio.Task]
    reconnect_event: asyncio.Event
    last_count: typing.Optional[int] = None

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
        self.STATE.reconnect_event.set()

        if self.STATE.simulator_task is not None:
            self.STATE.simulator_task.cancel()
            try:
                await self.STATE.simulator_task
            except asyncio.CancelledError:
                pass
            self.STATE.simulator_task = None

        if self.STATE.cur_settings.address == 'simulator':
            self.STATE.simulator_task = asyncio.create_task(self.simulator())

    async def initialize(self) -> None:
        self.STATE.incoming = asyncio.Queue()
        self.STATE.reconnect_event = asyncio.Event()
        self.STATE.simulator_task = None
        self.STATE.last_count = None
        await self.reconnect(self.SETTINGS)

    async def simulator(self) -> None:
        sleep_t = UnicornProtocol.FS / self.STATE.cur_settings.n_samp
        while True:
            data = None # TODO: Capture some data and read it out from file on loop here
            # self.STATE.incoming.put_nowait(data)
            await asyncio.sleep(sleep_t) # FIXME: Account for last_publish_time
        
    @ez.publisher(OUTPUT_GYROSCOPE)
    @ez.publisher(OUTPUT_SIGNAL)
    @ez.publisher(OUTPUT_BATTERY)
    @ez.publisher(OUTPUT_ACCELEROMETER)
    async def decode_and_publish(self) -> typing.AsyncGenerator:

        while True:
            decoder = UnicornProtocol(await self.STATE.incoming.get())

            count = decoder.packet_count()
            if self.STATE.last_count is None:
                self.STATE.last_count = count[0].item() - 1
            dropped_frames = np.diff(count, prepend = self.STATE.last_count).sum() - len(count)
            self.STATE.last_count = count[-1]
            if dropped_frames:
                ez.logger.info(f'{dropped_frames=}')


            time_axis = AxisArray.Axis.TimeAxis(
                fs = UnicornProtocol.FS,
                offset = time.time() - (decoder.n_samp / UnicornProtocol.FS)
            )
                
            eeg_message = AxisArray(
                data = decoder.eeg(),
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            acc, gyr = decoder.motion()

            acc_message = AxisArray(
                data = acc,
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            gyr_message = AxisArray(
                data = gyr,
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            yield self.OUTPUT_SIGNAL, eeg_message
            yield self.OUTPUT_BATTERY, decoder.battery()[0].item()
            yield self.OUTPUT_GYROSCOPE, gyr_message
            yield self.OUTPUT_ACCELEROMETER, acc_message