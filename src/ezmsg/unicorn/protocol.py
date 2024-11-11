import typing
import numpy as np
import numpy.typing as npt

class UnicornProtocol:
    """ Bluetooth Unicorn protocol
    https://github.com/unicorn-bi/Unicorn-Suite-Hybrid-Black/blob/master/Unicorn%20Bluetooth%20Protocol/UnicornBluetoothProtocol.pdf
    From the bottom of my heart, thank you g.tec.  This is how you support a device :)
    """
    PORT = 1
    FS = 250.0
    PAYLOAD_LENGTH = 45
    
    EEG_CHANNELS_COUNT = 8
    BYTES_PER_EEG_CHANNEL = 3 # 24 bit signed EEG
    ACC_CHANNELS_COUNT = 3 # X, Y, Z
    BYTES_PER_ACC_CHANNEL = 2 # 16 bit signed
    GYR_CHANNELS_COUNT = 3 # YAW, PITCH, ROLL
    BYTES_PER_GYR_CHANNEL = 2 # 16 bit signed

    HEADER_OFFSET = 0
    HEADER_LENGTH = 2
    
    BATTERY_OFFSET = HEADER_OFFSET + HEADER_LENGTH
    BATTERY_LENGTH = 1
    
    EEG_OFFSET = BATTERY_OFFSET + BATTERY_LENGTH
    EEG_LENGTH = EEG_CHANNELS_COUNT * BYTES_PER_EEG_CHANNEL
    
    ACC_OFFSET = EEG_OFFSET + EEG_LENGTH
    ACC_LENGTH = ACC_CHANNELS_COUNT * BYTES_PER_ACC_CHANNEL

    GYR_OFFSET = ACC_OFFSET + ACC_LENGTH
    GYR_LENGTH = GYR_CHANNELS_COUNT * BYTES_PER_GYR_CHANNEL
    
    COUNT_OFFSET = GYR_OFFSET + GYR_LENGTH
    COUNT_LENGTH = 4
    
    FOOTER_OFFSET = COUNT_OFFSET + COUNT_LENGTH
    FOOTER_LENGTH = 2

    assert PAYLOAD_LENGTH == FOOTER_OFFSET + FOOTER_LENGTH
    
    START_MSG = b'\x61\x7C\x87'
    STOP_MSG = b"\x63\x5C\xC5"

    EEG_SCALE = (4500000.0 / 50331642.0) # uV / ADC Units
    ACC_SCALE = (1.0 / 4096.0) # g / ADC Units
    GYR_SCALE = (1.0 / 32.8) # deg/sec / ADC Units

    MOTION_SCALE = np.array([ACC_SCALE] * ACC_CHANNELS_COUNT + [GYR_SCALE] * GYR_CHANNELS_COUNT)

    n_samp: int
    data_bytes: npt.NDArray

    def __init__(self, data: bytes):
        assert (len(data) % UnicornProtocol.PAYLOAD_LENGTH) == 0
        self.n_samp = int(len(data) / UnicornProtocol.PAYLOAD_LENGTH)
        self.data_bytes = np.frombuffer(data, dtype = np.uint8) \
            .reshape(self.n_samp, UnicornProtocol.PAYLOAD_LENGTH)

    def eeg(self, adc_units: bool = False) -> npt.NDArray:
        """ Decode EEG data
        Returns (time x 8 ch) decoded EEG data.
        If adc_units == True, returns raw ADC data, otherwise returns data in uV (default)
        """
        eeg_bytes = self.data_bytes[:, UnicornProtocol.EEG_OFFSET:UnicornProtocol.ACC_OFFSET]
        eeg_bytes = eeg_bytes.reshape(self.n_samp, UnicornProtocol.EEG_CHANNELS_COUNT, UnicornProtocol.BYTES_PER_EEG_CHANNEL)
        sext = ((eeg_bytes[:, :, 0] > 0x80) * 0xFF).astype(np.uint8)[..., np.newaxis] # sign extension
        eeg_bytes = np.concatenate([eeg_bytes[..., ::-1], sext], axis = -1)
        eeg_data = np.frombuffer(eeg_bytes.data, dtype = np.int32) \
            .reshape(self.n_samp, UnicornProtocol.EEG_CHANNELS_COUNT)
        return eeg_data if adc_units else eeg_data * UnicornProtocol.EEG_SCALE

    def motion(self, adc_units: bool = False) -> npt.NDArray:
        """ Decode motion data
        Returns (accelerometer (time x 3 ch), gyroscope (time x 3 ch)) decoded motion data.
        Its just much faster to decode both of these at the same time rather than split
        accelerometer and gyroscope into separate decoding functions
        If adc_units == True, returns raw ADC data, otherwise returns data in (g, deg/sec) respectively
        """
        n_ch = UnicornProtocol.ACC_CHANNELS_COUNT + UnicornProtocol.GYR_CHANNELS_COUNT
        motion_bytes = self.data_bytes[:, UnicornProtocol.ACC_OFFSET:UnicornProtocol.COUNT_OFFSET]
        motion_data = np.frombuffer(motion_bytes.copy().data, dtype = np.int16).reshape(self.n_samp, n_ch) 
        return motion_data if adc_units else motion_data * UnicornProtocol.MOTION_SCALE

    def packet_count(self) -> npt.NDArray:
        """ Decode packet count
        This is a monotonically increasing number representing the 
        current packet number (so that one could check for dropped packets)
        """
        count_bytes = self.data_bytes[:, UnicornProtocol.COUNT_OFFSET:UnicornProtocol.FOOTER_OFFSET]
        count = np.frombuffer(count_bytes.copy().data, dtype = np.int32)
        return count

    def battery(self, adc_units: bool = False) -> npt.NDArray:
        """ Decode battery status
        If adc_units == True, returns raw 4-bit ADC data, otherwise returns a percentage
        """
        battery_bytes = self.data_bytes[:, UnicornProtocol.BATTERY_OFFSET:UnicornProtocol.EEG_OFFSET] & 0x0F
        return battery_bytes if adc_units else battery_bytes / 15
