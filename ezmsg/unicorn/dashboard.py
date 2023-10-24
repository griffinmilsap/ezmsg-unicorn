import asyncio
import typing

from dataclasses import field, replace

import ezmsg.core as ez
from ezmsg.core.component import Component
import panel as pn

from param.parameterized import Event
from ezmsg.util.messages.axisarray import AxisArray
from ezmsg.unicorn.device import UnicornDevice, UnicornDeviceSettings, _UNICORN_FS, _UNICORN_EEG_CHANNELS_COUNT
from ezmsg.sigproc.synth import EEGSynth, EEGSynthSettings
from ezmsg.panel.timeseriesplot import TimeSeriesPlot, TimeSeriesPlotSettings


class UnicornDiscoveryState(ez.State):

    device_select: pn.widgets.Select
    address: pn.widgets.TextInput
    connect_button: pn.widgets.Button
    disconnect_button: pn.widgets.Button

    settings_queue: asyncio.Queue[UnicornDeviceSettings]
    addresses: typing.Dict[str, str] = field(default_factory = dict)


class UnicornDiscoverySettings(ez.Settings):
    default_settings: UnicornDeviceSettings


class UnicornDiscovery(ez.Unit):
    SETTINGS: UnicornDiscoverySettings
    STATE: UnicornDiscoveryState

    OUTPUT_SETTINGS = ez.OutputStream(UnicornDeviceSettings)

    def initialize(self) -> None:
        
        self.STATE.device_select = pn.widgets.Select(name="Nearby Devices", options=[], value=None, size=5)
        self.STATE.address = pn.widgets.TextInput(name='Device Address', placeholder="XX:XX:XX:XX:XX:XX")
        self.STATE.connect_button = pn.widgets.Button(name="Connect", button_type="success", disabled=False)
        self.STATE.disconnect_button = pn.widgets.Button(name="Disconnect", button_type="danger", disabled=False)
       
        self.STATE.connect_button.on_click(lambda _: 
            self.STATE.settings_queue.put_nowait( replace(
                self.SETTINGS.default_settings, 
                address = self.STATE.address.value
            ))
        )

        self.STATE.disconnect_button.on_click(lambda _:
            self.STATE.settings_queue.put_nowait( replace(
                self.SETTINGS.default_settings,
                address = None
            ))                
        )

        async def on_select(value: Event):
            self.STATE.address.value = self.STATE.addresses.get(value.new, '')
        self.STATE.device_select.param.watch(on_select, 'value')

        self.STATE.settings_queue = asyncio.Queue()

    @ez.publisher(OUTPUT_SETTINGS)
    async def pub_settings(self) -> typing.AsyncGenerator:
        while True:
            settings = await self.STATE.settings_queue.get()
            yield self.OUTPUT_SETTINGS, settings
        
    def panel(self) -> pn.viewable.Viewable:
        return pn.Column(
            "__Unicorn Device Discovery__",
            self.STATE.device_select,
            self.STATE.address,
            pn.Row(
                self.STATE.connect_button,
                self.STATE.disconnect_button,
            )
        )
    
    async def discover_devices(self) -> None:

        options: typing.List[str] = []
        self.STATE.device_select.options = options
        self.STATE.addresses.clear()

        # Add simulator to the list
        self.STATE.addresses['simulator'] = 'simulator'
        options.append('simulator')

        process = await asyncio.create_subprocess_exec(
            '/usr/bin/bluetoothctl',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if process.stdin is None or process.stdout is None or process.stderr is None:
            ez.logger.warning('failed to discover devices: could not open pipes to bluetoothctl')
            return

        try:
            ez.logger.info('starting bluetooth discovery')
            process.stdin.write('scan on\n'.encode())
            await process.stdin.drain()

            while True:
                data = await process.stdout.readline()

                if not data: 
                    break

                data = data.decode('ascii').rstrip()

                tokens = data.split(' ')
                _, tag, ty = tokens[:3]

                if 'NEW' in tag and ty == 'Device':
                    addr = tokens[3]
                    name = ' '.join(tokens[4:])
                    # if 'UN-' in name:
                    entry = f'{name} ({addr})'
                    options.append(entry)
                    self.STATE.addresses[entry] = addr

                    # force refresh currently-loaded dashboards
                    self.STATE.device_select.options = options

            exit_code = await process.wait()

            if exit_code:
                err = await process.stderr.read()
                err = err.decode('ascii').rstrip()
                ez.logger.info(f'bluetooth discovery failure -- {err}')
        
        finally:
            process.stdin.write('scan off\nexit\n'.encode())
            await process.stdin.drain()
            await process.wait()


class UnicornSimulatorSwitchState(ez.State):
    signal_queue: asyncio.Queue[AxisArray] = field(default_factory=asyncio.Queue)
    output_simulator: bool = False


class UnicornSimulatorSwitch(ez.Unit):

    STATE: UnicornSimulatorSwitchState

    INPUT_SETTINGS = ez.InputStream(UnicornDeviceSettings)
    INPUT_SYNTH_SIGNAL = ez.InputStream(AxisArray)
    INPUT_DEVICE_SIGNAL = ez.InputStream(AxisArray)
    OUTPUT_SIGNAL = ez.OutputStream(AxisArray)

    @ez.subscriber(INPUT_SETTINGS)
    async def on_settings(self, msg: UnicornDeviceSettings) -> None:
        self.STATE.output_simulator = msg.address == 'simulator'

    @ez.subscriber(INPUT_DEVICE_SIGNAL)
    async def on_device_signal(self, msg: AxisArray) -> None:
        if not self.STATE.output_simulator:
            self.STATE.signal_queue.put_nowait(msg)

    @ez.subscriber(INPUT_SYNTH_SIGNAL)
    async def on_synth_signal(self, msg: AxisArray) -> None:
        if self.STATE.output_simulator:
            self.STATE.signal_queue.put_nowait(msg)

    @ez.publisher(OUTPUT_SIGNAL)
    async def pub_signal(self) -> typing.AsyncGenerator:
        while True:
            signal = await self.STATE.signal_queue.get()
            yield self.OUTPUT_SIGNAL, signal


class UnicornDashboardSettings(ez.Settings):
    device_settings: UnicornDeviceSettings


class UnicornDashboard(ez.Collection):

    SETTINGS: UnicornDashboardSettings

    OUTPUT_SIGNAL = ez.OutputStream(AxisArray)

    PLOT = TimeSeriesPlot(TimeSeriesPlotSettings(name = ''))

    SIMSWITCH = UnicornSimulatorSwitch()
    SYNTH = EEGSynth()

    DISCOVERY = UnicornDiscovery()
    DEVICE = UnicornDevice()

    def configure(self) -> None:
        self.DEVICE.apply_settings(self.SETTINGS.device_settings)
        self.DISCOVERY.apply_settings(
            UnicornDiscoverySettings(
                default_settings = self.SETTINGS.device_settings
            )
        )

        self.SYNTH.apply_settings(
            EEGSynthSettings(
                fs = _UNICORN_FS, 
                n_ch= _UNICORN_EEG_CHANNELS_COUNT, 
                n_time = self.SETTINGS.device_settings.n_samp
            )
        )

    def panel(self) -> pn.viewable.Viewable:
        return pn.Row(
            self.DISCOVERY.panel(),
            self.PLOT.panel()
        )

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.DISCOVERY.OUTPUT_SETTINGS, self.DEVICE.INPUT_SETTINGS),
            (self.DISCOVERY.OUTPUT_SETTINGS, self.SIMSWITCH.INPUT_SETTINGS),
            (self.SYNTH.OUTPUT_SIGNAL, self.SIMSWITCH.INPUT_SYNTH_SIGNAL),
            (self.DEVICE.OUTPUT_SIGNAL, self.SIMSWITCH.INPUT_DEVICE_SIGNAL),
            (self.SIMSWITCH.OUTPUT_SIGNAL, self.PLOT.INPUT_SIGNAL),
            (self.SIMSWITCH.OUTPUT_SIGNAL, self.OUTPUT_SIGNAL)
        )
    
    def process_components(self) -> typing.Collection[Component]:
        return (self.DEVICE, self.SYNTH)

if __name__ == '__main__':
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
        '--address',
        type = str,
        help = 'bluetooth address of Unicorn to autoconnect to (XX:XX:XX:XX:XX:XX)',
        default = None
    )

    device_group.add_argument(
        '--n_samp',
        type = int,
        help = 'number of data frames per message',
        default = 50
    )

    class Args:
        port: int
        address: typing.Optional[str]
        n_samp: int

    args = parser.parse_args(namespace = Args)

    APP = Application(ApplicationSettings(port = args.port))
    DASHBOARD = UnicornDashboard(
        UnicornDashboardSettings(
            device_settings = UnicornDeviceSettings(
                address = args.address,
                n_samp = args.n_samp
            )
        )
    )

    APP.panels = { 'unicorn': DASHBOARD.panel }

    ez.run(
        app = APP,
        dashboard = DASHBOARD,
    )
