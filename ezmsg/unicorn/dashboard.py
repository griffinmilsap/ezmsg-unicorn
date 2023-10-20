import asyncio
import typing

import ezmsg.core as ez
import panel as pn

from ezmsg.unicorn.device import UnicornDeviceSettings

class UnicornDashboardState(ez.State):

    device_select: pn.widgets.Select
    stop_bt_button: pn.widgets.Button
    connect_button: pn.widgets.Button
    start_bt_button: pn.widgets.Button
    disconnect_button: pn.widgets.Button
    address: pn.widgets.TextInput
    status_indicator: pn.widgets.TextInput
    bt_status_indicator: pn.widgets.TextInput

    settings_queue: asyncio.Queue[UnicornDeviceSettings]


class UnicornDashboard(ez.Unit):
    STATE: UnicornDashboardState

    OUTPUT_SETTINGS = ez.OutputStream(UnicornDeviceSettings)

    def initialize(self) -> None:

        self.STATE.bt_status_indicator = pn.widgets.TextInput(name="Find Devices", value="Search for Devices", disabled=True) 
        
        self.STATE.start_bt_button = pn.widgets.Button(name="Start Bluetooth Scan", button_type="primary", disabled=False, icon="bluetooth",icon_size='1.35em')        
        self.STATE.stop_bt_button = pn.widgets.Button(name="Unicorn Devices", button_type="primary", button_style='outline', disabled=False, icon="brain", icon_size='1.35em')
        
        self.STATE.device_select = pn.widgets.Select(name="Select Device", option=[], value=None)
        self.STATE.status_indicator = pn.widgets.TextInput(name="Connection Status", value="Disconnected", disabled=True)
        self.STATE.connect_button = pn.widgets.Button(name="Connect", button_type="success", disabled=False)
        self.STATE.disconnect_button = pn.widgets.Button(name="Disconnect", button_type="danger", disabled=False)

        self.STATE.address = pn.widgets.TextInput(name = 'Device Address', placeholder="XX:XX:XX:XX:XX:XX")
       
        self.STATE.start_bt_button.on_click(self.findDevicesStart)        
        self.STATE.stop_bt_button.on_click(self.findDevicesStop)

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
                self.STATE.bt_status_indicator,
                self.STATE.start_bt_button,
                self.STATE.stop_bt_button,
                self.STATE.device_select,
                self.STATE.status_indicator,
                self.STATE.address,
                pn.Row(
                    self.STATE.connect_button,
                    self.STATE.disconnect_button,
                )
            )
        )
    
    # bluetoothctl commands and desctiptions
    # https://www.linux-magazine.com/Issues/2017/197/Command-Line-bluetoothctl
    async def bluetoothctl_command(self, command):
        process = await asyncio.create_subprocess_shell(
            f'bluetoothctl {command}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        return stdout.decode().strip()

    async def enable_bluetooth(self):
        
        print("Enabling Bluetooth...")
        await self.bluetoothctl_command("power on")
        print("Bluetooth enabled.")

    async def make_discoverable(self):
        
        print("Making device discoverable...")
        await self.bluetoothctl_command("discoverable on")
        print("There are discoverable devices.")

    async def remove_devices(self):
        
        # List devices in the Bluetoothctl cache
        devices_list = await self.bluetoothctl_command("devices")

        # Split the output by line and extract MAC addresses
        devices_lines = devices_list.split('\n')
        devices_lines.clear()

        lines = devices_list.split("\n")
        output_list = []
        for line in lines:
            if "Device" in line:
                parts = line.split()
                if len(parts) >= 2:
                    deviceAddress = parts[2]
                    output_list.append(deviceAddress)
        output_list.clear()
    
    def findDevicesStart(self, event):
        async def scan(scan_duration=10):
            
            print("Starting scan for nearby devices...")
            self.STATE.bt_status_indicator.value = "Starting scan for nearby devices ...."
            await self.enable_bluetooth()
            await self.remove_devices()
            await self.bluetoothctl_command("scan on")

        asyncio.create_task(scan())

    # Scan Off
    async def scan_off(self):
        await self.enable_bluetooth()
        await self.bluetoothctl_command("scan off")
        print("Scan complete")


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

        
                    if len(deviceName) > 0:
                        self.STATE.status_indicator.value = "Select wanted device."
                        self.STATE.bt_status_indicator.value = "Found {} device nearby".format(len(self.STATE.device_dict))

                    else:                    
                        self.STATE.status_indicator.value = "No devices were located. Start Bluetooth Scan"
                        self.STATE.bt_status_indicator.value = "Found {} device nearby".format(len(self.STATE.device_dict))

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
