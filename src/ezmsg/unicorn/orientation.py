import time

import ezmsg.core as ez
import panel as pn

from ezmsg.util.messages.axisarray import AxisArray

from .imuvqf import VQFFilter, VQFFilterSettings
from .orientationpane import OrientationPane

class OrientationVisualSettings(ez.Settings):
    time_axis: str = 'time'

class OrientationVisualState(ez.State):
    pane: OrientationPane

class OrientationVisual(ez.Unit):
    SETTINGS = OrientationVisualSettings
    STATE = OrientationVisualState

    INPUT_ORIENTATION = ez.InputStream(AxisArray)

    async def initialize(self) -> None:
        self.STATE.pane = OrientationPane(width = 200, height = 200)

    def pane(self) -> pn.viewable.Viewable:
        return self.STATE.pane
    
    @ez.subscriber(INPUT_ORIENTATION)
    async def on_orientation(self, msg: AxisArray) -> None:
        latest_quat = msg.as2d(self.SETTINGS.time_axis)[-1, :]
        self.STATE.pane.orientation = latest_quat
        

class OrientationSettings(ez.Settings):
    time_axis: str = 'time'

class Orientation(ez.Collection):
    SETTINGS = OrientationSettings

    VQF = VQFFilter()
    VISUAL = OrientationVisual()

    INPUT_MOTION = ez.InputStream(AxisArray)

    def configure(self) -> None:
        self.VQF.apply_settings(
            VQFFilterSettings(
                time_axis = self.SETTINGS.time_axis
            )
        )

    def pane(self) -> pn.viewable.Viewable:
        return self.VISUAL.pane()

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.INPUT_MOTION, self.VQF.INPUT_MOTION),
            (self.VQF.OUTPUT_ORIENTATION, self.VISUAL.INPUT_ORIENTATION)
        )
