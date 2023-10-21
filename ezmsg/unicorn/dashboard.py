import asyncio
import typing

from dataclasses import field

import ezmsg.core as ez
import panel as pn

from param.parameterized import Event
from ezmsg.unicorn.device import UnicornDeviceSettings

class UnicornDashboardState(ez.State):

    device_select: pn.widgets.Select
    address: pn.widgets.TextInput
    connect_button: pn.widgets.Button
    disconnect_button: pn.widgets.Button

    settings_queue: asyncio.Queue[UnicornDeviceSettings]
    addresses: typing.Dict[str, str] = field(default_factory = dict)


class UnicornDashboard(ez.Unit):
    STATE: UnicornDashboardState

    OUTPUT_SETTINGS = ez.OutputStream(UnicornDeviceSettings)

    def initialize(self) -> None:
        
        self.STATE.device_select = pn.widgets.Select(name="Select Device", options=[], value=None, size=5)
        self.STATE.address = pn.widgets.TextInput(name='Device Address', placeholder="XX:XX:XX:XX:XX:XX")
        self.STATE.connect_button = pn.widgets.Button(name="Connect", button_type="success", disabled=False)
        self.STATE.disconnect_button = pn.widgets.Button(name="Disconnect", button_type="danger", disabled=False)
       

        self.STATE.connect_button.on_click(lambda _: 
            self.STATE.settings_queue.put_nowait(
                UnicornDeviceSettings(
                    addr = self.STATE.address.value
                )
            )
        )

        self.STATE.disconnect_button.on_click(lambda _:
            self.STATE.settings_queue.put_nowait(
                UnicornDeviceSettings()
            )                                 
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
        return pn.Row(
            pn.Column(
                '# Unicorn Device Dashboard',
                self.STATE.device_select,
                self.STATE.address,
                pn.Row(
                    self.STATE.connect_button,
                    self.STATE.disconnect_button,
                )
            )
        )
    
    @ez.task
    async def discover_devices(self) -> None:

        options: typing.List[str] = []
        self.STATE.device_select.options = options

        self.STATE.addresses.clear()

        process = await asyncio.create_subprocess_exec(
            '/usr/bin/bluetoothctl',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

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
                    entry = f'{name} ({addr})'
                    options.append(entry)
                    self.STATE.addresses[entry] = addr

            exit_code = await process.wait()

            if exit_code:
                err = await process.stderr.read()
                err = err.decode('ascii').rstrip()
                ez.logger.info(f'bluetooth discovery failure -- {err}')
        
        finally:
            process.stdin.write('scan off\nexit\n'.encode())
            await process.stdin.drain()
            await process.wait()


if __name__ == '__main__':
    import argparse
    from ezmsg.panel.application import Application, ApplicationSettings
    from ezmsg.unicorn.device import UnicornDevice

    parser = argparse.ArgumentParser(
        description = 'Unicorn Dashboard'
    )

    parser.add_argument(
        '--port',
        type = int,
        help = 'Port to host visualization on. [0 = Any port]',
        default = 0
    )

    class Args:
        port: int

    args = parser.parse_args(namespace = Args)

    APP = Application(ApplicationSettings(port = args.port))
    DASHBOARD = UnicornDashboard()
    DEVICE = UnicornDevice()

    APP.panels = { 'unicorn': DASHBOARD.panel }

    ez.run(
        app = APP,
        dashboard = DASHBOARD,
        device = DEVICE,
        connections = (
            (DASHBOARD.OUTPUT_SETTINGS, DEVICE.INPUT_SETTINGS),
        )
    )
