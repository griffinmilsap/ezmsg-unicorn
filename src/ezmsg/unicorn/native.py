
import asyncio
import socket

import ezmsg.core as ez

from .protocol import UnicornProtocol
from .connection import UnicornConnection
    

class NativeUnicornConnection(UnicornConnection):

    @ez.task
    async def handle_device(self) -> None:
        while True:
            await self.STATE.reconnect_event.wait()
            self.STATE.reconnect_event.clear()

            if self.STATE.cur_settings.address in (None, '', 'simulator'):
                continue

            while True: # Reconnection Loop; keep trying to connect on device disconnect
                try:
                    # We choose to do this instead of using pybluez so that we can interact
                    # with RFCOMM using non-blocking async calls.  Currently, this is only
                    # supported on linux with python built with bluetooth support.
                    # NOTE: sock.connect is blocking and could take a long time to return...
                    #     - this unit should probably live in its own process because of this...
                    ez.logger.debug(f"opening RFCOMM connection on {self.STATE.cur_settings.address}")
                    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, proto = socket.BTPROTO_RFCOMM) # type: ignore
                    sock.connect((self.STATE.cur_settings.address, UnicornProtocol.PORT))
                    reader, writer = await asyncio.open_connection(sock = sock)

                except Exception as e:
                    ez.logger.debug(f'could not open RFCOMM connection to {self.STATE.cur_settings.address}: {e}')
                    if self.STATE.reconnect_event.is_set():
                        break

                    else:
                        await asyncio.sleep(60.0) # TODO: Consider making this a setting.. reconnect_delay?
                        continue

                read_length = UnicornProtocol.PAYLOAD_LENGTH * self.STATE.cur_settings.n_samp

                try:
                    # 1. Send "Start Acquisition" command
                    ez.logger.debug(f"starting stream")
                    writer.write(UnicornProtocol.START_MSG)
                    await writer.drain()
                    
                    # 2. Receive "Start Acquisition" acknowledge message
                    response = await reader.readexactly(len(UnicornProtocol.START_MSG)) # 0x00 0x00 0x00

                    while True: # Acquisition loop; continue to get data while connected

                        if self.STATE.reconnect_event.is_set():
                            break # Out of acquisition loop

                        try:
                            # 3. Receive Payload
                            block = await reader.readexactly(read_length)
                            self.STATE.incoming.put_nowait(block)

                        except TimeoutError:
                            ez.logger.warning('timeout on unicorn connection. disconnected.')
                            break

                finally: 
                    # No matter what happens during acquisition, 
                    # try to shut down bluetooth connection gracefully
                    if not writer.is_closing():
                        ez.logger.debug(f"stopping stream")
                        # 6. Send "Stop Acquisition" command
                        writer.write(UnicornProtocol.STOP_MSG)
                        await writer.drain()
                        # 7. Receive "Stop Acquisition" acknowledge message
                        response = await reader.readexactly(len(UnicornProtocol.STOP_MSG)) # 0x00 0x00 0x00
                        writer.close()
                        await writer.wait_closed()

                if self.STATE.reconnect_event.is_set():
                    break # Out of reconnection loop
