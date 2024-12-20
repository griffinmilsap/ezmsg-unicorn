import asyncio
import typing

from dataclasses import field, replace

import ezmsg.core as ez
import panel as pn

from param.parameterized import Event

from .device import UnicornSettings


class UnicornDiscoveryState(ez.State):

    device_select: pn.widgets.Select
    scan_button: pn.widgets.Button
    scan_progress: pn.indicators.Progress
    address: pn.widgets.TextInput
    connect_button: pn.widgets.Button
    disconnect_button: pn.widgets.Button

    settings_queue: asyncio.Queue[UnicornSettings]

    options: typing.List[str] = field(default_factory=list)
    addresses: typing.Dict[str, str] = field(default_factory = dict)


class UnicornDiscoverySettings(ez.Settings):
    default_settings: UnicornSettings


class UnicornDiscovery(ez.Unit):
    """ A bluetooth discovery pane that uses `bluetoothctl` under the hood 
    (so it only works on Linux with bluez)
    """
    SETTINGS = UnicornDiscoverySettings
    STATE = UnicornDiscoveryState

    OUTPUT_SETTINGS = ez.OutputStream(UnicornSettings)

    def initialize(self) -> None:
        
        self.STATE.device_select = pn.widgets.Select(name="Nearby Devices", options=self.STATE.options, value=None, size=5)
        self.STATE.scan_button = pn.widgets.Button(name="Bluetooth Scan", button_type='primary', sizing_mode='stretch_width')
        self.STATE.scan_progress = pn.indicators.Progress(value = 0, max = 100, sizing_mode='stretch_width')
        self.STATE.address = pn.widgets.TextInput(name='Device Address', placeholder="XX:XX:XX:XX:XX:XX")
        self.STATE.connect_button = pn.widgets.Button(name="Connect", button_type="success", disabled=False, sizing_mode='stretch_width')
        self.STATE.disconnect_button = pn.widgets.Button(name="Disconnect", button_type="danger", disabled=False, sizing_mode='stretch_width')
       
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

        async def scan(value: Event):
            scan_time = 30.0 # sec
            poll_time = 0.2 # sec

            self.STATE.scan_button.disabled = True

            max_itrs = int(scan_time / poll_time)
            self.STATE.scan_progress.max = max_itrs - 1

            try:
                discovery = asyncio.create_task(self.discover_devices())
                for itr in range(max_itrs):
                    await asyncio.sleep(poll_time)
                    self.STATE.scan_progress.value = itr
                discovery.cancel()
                await discovery
            finally:
                self.STATE.scan_button.disabled = False
                self.STATE.device_select.options = list(self.STATE.addresses.keys())

        self.STATE.scan_button.on_click(scan) # type: ignore

        async def on_select(value: Event):
            self.STATE.address.value = self.STATE.addresses.get(value.new, '')
        self.STATE.device_select.param.watch(on_select, 'value')

        self.STATE.settings_queue = asyncio.Queue()

        # Add simulator to the list
        self.STATE.addresses['simulator'] = 'simulator'
        self.STATE.options.append('simulator')

    @ez.publisher(OUTPUT_SETTINGS)
    async def pub_settings(self) -> typing.AsyncGenerator:
        while True:
            settings = await self.STATE.settings_queue.get()
            yield self.OUTPUT_SETTINGS, settings
        
    def controls(self) -> pn.viewable.Viewable:
        return pn.Card(
            self.STATE.device_select,
            self.STATE.scan_button,
            self.STATE.scan_progress,
            self.STATE.address,
            pn.Row(
                self.STATE.connect_button,
                self.STATE.disconnect_button,
            ),
            title = "Unicorn Device Discovery",
            sizing_mode = 'stretch_width'
        )
    
    async def discover_devices(self) -> None:

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
                    if 'UN-' in name:
                        entry = f'{name} ({addr})'
                        self.STATE.addresses[entry] = addr
                        self.STATE.device_select.options = list(self.STATE.addresses.keys())

            exit_code = await process.wait()

            if exit_code:
                err = await process.stderr.read()
                err = err.decode('ascii').rstrip()
                ez.logger.info(f'bluetooth discovery failure -- {err}')
        
        finally:
            process.stdin.write('scan off\nexit\n'.encode())
            await process.stdin.drain()
            await process.wait()