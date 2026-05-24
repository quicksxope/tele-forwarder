import asyncio
import json
from paths import SOCKET_PATH

DEFAULT_SOCKET = str(SOCKET_PATH)


async def call(method: str, socket_path: str = DEFAULT_SOCKET, **kwargs) -> dict:
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        raise ConnectionRefusedError('Daemon is not running — start forwarder.py first')
    try:
        payload = json.dumps({'method': method, **kwargs}) + '\n'
        writer.write(payload.encode())
        await writer.drain()
        response = await reader.readline()
        return json.loads(response)
    finally:
        writer.close()
        await writer.wait_closed()
