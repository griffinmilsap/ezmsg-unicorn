import numpy as np

import pytest

from ezmsg.unicorn.protocol import UnicornProtocol

def test_protocol() -> None:
    # See protocol document section 1.5: Payload Conversion Example
    # https://github.com/unicorn-bi/Unicorn-Suite-Hybrid-Black/blob/master/Unicorn%20Bluetooth%20Protocol/UnicornBluetoothProtocol.pdf

    example = bytes([
        0xC0, 0x00, 0x0F, 0x00, 0x9F, 0xAF, 0x00, 0x9F,
        0xD4, 0x00, 0xA0, 0x40, 0x00, 0x9F, 0x43, 0x00, 
        0x9F, 0x9A, 0x00, 0x9F, 0xE3, 0x00, 0x9F, 0x85, 
        0x00, 0x9F, 0xBB, 0x2E, 0xF6, 0xE9, 0x02, 0x8D, 
        0xF2, 0xF3, 0xFF, 0xEF, 0xFF, 0x23, 0x00, 0xB0, 
        0x00, 0x00, 0x00, 0x0D, 0x0A
    ])
    
    expected_eeg = np.array([[3654.87, 3658.18, 3667.83, 3645.21, 3652.99, 3659.52, 3651.11, 3655.94]]) # uV
    expected_acc = np.array([[-0.614,  0.182, -0.841]]) # g
    expected_gyr = np.array([[-0.396, -0.518,  1.067]]) # deg/s
    expected_packet_count = np.array([[176]])
    expected_battery = np.array([[1.0]])
    
    proto = UnicornProtocol(example)
    acc, gyr = proto.motion()

    assert np.allclose(np.round(proto.eeg(), decimals = 2), expected_eeg)
    assert np.allclose(np.round(acc, decimals = 3), expected_acc)
    assert np.allclose(np.round(gyr, decimals = 3), expected_gyr)
    assert np.allclose(proto.packet_count(), expected_packet_count)
    assert np.allclose(proto.battery(), expected_battery)


if __name__ == '__main__':
    test_protocol()