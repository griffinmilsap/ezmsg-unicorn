import asyncio
import typing

import ezmsg.core as ez
import panel as pn

from param.parameterized import Event
from ezmsg.unicorn.device import UnicornDeviceSettings

class UnicornDashboardState(ez.State):

    start_bt_button: pn.widgets.Button
    device_select: pn.widgets.Select
    address: pn.widgets.TextInput
    connect_button: pn.widgets.Button
    disconnect_button: pn.widgets.Button

    settings_queue: asyncio.Queue[UnicornDeviceSettings]


class UnicornDashboard(ez.Unit):
    STATE: UnicornDashboardState

    OUTPUT_SETTINGS = ez.OutputStream(UnicornDeviceSettings)

    def initialize(self) -> None:
        
        self.STATE.start_bt_button = pn.widgets.Button(name="Start Bluetooth Scan", button_type="primary", disabled=False, icon="bluetooth",icon_size='1.35em')        
        
        self.STATE.device_select = pn.widgets.Select(name="Select Device", options=[], value=None, size=5)
        self.STATE.address = pn.widgets.TextInput(name='Device Address', placeholder="XX:XX:XX:XX:XX:XX")
        self.STATE.connect_button = pn.widgets.Button(name="Connect", button_type="success", disabled=False)
        self.STATE.disconnect_button = pn.widgets.Button(name="Disconnect", button_type="danger", disabled=False)
       
        # self.STATE.start_bt_button.on_click(self.findDevicesStart)        

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
            self.STATE.address.value = value.new
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
                self.STATE.start_bt_button,
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

        process = await asyncio.create_subprocess_shell(
            f'bluetoothctl scan on',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            while True:
                data = await process.stdout.readline()

                if not data: 
                    break

                data = data.decode('ascii').rstrip()
                ez.logger.info(f"bluetoothctl: {data}")
                options.append(data)

            exit_code = await process.wait()

            if exit_code:
                err = await process.stderr.read()
                err = err.decode('ascii').rstrip()
                ez.logger.info(f'Bluetooth discovery failure -- {err}')
        
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
    

    # Scan Off
    async def scan_off(self):
        await self.enable_bluetooth()
        await self.bluetoothctl_command("scan off")


    def findDevicesStop(self, event):
        async def nearby_devices():

            await self.scan_off()        
            device_list = await self.bluetoothctl_command("devices")
            
            # mac address as both name and address
            lines = device_list.split("\n")
            self.STATE.device_dict = {}
            for line in lines:
                if "Device" in line:
                    parts = line.split()
                    
                    if len(parts) >= 2:
                        deviceAddress = parts[1]
                        deviceName = parts[2]
                        if "UN-" in deviceName:
                            self.STATE.device_dict[deviceName] = deviceAddress

                            self.STATE.device_select.options = list(self.STATE.device_dict.keys())
                            self.STATE.device_select.value = None


        asyncio.create_task(nearby_devices())

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

    APP.panels = { 'Unicorn': DASHBOARD.panel }

    ez.run(
        app = APP,
        dashboard = DASHBOARD,
        device = DEVICE,
        connections = (
            (DASHBOARD.OUTPUT_SETTINGS, DEVICE.INPUT_SETTINGS),
        )
    )
