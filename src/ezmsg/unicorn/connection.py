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
        await self.reconnect(self.SETTINGS)

    async def simulator(self) -> None:
        sleep_t = UnicornProtocol.FS / self.STATE.cur_settings.n_samp
        while True:
            data = None # TODO: Simulate
            # self.STATE.incoming.put_nowait(data)
            await asyncio.sleep(sleep_t)
        
    @ez.publisher(OUTPUT_GYROSCOPE)
    @ez.publisher(OUTPUT_SIGNAL)
    @ez.publisher(OUTPUT_BATTERY)
    @ez.publisher(OUTPUT_ACCELEROMETER)
    async def decode_and_publish(self) -> typing.AsyncGenerator:

        while True:
            block = await self.STATE.incoming.get()

            n_samp = len(block) / UnicornProtocol.PAYLOAD_LENGTH

            battery_level = 0
            bounds = np.arange(n_samp + 1) * UnicornProtocol.PAYLOAD_LENGTH
            eeg_frames, accel_frames, gyro_frames = [], [], []

            for start, stop in zip(bounds[:-1], bounds[1:]):
                payload = block[int(start):int(stop)]
                
                eeg_frames.append(np.array([
                    int.from_bytes(
                        payload[
                            UnicornProtocol.EEG_OFFSET +  i      * UnicornProtocol.BYTES_PER_EEG_CHANNEL :
                            UnicornProtocol.EEG_OFFSET + (i + 1) * UnicornProtocol.BYTES_PER_EEG_CHANNEL
                        ],
                        byteorder='big',
                        signed=True
                    ) for i in range(UnicornProtocol.EEG_CHANNELS_COUNT)
                ]))

                # Extract accelerometer data
                accel_frames.append(np.array([
                    int.from_bytes(
                        payload[
                            UnicornProtocol.ACC_OFFSET + i * UnicornProtocol.BYTES_PER_ACC_CHANNEL :
                            UnicornProtocol.ACC_OFFSET + (i + 1) * UnicornProtocol.BYTES_PER_ACC_CHANNEL
                        ],
                        byteorder='big',
                        signed=True
                    ) for i in range(UnicornProtocol.ACC_CHANNELS_COUNT)
                ]))

                # Extract gyroscope data
                gyro_frames.append(np.array([
                    int.from_bytes(
                        payload[
                            UnicornProtocol.GYR_OFFSET + i * UnicornProtocol.BYTES_PER_GYR_CHANNEL :
                            UnicornProtocol.GYR_OFFSET + (i + 1) * UnicornProtocol.BYTES_PER_GYR_CHANNEL
                        ],
                        byteorder='big',
                        signed=True
                    ) for i in range(UnicornProtocol.GYROSCOPE_CHANNELS_COUNT)
                ]))

                battery_level = (100.0 / 1.3) * ((payload[UnicornProtocol.BATTERY_LEVEL_OFFSET] & 0x0F) * 1.3 / 15.0)

            samp_idx = int.from_bytes(block[39:43], byteorder = 'little', signed = False)
            # TODO: Check for dropped packets?

            time_axis = AxisArray.Axis.TimeAxis(
                fs = UnicornProtocol.FS,
                offset = time.time() - (n_samp / UnicornProtocol.FS)
            )
                
            eeg_message = AxisArray(
                data = UnicornProtocol.calibrate_ephys(np.array(eeg_frames)),
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            acc_message = AxisArray(
                data = UnicornProtocol.calibrate_accel(np.array(accel_frames)),
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            gyr_message = AxisArray(
                data = UnicornProtocol.calibrate_gyro(np.array(gyro_frames)),
                dims = ['time', 'ch'],
                axes = {'time': time_axis}
            )

            yield self.OUTPUT_SIGNAL, eeg_message
            yield self.OUTPUT_BATTERY, battery_level
            yield self.OUTPUT_GYROSCOPE, gyr_message
            yield self.OUTPUT_ACCELEROMETER, acc_message