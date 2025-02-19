import typing

from dataclasses import replace

import numpy as np
import ezmsg.core as ez

vqf_exists = True
try:
    from vqf import VQF
except ImportError:
    ez.logger.warning('install vqf for orientation estimation')
    vqf_exists = False

from ezmsg.util.messages.axisarray import AxisArray, view2d

class VQFFilterSettings(ez.Settings):
    time_axis: typing.Union[str, int] = 'time'
    ch_axis: typing.Union[str, int] = 'ch'

class VQFFilterState(ez.State):
    vqf: typing.Optional[VQF] = None

class VQFFilter(ez.Unit):
    """ Estimates orientation from IMU data using VQF https://github.com/dlaidig/vqf """
    SETTINGS = VQFFilterSettings
    STATE = VQFFilterState

    INPUT_MOTION = ez.InputStream(AxisArray) # accx, accy, accz, gyrx, gyry, gyrz
    OUTPUT_ORIENTATION = ez.OutputStream(AxisArray) # quaternion

    @ez.subscriber(INPUT_MOTION)
    @ez.publisher(OUTPUT_ORIENTATION)
    async def on_motion(self, msg: AxisArray) -> typing.AsyncGenerator:
        # Assumptions: 
        # * msg has two axes, SETTINGS.time_axis and SETTINGS.ch_axis -- order doesn't matter
        # * ch has 6 elements, in order, [accx, accy, accz, gyrx, gyry, gyrz]
        # * acc values are in m/s^2
        # * gyr values are in rad/s

        if not vqf_exists: return

        # Output is quaternions in [w x y z] ("scalar first") format
        # Note scipy.spatial.transform expects [x y z w] ("scalar last") format
        time_axis = msg.ax(self.SETTINGS.time_axis)
        if not self.STATE.vqf or time_axis.axis.gain != self.STATE.vqf.coeffs['gyrTs']:
            self.STATE.vqf = VQF(time_axis.axis.gain)

        motion_data = msg.data.copy()
        with view2d(motion_data, time_axis.idx) as data:
            acc = np.ascontiguousarray(data[:, :3] * 9.8) # Convert from g to m/s^2
            gyr = np.ascontiguousarray(np.deg2rad(data[:, 3:6])) # Convert from deg/sec to rad/sec
            data[:, :4] = self.STATE.vqf.updateBatch(gyr, acc)['quat6D']

        out_data = np.take(motion_data, indices = np.arange(4), axis = msg.axis_idx(self.SETTINGS.ch_axis))
        yield self.OUTPUT_ORIENTATION, replace(msg, data = out_data)
