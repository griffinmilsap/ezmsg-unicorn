
import typing
import socket

import ezmsg.core as ez

from .connection import UnicornConnectionSettings
from .native import NativeUnicornConnection

Unicorn = NativeUnicornConnection
if not hasattr(socket, 'AF_BLUETOOTH'):
    from .qt import QtUnicornConnection
    Unicorn = QtUnicornConnection
UnicornSettings = UnicornConnectionSettings
                        
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
