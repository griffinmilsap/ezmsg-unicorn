import typing

from dataclasses import field

import ezmsg.core as ez
import panel as pn

from ezmsg.util.messages.axisarray import AxisArray
from ezmsg.panel.timeseriesplot import TimeSeriesPlot, TimeSeriesPlotSettings
from ezmsg.panel.tabbedapp import Tab
from ezmsg.sigproc.window import Window, WindowSettings

from .discovery import UnicornDiscovery, UnicornDiscoverySettings
from .device import Unicorn, UnicornSettings
from .status import UnicornStatus, UnicornStatusSettings

class UnicornDashboardSettings(ez.Settings):
    device_settings: UnicornSettings = field(
        default_factory = UnicornSettings
    )
    plot_update_rate: float = 5.0 # Hz


class UnicornDashboard(ez.Collection, Tab):

    SETTINGS = UnicornDashboardSettings

    OUTPUT_SIGNAL = ez.OutputStream(AxisArray)
    OUTPUT_MOTION = ez.OutputStream(AxisArray)
    OUTPUT_BATTERY = ez.OutputStream(float)
    OUTPUT_DROPPED = ez.OutputStream(int)

    PLOT = TimeSeriesPlot(TimeSeriesPlotSettings(name = '', downsample_factor = 2))

    DISCOVERY = UnicornDiscovery()
    DEVICE = Unicorn()
    PLOT_WINDOW = Window()
    STATUS = UnicornStatus()

    def configure(self) -> None:
        self.DEVICE.apply_settings(self.SETTINGS.device_settings)
        self.DISCOVERY.apply_settings(
            UnicornDiscoverySettings(
                default_settings = self.SETTINGS.device_settings
            )
        )

        plot_per = 1.0 / self.SETTINGS.plot_update_rate
        self.PLOT_WINDOW.apply_settings(
            WindowSettings(
                axis = 'time', 
                window_dur = plot_per, 
                window_shift = plot_per
            )
        )

        self.STATUS.apply_settings(
            UnicornStatusSettings(
                time_axis = 'time',
            )
        )

    @property
    def title(self) -> str:
        return 'Unicorn Device'
    
    def sidebar(self) -> pn.viewable.Viewable:
        return pn.Column(
            # self.DISCOVERY.controls(), # Unsupported on all but Linux...
            self.STATUS.panel(),
            self.PLOT.sidebar(),
        )
    
    def content(self) -> pn.viewable.Viewable:
        return self.PLOT.content()

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.DISCOVERY.OUTPUT_SETTINGS, self.DEVICE.INPUT_SETTINGS),
            (self.DEVICE.OUTPUT_SIGNAL, self.PLOT_WINDOW.INPUT_SIGNAL),
            (self.PLOT_WINDOW.OUTPUT_SIGNAL, self.PLOT.INPUT_SIGNAL),
            (self.DEVICE.OUTPUT_SIGNAL, self.OUTPUT_SIGNAL),
            (self.DEVICE.OUTPUT_MOTION, self.OUTPUT_MOTION),
            (self.DEVICE.OUTPUT_BATTERY, self.OUTPUT_BATTERY),
            (self.DEVICE.OUTPUT_DROPPED, self.OUTPUT_DROPPED),
            (self.DEVICE.OUTPUT_MOTION, self.STATUS.INPUT_MOTION),
            (self.DEVICE.OUTPUT_BATTERY, self.STATUS.INPUT_BATTERY),
            (self.DEVICE.OUTPUT_DROPPED, self.STATUS.INPUT_DROPPED), 
        )
    
    def process_components(self) -> typing.Collection[ez.Component]:
        return (self.DEVICE, )


def dashboard() -> None:
    import argparse
    from ezmsg.panel.application import Application, ApplicationSettings


    parser = argparse.ArgumentParser(
        description = 'Unicorn Dashboard'
    )

    dashboard_group = parser.add_argument_group('dashboard')

    dashboard_group.add_argument(
        '--port',
        type = int,
        help = 'port to host dashboard on. [0 = any open port, default]',
        default = 0
    )

    device_group = parser.add_argument_group('device')

    device_group.add_argument(
        '--address', '-a',
        type = str,
        help = 'bluetooth address of Unicorn to autoconnect to (XX:XX:XX:XX:XX:XX)',
        default = 'simulator'
    )

    device_group.add_argument(
        '--n_samp',
        type = int,
        help = 'number of data frames per message',
        default = 10
    )

    class Args:
        port: int
        address: typing.Optional[str]
        n_samp: int

    args = parser.parse_args(namespace = Args)

    APP = Application(ApplicationSettings(port = args.port))
    DASHBOARD = UnicornDashboard(
        UnicornDashboardSettings(
            device_settings = UnicornSettings(
                address = args.address,
                n_samp = args.n_samp
            )
        )
    )

    APP.panels = { 'unicorn_device': DASHBOARD.app }

    ez.run(
        app = APP,
        dashboard = DASHBOARD,
    )

if __name__ == '__main__':
    dashboard()