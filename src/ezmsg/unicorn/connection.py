import asyncio
import typing
import time

from importlib.resources import files
from dataclasses import dataclass, field

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
    reconnect_event: asyncio.Event

    signal_queue: asyncio.Queue[AxisArray]
    motion_queue: asyncio.Queue[AxisArray]
    battery_queue: asyncio.Queue[float]
    dropped_queue: asyncio.Queue[int]

class UnicornConnection(ez.Unit):
    SETTINGS = UnicornConnectionSettings
    STATE = UnicornConnectionState

    INPUT_SETTINGS = ez.InputStream(UnicornConnectionSettings)
    
    OUTPUT_MOTION = ez.OutputStream(AxisArray, num_buffers = 2)
    OUTPUT_SIGNAL = ez.OutputStream(AxisArray, num_buffers = 2)
    OUTPUT_BATTERY = ez.OutputStream(float)
    OUTPUT_DROPPED = ez.OutputStream(int)

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

    @ez.publisher(OUTPUT_DROPPED)
    async def pub_dropped(self) -> typing.AsyncGenerator:
        while True:
            dropped = await self.STATE.dropped_queue.get()
            yield self.OUTPUT_DROPPED, dropped

    # NOTE: battery updates every n_samp frames acquired
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

        if isinstance(self.STATE.cur_settings.address, str) and 'simulator' in self.STATE.cur_settings.address:
            ez.logger.info(f'Starting Unicorn simulator task: {self.STATE.cur_settings.address}')
            self.STATE.simulator_task = asyncio.create_task(
                self.simulator(
                    recording = self.STATE.cur_settings.address
                )
            )

    async def initialize(self) -> None:
        self.STATE.reconnect_event = asyncio.Event()
        self.STATE.simulator_task = None

        self.STATE.signal_queue = asyncio.Queue()
        self.STATE.motion_queue = asyncio.Queue()
        self.STATE.battery_queue = asyncio.Queue()
        self.STATE.dropped_queue = asyncio.Queue()

        await self.reconnect(self.SETTINGS)

    async def shutdown(self) -> None:
        if self.STATE.simulator_task is not None:
            self.STATE.simulator_task.cancel()
            try:
                await self.STATE.simulator_task
            except asyncio.CancelledError:
                pass
            self.STATE.simulator_task = None

    async def simulator(self, recording: str) -> None:
        rec_dir = files('ezmsg.unicorn.recordings')
        recs = [f.stem for f in rec_dir.glob('*.bin')]
        if recording not in recs:
            ez.logger.error(f'Could not find simulator recording: {recording}')
            ez.logger.info(f'Available simulators: {recs}')
            return
        
        data = rec_dir.joinpath(f'{recording}.bin').read_bytes()
        read_length = UnicornProtocol.PAYLOAD_LENGTH * self.STATE.cur_settings.n_samp

        try:
            while True: # Loop Recording
                cur_idx: int = 0
                last_samp: typing.Optional[int] = None
                interpolator = self.interpolator()

                while True: # Acquire Loop
                    read_stop = cur_idx + read_length
                    if read_stop >= len(data):
                        ez.logger.info('Simulator record looping')
                        break
                    block = data[cur_idx:read_stop]
                    cur_idx = read_stop

                    # Recording may have dropped packets (as a real device may)
                    # Figure out how long to sleep in the presence of these dropped packets
                    samp_indices = UnicornProtocol(block).packet_count()
                    if last_samp is None:
                        last_samp = samp_indices[0].item() - 1

                    assert last_samp is not None
                    
                    cur_samp = samp_indices[-1].item()
                    delta_samp = cur_samp - last_samp
                    last_samp = cur_samp

                    await asyncio.sleep(delta_samp / UnicornProtocol.FS)
                    interpolator.send(block)
        except asyncio.CancelledError:
            pass

    @consumer
    def interpolator(self) -> typing.Generator[None, bytes, None]:
        """ As a wireless EEG device, packets WILL be dropped.
        This interpolator fills in the blanks and queues outputs
        NOTE: this will result in messages with different numbers of frames"""
        
        last_eeg_frame = np.array([])
        last_motion_frame = np.array([])
        last_count: typing.Optional[int] = None

        TimeAxis = AxisArray.Axis.TimeAxis
        if hasattr(AxisArray, 'TimeAxis'):
            TimeAxis = AxisArray.TimeAxis
        
        while True:
            block = yield None
            timestamp = time.time() # Log timestamp as close to receipt as possible
            
            decoder = UnicornProtocol(block)

            count = decoder.packet_count()
            if last_count is None:
                last_count = count[0].item() - 1
            dropped_frames: int = np.diff(count, prepend = last_count).sum() - len(count)

            eeg = decoder.eeg()
            motion = decoder.motion()

            interpolated: npt.NDArray[np.bool] = np.array([False] * len(count))
            if dropped_frames > 0:
                ez.logger.debug(f'Unicorn {dropped_frames=}')
                self.STATE.dropped_queue.put_nowait(dropped_frames)

                count_buffer = np.concatenate((np.array([last_count]), count), axis = 0)

                last_eeg_frame = last_eeg_frame if last_eeg_frame.size else eeg[np.newaxis, 0, ...]
                eeg_buffer = np.concatenate((last_eeg_frame, eeg), axis = 0)

                last_motion_frame = last_motion_frame if last_motion_frame.size else motion[np.newaxis, 0, ...]
                motion_buffer = np.concatenate((last_motion_frame, motion), axis = 0)

                interp_count = np.arange(count_buffer[0].item(), count_buffer[-1].item() + 1)

                def interp(a: np.ndarray) -> np.ndarray:
                    return np.interp(interp_count, count_buffer, a)
                
                eeg = np.apply_along_axis(interp, 0, eeg_buffer)[1:, ...]
                motion = np.apply_along_axis(interp, 0, motion_buffer)[1:, ...]
                count = interp_count[1:]

                interpolated = ~np.isin(interp_count, count_buffer)

            axes = {}

            axes['time'] = TimeAxis(
                fs = UnicornProtocol.FS,
                offset = timestamp - (len(count) / UnicornProtocol.FS)
            )

            if hasattr(AxisArray, 'CoordinateAxis'):
                axes['interpolated'] = AxisArray.CoordinateAxis(
                    data = interpolated,
                    dims = ['time']
                )
                
            self.STATE.signal_queue.put_nowait(AxisArray(
                data = eeg,
                dims = ['time', 'ch'],
                axes = axes.copy(),
                key = f'EEG_{self.STATE.cur_settings.address}'
            ))

            self.STATE.motion_queue.put_nowait(AxisArray(
                data = motion,
                dims = ['time', 'ch'],
                axes = axes.copy(),
                key = f'MOTION_{self.STATE.cur_settings.address}'
            ))

            self.STATE.battery_queue.put_nowait(decoder.battery()[-1].item())

            last_eeg_frame = eeg[-1:, ...]
            last_motion_frame = motion[-1:, ...]
            last_count = count[-1]
