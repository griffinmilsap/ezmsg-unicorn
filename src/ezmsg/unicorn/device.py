
import typing
import socket

import ezmsg.core as ez

from .connection import UnicornConnection, UnicornConnectionSettings
from .native import NativeUnicornConnection

# Python libraries that handle Bluetooth Classic RFCOMM connections
# are in short supply these days (2024)..  
# Bleak is great and modern but only handles bluetooth low-energy (BLE)
# PyBluez is not maintained and wheels are not compiled for modern MacOS (ARM)
# Python has native RFCOMM support, but the Python that ships with MacOS and Windows
# is not currently compiled with bluetooth support...
# I hate to depend on a library as large as Qt for one silly module to handle
# RFCOMM bluetooth communication, but this is where we are with such a dated technology.
# @gtec - If you're reading this, please consider a BLE firmware upgrade

Unicorn = UnicornConnection # Only Simulator
UnicornSettings = UnicornConnectionSettings

if hasattr(socket, 'AF_BLUETOOTH'):
    Unicorn = NativeUnicornConnection
else:
    ez.logger.info(f'ezmsg-unicorn: Python was built without native RFCOMM support')
    try:
        from .qt import QtUnicornConnection
        Unicorn = QtUnicornConnection
    except ImportError:
        ez.logger.error(f'ezmsg-unicorn: Install PyQt5 for third-party RFCOMM support')
                        
if __name__ == '__main__':

    import argparse

    from ezmsg.util.debuglog import DebugLog

    parser = argparse.ArgumentParser(
        description = 'Unicorn Device'
    )

    device_group = parser.add_argument_group('device')

    device_group.add_argument(
        '-a', '--address',
        type = str,
        help = 'bluetooth address of Unicorn to stream data from (XX:XX:XX:XX:XX:XX)',
    )

    device_group.add_argument(
        '--n_samp',
        type = int,
        help = 'number of data frames per message',
        default = 50
    )

    class Args:
        address: typing.Optional[str]
        n_samp: int

    args = parser.parse_args(namespace = Args)

    if args.address in (None, ''):
        ez.logger.error('no device to connect to; exiting.')
        
    else:
        DEVICE = Unicorn(
            UnicornSettings(
                args.address,
                args.n_samp
            )
        )

        LOG = DebugLog()
        
        ez.run(
            device = DEVICE,
            log = LOG,
            connections=(
                (DEVICE.OUTPUT_SIGNAL, LOG.INPUT),
                (DEVICE.OUTPUT_BATTERY, LOG.INPUT),
                (DEVICE.OUTPUT_GYROSCOPE, LOG.INPUT),
                (DEVICE.OUTPUT_ACCELEROMETER, LOG.INPUT)
            )
        )
