import asyncio
import typing
import time

import ezmsg.core as ez
import panel as pn
import numpy as np

from ezmsg.util.messages.axisarray import AxisArray

from .imuvqf import VQFFilter, VQFFilterSettings
from .orientationpane import OrientationPane

class StatusPaneSettings(ez.Settings):
    time_axis: str = 'time'

class StatusPaneState(ez.State):
    orientation_pane: OrientationPane
    battery_indicator: pn.indicators.LinearGauge
    status_alert: pn.pane.Alert
    last_signal_time: float = 0
    last_drop_time: float = 0

class StatusPane(ez.Unit):
    SETTINGS = StatusPaneSettings
    STATE = StatusPaneState

    INPUT_ORIENTATION = ez.InputStream(AxisArray)
    INPUT_BATTERY = ez.InputStream(float)
    INPUT_DROPPED = ez.InputStream(int)

    async def initialize(self) -> None:
        self.STATE.orientation_pane = OrientationPane(width = 100, height = 100)
        self.STATE.status_alert = pn.pane.Alert('Disconnected.', alert_type = 'danger')
        self.STATE.battery_indicator = pn.indicators.Progress(
            name = 'Battery',
            value = 0,
            max = 100,
            bar_color = 'success',
            sizing_mode = 'stretch_width'
        )

    def panel(self) -> pn.viewable.Viewable:
        return pn.Card(
            self.STATE.status_alert,
            pn.widgets.StaticText(name = 'Battery'),
            self.STATE.battery_indicator,
            pn.widgets.StaticText(name = 'Orientation'),
            pn.Row(
                pn.layout.HSpacer(),
                self.STATE.orientation_pane,
                pn.layout.HSpacer(),
                sizing_mode = 'stretch_width'
            ),
            title = 'Unicorn Status',
            sizing_mode = 'stretch_width'
        )
    
    @ez.subscriber(INPUT_ORIENTATION)
    async def on_orientation(self, msg: AxisArray) -> None:
        latest_quat = msg.as2d(self.SETTINGS.time_axis)[-1, :]
        self.STATE.orientation_pane.orientation = latest_quat.tolist()
        self.STATE.last_signal_time = time.time()

    @ez.subscriber(INPUT_BATTERY)
    async def on_battery(self, msg: float) -> None:
        percent = round(msg * 100.0)
        self.STATE.battery_indicator.value = percent
        if percent < 60.0:
            self.STATE.battery_indicator.bar_color = 'danger'

    @ez.subscriber(INPUT_DROPPED)
    async def on_dropped(self, msg: int) -> None:
        self.STATE.status_alert.object = f'__Warning:__ Dropped {msg} frames!'
        self.STATE.status_alert.alert_type = 'warning'
        self.STATE.last_drop_time = time.time()

    @ez.task
    async def monitor_status(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if time.time() - self.STATE.last_signal_time < 5.0:
                if time.time() - self.STATE.last_drop_time > 5.0:
                    self.STATE.status_alert.object = 'Streaming...'
                    self.STATE.status_alert.alert_type = 'success'
            else:
                self.STATE.status_alert.object = 'Disconnected.'
                self.STATE.status_alert.alert_type = 'danger'

        

class UnicornStatusSettings(ez.Settings):
    time_axis: str = 'time'

class UnicornStatus(ez.Collection):
    SETTINGS = UnicornStatusSettings

    VQF = VQFFilter()
    PANE = StatusPane()

    INPUT_MOTION = ez.InputStream(AxisArray)
    INPUT_BATTERY = ez.InputStream(float)
    INPUT_DROPPED = ez.InputStream(int)

    def configure(self) -> None:
        self.VQF.apply_settings(
            VQFFilterSettings(
                time_axis = self.SETTINGS.time_axis
            )
        )

    def panel(self) -> pn.viewable.Viewable:
        return self.PANE.panel()

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.INPUT_MOTION, self.VQF.INPUT_MOTION),
            (self.VQF.OUTPUT_ORIENTATION, self.PANE.INPUT_ORIENTATION),
            (self.INPUT_BATTERY, self.PANE.INPUT_BATTERY),
            (self.INPUT_DROPPED, self.PANE.INPUT_DROPPED),
        )


def quat_mult(q1, q2):
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
    ], dtype = np.float64)

def quat_inv(q):
    # Assumes q is a unit quaternion
    w, x, y, z = q
    return np.array([w, -x, -y, -z], dtype = np.float64)