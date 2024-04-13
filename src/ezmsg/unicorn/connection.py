import asyncio
import typing
import time

import ezmsg.core as ez
import numpy as np
import numpy.typing as npt

from ezmsg.util.messages.axisarray import AxisArray
from ezmsg.util.generator import consumer

from .protocol import UnicornProtocol


class UnicornConnectionSettings(ez.Settings):
    # if addr == None; don't connect to any device.
    address: typing.Optional[str] = None # "XX:XX:XX:XX:XX:XX"
    n_samp: int = 50

class UnicornConnectionState(ez.State):
    cur_settings: UnicornConnectionSettings
    simulator_task: typing.Optional[asyncio.Task]
    reconnect_event: asyncio.Event

    signal_queue: asyncio.Queue[AxisArray]
    motion_queue: asyncio.Queue[AxisArray]
    battery_queue: asyncio.Queue[float]

class UnicornConnection(ez.Unit):
    SETTINGS: UnicornConnectionSettings
    STATE: UnicornConnectionState

    INPUT_SETTINGS = ez.InputStream(UnicornConnectionSettings)
    
    OUTPUT_MOTION = ez.OutputStream(AxisArray)
    OUTPUT_SIGNAL = ez.OutputStream(AxisArray)
    OUTPUT_BATTERY = ez.OutputStream(float)

    @ez.subscriber(INPUT_SETTINGS)
    async def on_settings(self, msg: UnicornConnectionSettings) -> None:
        await self.reconnect(msg)

    @ez.publisher(OUTPUT_SIGNAL)
    async def pub_signal(self) -> typing.AsyncGenerator:
        while True:
            signal = await self.STATE.signal_queue.get()
            yield self.OUTPUT_SIGNAL, signal
        
    @ez.publisher(OUTPUT_MOTION)
    async def pub_motion(self) -> typing.AsyncGenerator:
        while True:
            motion = await self.STATE.motion_queue.get()
            yield self.OUTPUT_MOTION, motion

    # NOTE: Subscribers not get a battery update corresponding to every signal/motion update
    @ez.publisher(OUTPUT_BATTERY)
    async def pub_battery(self) -> typing.AsyncGenerator:
        while True:
            battery = await self.STATE.battery_queue.get()
            yield self.OUTPUT_BATTERY, battery

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
        self.STATE.reconnect_event = asyncio.Event()
        self.STATE.simulator_task = None

        self.STATE.signal_queue = asyncio.Queue()
        self.STATE.motion_queue = asyncio.Queue()
        self.STATE.battery_queue = asyncio.Queue()

        await self.reconnect(self.SETTINGS)

    async def simulator(self) -> None:
        sleep_t = UnicornProtocol.FS / self.STATE.cur_settings.n_samp
        while True:
            data = None # TODO: Capture some data and read it out from file on loop here
            # self.STATE.incoming.put_nowait(data)
            await asyncio.sleep(sleep_t) # FIXME: Account for last_publish_time

    @consumer
    def interpolator(self) -> typing.Generator[None, bytes, None]:
        """ As a wireless EEG device, packets WILL be dropped.
        This interpolator fills in the blanks and queues outputs
        NOTE: this will result in messages with different numbers of frames"""
        
        last_eeg_frame = np.array([])
        last_motion_frame = np.array([])
        last_count: typing.Optional[int] = None
        
        while True:
            block = yield None
            timestamp = time.time() # Log timestamp as close to receipt as possible
            
            decoder = UnicornProtocol(block)

            count = decoder.packet_count()
            if last_count is None:
                last_count = count[0].item() - 1
            dropped_frames = np.diff(count, prepend = last_count).sum() - len(count)

            eeg = decoder.eeg()
            motion = decoder.motion()

            if dropped_frames > 0:
                ez.logger.info(f'Unicorn {dropped_frames=}')
                count_buffer = np.concatenate((np.array([last_count]), count), axis = 0)

                last_eeg_frame = last_eeg_frame if last_eeg_frame.size else eeg[0, ...]
                eeg_buffer = np.concatenate((last_eeg_frame, eeg), axis = 0)

                last_motion_frame = last_motion_frame if last_motion_frame.size else motion[0, ...]
                motion_buffer = np.concatenate((last_motion_frame, motion), axis = 0)

                interp_count = np.arange(count_buffer[0].item(), count_buffer[-1].item() + 1)

                def interp(a: np.ndarray) -> np.ndarray:
                    return np.interp(interp_count, count_buffer, a)
                
                eeg = np.apply_along_axis(interp, 0, eeg_buffer)[1:, ...]
                motion = np.apply_along_axis(interp, 0, motion_buffer)[1:, ...]
                count = interp_count[1:]

            time_axis = AxisArray.Axis.TimeAxis(
                fs = UnicornProtocol.FS,
                offset = timestamp - (len(count) / UnicornProtocol.FS)
            )
                
            self.STATE.signal_queue.put_nowait(AxisArray(
                data = eeg,
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            ))

            self.STATE.motion_queue.put_nowait(AxisArray(
                data = motion,
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            ))

            self.STATE.battery_queue.put_nowait(decoder.battery()[-1].item())

            last_eeg_frame = eeg[-1:, ...]
            last_motion_frame = motion[-1:, ...]
            last_count = count[-1]
