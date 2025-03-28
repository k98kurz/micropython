from binascii import crc32
from collections import namedtuple, deque
from hashlib import sha256
from machine import unique_id, reset
from math import ceil
from random import randint
from struct import pack, unpack
from time import time, time_ns
import asyncio
import micropython

try:
    from typing import Callable
except ImportError:
    ...

try:
    from types import GeneratorType
except ImportError:
    GeneratorType = type((lambda: (yield))())

try:
    from machine import lightsleep # type: ignore
except ImportError:
    from time import sleep
    def lightsleep(ms):
        sleep(ms/1000)

if hasattr(asyncio, 'coroutines'):
    def iscoroutine(c):
        return asyncio.coroutines.iscoroutine(c)
else:
    def iscoroutine(c):
        return isinstance(c, GeneratorType)

if hasattr(asyncio, 'sleep_ms'):
    sleep_ms = asyncio.sleep_ms
else:
    sleep_ms = lambda ms: asyncio.sleep(ms/1000)


VERSION = micropython.const('0.1.0-dev')
PROTOCOL_VERSION = micropython.const(0)
DEBUG = True
MODEM_SLEEP_MS = micropython.const(90)
MODEM_WAKE_MS = micropython.const(40)
MODEM_INTERSECT_INTERVAL = micropython.const(int(0.9 * MODEM_WAKE_MS))
MODEM_INTERSECT_RTX_TIMES = micropython.const(
    int((MODEM_SLEEP_MS+MODEM_WAKE_MS)/MODEM_INTERSECT_INTERVAL) + 1
)
SEQ_SYNC_DELAY_MS = micropython.const(10_000)
SEND_RETRY_DELAY_MS = micropython.const(2_000)
SEND_RETRY_COUNT = micropython.const(3)
dTree = micropython.const(0)
dCPL = micropython.const(1)

def time_ms():
    return int(time_ns()/1_000_000)

def debug(*args):
    if DEBUG:
        print(*args)

def trace(cls_or_fn, prefix: str = ''):
    if type(cls_or_fn) is type:
        class Wrapped(cls_or_fn):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                for name in dir(self):
                    if len(name) >= 2 and name[:2] == '__':
                        continue
                    if type(getattr(self, name)) is type:
                        continue
                    if callable(getattr(self, name)):
                        setattr(self, name, trace(getattr(self, name), f'{cls_or_fn.__name__}.'))
        for name in dir(cls_or_fn):
            if len(name) >= 2 and name[:2] == '__':
                continue
            if type(getattr(cls_or_fn, name)) is type:
                continue
            if callable(getattr(cls_or_fn, name)):
                setattr(cls_or_fn, name, trace(getattr(cls_or_fn, name), f'{cls_or_fn.__name__}.'))
        try:
            if hasattr(Wrapped, '__name__') and hasattr(cls_or_fn, '__name__'):
                setattr(Wrapped, '__name__', getattr(cls_or_fn, '__name__'))
        except:
            ...
        try:
            if hasattr(Wrapped, '__module__') and hasattr(cls_or_fn, '__module__'):
                setattr(Wrapped, '__module__', getattr(cls_or_fn, '__module__'))
        except:
            ...
        return Wrapped
    elif callable(cls_or_fn):
        def wrap(*args, **kwargs):
            if DEBUG:
                print(f'{prefix}{cls_or_fn.__name__}: {args=} {kwargs=}')
            return cls_or_fn(*args, **kwargs)
        return wrap
    else:
        return cls_or_fn

def clear(d: deque|list):
    while len(d) > 0:
        d.pop()

def enum(**enums):
    """Enum workaround for micropython. CC BY-SA 4.0
        https://stackoverflow.com/a/1695250
    """
    return type('Enum', (), enums)


Field = namedtuple("Field", ["name", "length", "type", "max_length"])


# @micropython.native
class Flags:
    error: bool
    throttle: bool
    ask: bool
    ack: bool
    rtx: bool
    rns: bool
    nia: bool
    reserved1: bool
    reserved2: bool
    mode: bool

    def __init__(self, state: int|bytes) -> None:
        self.state = state if type(state) is int else int.from_bytes(state, 'big')

    @property
    def error(self) -> bool:
        return bool(self.state & 0b10000000)
    @error.setter
    def error(self, val: bool):
        if val:
            self.state |= 0b10000000
        else:
            self.state &= 0b01111111

    @property
    def throttle(self) -> bool:
        return bool(self.state & 0b01000000)
    @throttle.setter
    def throttle(self, val: bool):
        if val:
            self.state |= 0b01000000
        else:
            self.state &= 0b10111111

    @property
    def _bit2(self) -> bool:
        return bool(self.state & 0b00100000)
    @_bit2.setter
    def _bit2(self, val):
        if val:
            self.state |= 0b00100000
        else:
            self.state &= 0b11011111

    @property
    def _bit3(self) -> bool:
        return bool(self.state & 0b00010000)
    @_bit3.setter
    def _bit3(self, val):
        if val:
            self.state |= 0b00010000
        else:
            self.state &= 0b11101111

    @property
    def _bit4(self) -> bool:
        return bool(self.state & 0b00001000)
    @_bit4.setter
    def _bit4(self, val):
        if val:
            self.state |= 0b00001000
        else:
            self.state &= 0b11110111

    @property
    def ask(self) -> bool:
        return not self._bit2 and not self._bit3 and self._bit4
    @ask.setter
    def ask(self, val: bool):
        if not val:
            return
        self._bit2 = False
        self._bit3 = False
        self._bit4 = val

    @property
    def ack(self) -> bool:
        return not self._bit2 and self._bit3 and not self._bit4
    @ack.setter
    def ack(self, val: bool):
        if not val:
            return
        self._bit2 = False
        self._bit3 = val
        self._bit4 = False

    @property
    def rtx(self) -> bool:
        return not self._bit2 and self._bit3 and self._bit4
    @rtx.setter
    def rtx(self, val: bool):
        if not val:
            return
        self._bit2 = False
        self._bit3 = val
        self._bit4 = val

    @property
    def rns(self) -> bool:
        return self._bit2 and not self._bit3 and not self._bit4
    @rns.setter
    def rns(self, val: bool):
        if not val:
            return
        self._bit2 = val
        self._bit3 = False
        self._bit4 = False

    @property
    def nia(self) -> bool:
        return self._bit2 and not self._bit3 and self._bit4
    @nia.setter
    def nia(self, val: bool):
        if not val:
            return
        self._bit2 = val
        self._bit3 = False
        self._bit4 = val

    @property
    def encoded6(self) -> bool:
        return self._bit2 and self._bit3 and not self._bit4
    @encoded6.setter
    def encoded6(self, val: bool):
        if not val:
            return
        self._bit2 = True
        self._bit3 = True
        self._bit4 = False

    @property
    def encoded7(self) -> bool:
        return self._bit2 and self._bit3 and self._bit4
    @encoded7.setter
    def encoded7(self, val: bool):
        if not val:
            return
        self._bit2 = True
        self._bit3 = True
        self._bit4 = True

    @property
    def reserved1(self) -> bool:
        return bool(self.state & 0b00000100)
    @reserved1.setter
    def reserved1(self, val: bool):
        if val:
            self.state |= 0b00000100
        else:
            self.state &= 0b11111011

    @property
    def reserved2(self) -> bool:
        return bool(self.state & 0b00000010)
    @reserved2.setter
    def reserved2(self, val: bool):
        if val:
            self.state |= 0b00000010
        else:
            self.state &= 0b11111101

    @property
    def mode(self) -> bool:
        return bool(self.state & 0b00000001)
    @mode.setter
    def mode(self, val: bool):
        if val:
            self.state |= 0b00000001
        else:
            self.state &= 0b11111110

    def __int__(self) -> int:
        return self.state

    def __repr__(self) -> str:
        return f'Flags(error={self.error}, throttle={self.throttle}, ' +\
            f'ask={self.ask}, ack={self.ack}, rtx={self.rtx}, ' +\
            f'rns={self.rns}, nia={self.nia}, encoded6={self.encoded6}, ' +\
            f'reserved1={self.reserved1}, reserved2={self.reserved2}, mode={self.mode})'

    def __eq__(self, other: 'Flags') -> bool:
        return self.state == other.state


# @micropython.native
class Schema:
    """Describes a packet schema."""
    version: int
    reserved: int = 0
    id: int
    fields: list[Field]
    max_body: int
    max_seq: int

    def __init__(self, version: int, id: int, fields: list[Field]) -> None:
        self.version = version
        self.id = id
        # variable length field can only be last
        assert all([field.length > 0 for field in fields[:-1]])
        self.fields = fields
        self.max_body = [f.max_length for f in self.fields if f.name == 'body'][0]
        max_seq = [f.length for f in self.fields if f.name == 'seq_size']
        self.max_seq = 2**(max_seq[0]*8) if max_seq else 1

    def unpack(self, packet: bytes) -> dict[str, int|bytes|Flags]:
        """Parses the packet into its fields."""
        # uniform header elements
        version, reserved, id, flags, packet = unpack(f'!BBBB{len(packet)-4}s', packet)
        flags = Flags(flags)

        # varying header elements and body
        format_str = '!'
        size = 0
        for field in self.fields:
            if field.type is int:
                format_str += 'B' if field.length == 1 else ('H' if field.length == 2 else 'I')
            elif field.type is bytes:
                format_str += f'{field.length}s' if field.length else f'{len(packet)-size}s'
            size += field.length
        parts = unpack(format_str, packet)
        names = [field.name for field in self.fields]
        result = {
            'version': version,
            'reserved': reserved,
            'id': id,
            'flags': flags,
        }
        for name, value in zip(names, parts):
            result[name] = value
        return result

    def pack(self, flags: Flags, fields: dict[str, int|bytes,]) -> bytes:
        """Packs the packet fields into bytes."""
        # uniform header elements
        format_str = '!BBBB'
        parts = [self.version, self.reserved, self.id, int(flags)]

        # varying header elements and body
        for field in self.fields:
            val = fields[field.name]
            if type(val) is bytes:
                if field.max_length:
                    assert len(val) <= field.max_length, f'{field.name}: {val} too large'
                else:
                    assert len(val) == field.length, f'{field.name}: {val} invalid length'
            if type(val) is int:
                val = val.to_bytes(field.length, 'big')
            parts.append(bytes(val))

            if field.max_length:
                format_str += f'{len(val)}s'
            else:
                format_str += f'{field.length}s'
        return pack(format_str, *parts)

    @property
    def max_blob(self) -> int:
        """Returns the max blob size the Schema can support transmitting."""
        return self.max_seq * self.max_body

# @micropython.native
def get_schema(id: int) -> Schema:
    """Get the Schema definition with the given id."""
    if id == 0:
        # ESP-NOW; 245 B max Package size
        return Schema(0, 0, [
            Field('packet_id', 1, int, 0),
            Field('body', 0, bytes, 245),
        ])
    if id == 1:
        # ESP-NOW; 241 B max Package size
        return Schema(0, 1, [
            Field('packet_id', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('body', 0, bytes, 241),
        ])
    if id == 2:
        # ESP-NOW; 256 max sequence size; 60.75 KiB max Package size
        return Schema(0, 2, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('body', 0, bytes, 243),
        ])
    if id == 3:
        # ESP-NOW; 256 max sequence size; 59.75 KiB max Package size
        return Schema(0, 3, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('body', 0, bytes, 239),
        ])
    if id == 4:
        # ESP-NOW; 65536 max sequence size; 14.8125 MiB max Package size
        return Schema(0, 4, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('body', 0, bytes, 237),
        ])
    if id == 5:
        # ESP-NOW; 211 B max Package size
        return Schema(0, 5, [
            Field('packet_id', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 211),
        ])
    if id == 6:
        # ESP-NOW; 207 B max Package size
        return Schema(0, 6, [
            Field('packet_id', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 207),
        ])
    if id == 7:
        # ESP-NOW; 256 max sequence size; 52.75 KiB max Package size
        return Schema(0, 7, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 209),
        ])
    if id == 8:
        # ESP-NOW; 256 max sequence size; 51.25 KiB max Package size
        return Schema(0, 8, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 205),
        ])
    if id == 9:
        # ESP-NOW; 65536 max sequence size; 12.9375 MiB max Package size
        return Schema(0, 9, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('ttl', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 207),
        ])
    if id == 10:
        # ESP-NOW; 65536 max sequence size; 12.6875 MiB max Package size
        return Schema(0, 10, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('ttl', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 203),
        ])
    if id == 11:
        # ESP-NOW; one-hop relayable; 216 max Package size.
        return Schema(0, 11, [
            Field('packet_id', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 216),
        ])
    if id == 12:
        # ESP-NOW; one-hop relayable; 256 max sequence size; 53.5 KiB max Package size.
        return Schema(0, 12, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 214),
        ])
    if id == 13:
        # ESP-NOW; one-hop relayable; 65536 max sequence size; 13.25 MiB max Package size.
        return Schema(0, 13, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 212),
        ])
    if id == 20:
        # RYLR-998; 235 B max Package size
        return Schema(0, 20, [
            Field('packet_id', 1, int, 0),
            Field('body', 0, bytes, 235),
        ])
    if id == 21:
        # RYLR-998; 231 B max Package size
        return Schema(0, 21, [
            Field('packet_id', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('body', 0, bytes, 231),
        ])
    if id == 22:
        # RYLR-998; 256 max sequence size; 53.25 KiB max Package size
        return Schema(0, 22, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('body', 0, bytes, 233),
        ])
    if id == 23:
        # RYLR-998; 256 max sequence size; 57.25 KiB max Package size
        return Schema(0, 23, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('body', 0, bytes, 229),
        ])
    if id == 24:
        # RYLR-998; 65536 max sequence size; 14.1875 MiB max Package size
        return Schema(0, 24, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('body', 0, bytes, 227),
        ])
    if id == 25:
        # RYLR-998; 201 B max Package size
        return Schema(0, 25, [
            Field('packet_id', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 201),
        ])
    if id == 26:
        # RYLR-998; 197 B max Package size
        return Schema(0, 26, [
            Field('packet_id', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 197),
        ])
    if id == 27:
        # RYLR-998; 256 max sequence size; 49.75 KiB max Package size
        return Schema(0, 27, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 199),
        ])
    if id == 28:
        # RYLR-998; 256 max sequence size; 48.75 KiB max Package size
        return Schema(0, 28, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('ttl', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 195),
        ])
    if id == 29:
        # RYLR-998; 65536 max sequence size; 12.3125 MiB max Package size
        return Schema(0, 29, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('ttl', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 197),
        ])
    if id == 30:
        # RYLR-998; 65536 max sequence size; 12.0625 MiB max Package size
        return Schema(0, 30, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('ttl', 1, int, 0),
            Field('checksum', 4, bytes, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 193),
        ])
    if id == 31:
        # LYLR-998; one-hop relayable; 206 max Package size.
        return Schema(0, 31, [
            Field('packet_id', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 206),
        ])
    if id == 32:
        # LYLR-998; one-hop relayable; 256 max sequence size; 51 KiB max Package size.
        return Schema(0, 32, [
            Field('packet_id', 1, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 1, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 204),
        ])
    if id == 33:
        # LYLR-998; one-hop relayable; 65536 max sequence size; 12.625 MiB max Package size.
        return Schema(0, 33, [
            Field('packet_id', 2, int, 0),
            Field('seq_id', 1, int, 0),
            Field('seq_size', 2, int, 0),
            Field('tree_state', 1, int, 0),
            Field('to_addr', 16, bytes, 0),
            Field('from_addr', 16, bytes, 0),
            Field('body', 0, bytes, 202),
        ])
    raise ValueError(f'Unsupported schema id: {id}')

# @micropython.native
def get_schemas(ids: list[int]) -> list[Schema]:
    """Get a list of Schema definitions with the given ids."""
    return [get_schema(i) for i in ids]

# @micropython.native
def schema_supports_sequence(schema: Schema) -> bool:
    """Determine if a Schema supports sequencing."""
    return len([True for field in schema.fields if field.name == 'packet_id']) == 1 \
        and len([True for field in schema.fields if field.name == 'seq_id'])  == 1 \
        and len([True for field in schema.fields if field.name == 'seq_size'])  == 1 \
        and len([True for field in schema.fields if field.name == 'body'])  == 1

# @micropython.native
def schema_supports_routing(schema: Schema) -> bool:
    """Determine if a Schema supports multi-hop routing."""
    return len([True for f in schema.fields if f.name == 'ttl']) == 1

# @micropython.native
def schema_has(schema: Schema, field_name: str) -> bool:
    """Determine if a Schema has a specific field."""
    return len([True for f in schema.fields if f.name == field_name]) == 1

# @micropython.native
def schema_lacks(schema: Schema, field_name: str) -> bool:
    """Determine if a Schema lacks a specific field."""
    return len([True for f in schema.fields if f.name == field_name]) == 0


SCHEMA_IDS: list[int] = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
    20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33
]
SCHEMA_IDS_SUPPORT_SEQUENCE: list[int] = [
    i for i in SCHEMA_IDS
    if len([True for f in get_schema(i).fields if f.name == 'seq_size'])
]
SCHEMA_IDS_SUPPORT_ROUTING: list[int] = [
    i for i in SCHEMA_IDS
    if len([True for f in get_schema(i).fields if f.name == 'ttl'])
]
SCHEMA_IDS_SUPPORT_RELAY: list[int] = [
    i for i in SCHEMA_IDS
    if schema_has(get_schema(i), 'to_addr') and schema_lacks(get_schema(i), 'ttl')
]
SCHEMA_IDS_SUPPORT_CHECKSUM: list[int] = [
    i for i in SCHEMA_IDS
    if len([True for f in get_schema(i).fields if f.name == 'checksum'])
]


# @micropython.native
class Packet:
    schema: Schema
    id: int
    flags: Flags
    body: bytes|bytearray|memoryview
    fields: dict[str, int|bytes|bytearray]

    def __init__(self, schema: Schema, flags: Flags,
                 fields: dict[str, int|bytes|bytearray]) -> None:
        self.schema = schema
        self.flags = flags
        self.fields = fields

    @classmethod
    def unpack(cls, data: bytes|bytearray) -> 'Packet':
        version, reserved, schema_id, flags, _ = unpack(f'!BBBB{len(data)-4}s', data)
        assert version <= PROTOCOL_VERSION, 'unsupported version encountered'
        schema = get_schema(schema_id)
        fields = schema.unpack(data)
        return cls(schema, Flags(flags), fields)

    def pack(self) -> bytes|bytearray:
        return self.schema.pack(self.flags, self.fields)

    @property
    def id(self) -> int:
        return self.fields['packet_id']

    @id.setter
    def id(self, data: int):
        self.fields['packet_id'] = data

    @property
    def body(self) -> bytes|bytearray|memoryview:
        return self.fields.get('body', b'')

    @body.setter
    def body(self, data: bytes|bytearray|memoryview):
        self.fields['body'] = data

    def set_checksum(self):
        """Set the checksum field to the crc32 of the body. Raises
            AssertionError if the Schema supports checksums.
        """
        assert len([True for f in self.schema.fields if f.name == 'checksum']), \
            f'Schema(id={self.schema.id}) does not support setting the checksum'
        self.fields['checksum'] = crc32(self.body).to_bytes(4, 'big')

    def __repr__(self) -> str:
        return f'Packet(schema.id={self.schema.id}, id={self.id}, ' + \
            f'flags={self.flags}, body={self.body.hex()})'


# @micropython.native
class Sequence:
    schema: Schema
    id: int
    data: bytearray|memoryview
    data_size: int|None
    seq_size: int # equal to actual seq_size-1; i.e. seq_size=0 means 1 packet
    max_body: int
    fields: dict[str, int|bytes|bytearray|memoryview|Flags]
    packets: set[int]
    tx_intrfcs_tried: set[str]

    def __init__(self, schema: Schema, id: int, data_size: int = None,
                 seq_size: int = None) -> None:
        """Initialize the Sequence. Raises AssertionError for data_size
            or seq_size that cannot be supported by the Schema, or if
            the Schema does not support sequencing.
        """
        assert schema_supports_sequence(schema), \
            'schema must include packet_id, seq_id, seq_size, and body to make a Sequence'
        assert 0 <= id < 256, 'sequence id must be between 0 and 255'
        assert data_size is None or 0 <= data_size, 'data_size cannot be negative'
        self.max_body = [f for f in schema.fields if f.name == 'body'][0].max_length
        assert data_size is None or data_size < 2**([
            field for field in schema.fields
            if field.name == 'seq_size'
        ][0].length*8)*self.max_body, f'data_size {data_size} too large for schema(id={schema.id})'
        assert seq_size is None or seq_size < 2**([
            field for field in schema.fields
            if field.name == 'seq_size'
        ][0].length*8), f'seq_size too large for schema(id={schema.id})'
        self.schema = schema
        self.id = id
        self.data_size = data_size
        self.data = bytearray(data_size) if data_size else bytearray(seq_size * self.max_body)
        self.packets = set()
        self.seq_size = ceil(data_size/self.max_body) if data_size else seq_size or 0
        self.fields = {}
        self.tx_intrfcs_tried = set()

    def set_data(self, data: bytes|bytearray|memoryview) -> None:
        """Sets the data for the sequence. Raises AssertionError if it
            is too large to be supported by the Schema.
        """
        size = len(data)
        max_seq_size = 2**([
            f for f in self.schema.fields
            if f.name == 'seq_size'
        ][0].length*8)
        assert size <= max_seq_size * self.max_body, \
            f'data is too large to fit into sequence of schema(id={self.schema.id})'
        if size != len(self.data):
            # copy the data into a fresh buffer
            self.data = bytearray(data)
        else:
            # overwrite current buffer
            self.data[:] = data[:]
        self.seq_size = ceil(size/self.max_body)
        self.packets = set([i for i in range(self.seq_size)])

    def get_packet(self, id: int, flags: Flags, fields: dict[str, int|bytes|Flags]) -> Packet|None:
        """Get the packet with the id (index within the sequence).
            Copies the field dict before modifying. If the packet has
            not been seen, return None. If the packet has been seen,
            return the Packet. Packet body will be a memoryview to
            conserve memory, but it is not readonly because micropython
            does not yet support readonly memoryview.
        """
        if id not in self.packets:
            return None

        offset = id * self.max_body
        size = len(self.data)
        bs = self.max_body if offset + self.max_body <= len(self.data) else size - offset
        fields = {
            k:v for k,v in fields.items()
        }
        fields['body'] = memoryview(self.data)[offset:offset+bs]
        fields['packet_id'] = id
        fields['seq_id'] = self.id
        fields['seq_size'] = self.seq_size - 1
        if id in (0, self.seq_size-1, self.seq_size//2):
            flags.ask = True
        return Packet(self.schema, flags, fields)

    def add_packet(self, packet: Packet) -> bool:
        """Adds a packet, writing its body into the data buffer. Returns
            True if all packets in the sequence have been merged in and
            False otherwise.
        """
        self.packets.add(packet.id)
        offset = packet.id * self.max_body
        bs = len(packet.body)
        self.data[offset:offset+bs] = packet.body
        if packet.id == self.seq_size - 1:
            trim = self.max_body - len(packet.body)
            self.data = self.data[:-trim]
        return len(self.packets) == self.seq_size

    def get_missing(self) -> set[int]:
        """Returns a set of IDs of missing packets. Sequence size must
            be set for this to work.
        """
        return set() if self.seq_size is None else set([i for i in range(self.seq_size)]).difference(self.packets)


# @micropython.native
class Package:
    app_id: bytes|bytearray|memoryview
    half_sha256: bytes|bytearray|memoryview
    blob: bytes|bytearray|memoryview|None

    def __init__(self, app_id: bytes|bytearray|memoryview, half_sha256: bytes|bytearray,
                 blob: bytes|bytearray|None) -> None:
        assert type(app_id) in (bytes, bytearray, memoryview) and len(app_id) == 16
        assert type(half_sha256) in (bytes, bytearray, memoryview) and len(half_sha256) == 16
        assert type(blob) in (bytes, bytearray, memoryview) or blob is None
        self.app_id = app_id
        self.half_sha256 = half_sha256
        self.blob = blob

    def verify(self) -> bool:
        return sha256(self.blob).digest()[:16] == self.half_sha256

    @classmethod
    def from_blob(cls, app_id: bytes|bytearray, blob: bytes|bytearray) -> 'Package':
        """Generate a Package using an app_id and a blob."""
        half_sha256 = sha256(blob).digest()[:16]
        return cls(app_id, half_sha256, blob)

    @classmethod
    def from_sequence(cls, seq: Sequence) -> 'Package':
        """Generate a Package using a completed sequence. Raises
            AssertionError if the sequence is missing packets.
        """
        assert len(seq.get_missing()) == 0
        return cls.unpack(seq.data)

    def pack(self) -> bytes:
        """Serialize a Package into bytes."""
        return pack(f'!16s16s{len(self.blob)}s', self.app_id, self.half_sha256, self.blob)

    @classmethod
    def unpack(cls, data: bytes) -> 'Package':
        """Deserialize a Package from bytes."""
        app_id, half_sha256, blob = unpack(f'!16s16s{len(data)-32}s', data)
        return cls(app_id, half_sha256, blob)


# @micropython.native
class Datagram:
    data: bytes
    intrfc_id: bytes|None
    addr: bytes|None

    def __init__(self, data: bytes, intrfc_id: bytes|None = None, addr: bytes|None = None) -> None:
        self.data = data
        self.intrfc_id = intrfc_id
        self.addr = addr

    def __repr__(self) -> str:
        return f'Datagram(data={self.data.hex()}, ' +\
            f'intrfc_id={self.intrfc_id.hex() if self.intrfc_id else None}, ' +\
            f'addr={self.addr.hex() if self.addr else None})'


# @micropython.native
class Interface:
    name: str
    supported_schemas: list[int]
    default_schema: Schema
    bitrate: int
    id: bytes
    inbox: deque[Datagram]
    outbox: deque[Datagram]
    castbox: deque[Datagram]
    receive_func: Callable|None
    receive_func_async: Callable|None
    send_func: Callable|None
    send_func_async: Callable|None
    broadcast_func: Callable|None
    broadcast_func_async: Callable|None
    wake_func: Callable|None
    _hooks: dict[str, Callable]

    def __init__(self, name: str, bitrate: int, configure: Callable,
                 supported_schemas: list[int], receive_func: Callable = None,
                 send_func: Callable = None, broadcast_func: Callable = None,
                 receive_func_async: Callable = None,
                 send_func_async: Callable = None,
                 broadcast_func_async: Callable = None,
                 wake_func: Callable = None) -> None:
        """Initialize an Interface. Note that the 0th item in the
            supported_schemas argument is used as the default Schema ID.
        """
        self.inbox = deque([], 256)
        self.outbox = deque([], 256)
        self.castbox = deque([], 256)
        self.name = name
        self._configure = configure
        self.bitrate = bitrate
        self.supported_schemas = supported_schemas
        self.default_schema = get_schema(supported_schemas[0])
        self.id = sha256(
            name.encode() + bitrate.to_bytes(4, 'big') +
            b''.join([i.to_bytes(1, 'big') for i in supported_schemas])
        ).digest()[:4]
        self.receive_func = receive_func
        self.send_func = send_func
        self.broadcast_func = broadcast_func
        self.receive_func_async = receive_func_async
        self.send_func_async = send_func_async
        self.broadcast_func_async = broadcast_func_async
        self.wake_func = wake_func
        self._hooks = {}

    def __hash__(self) -> int:
        return hash(self.id)

    def configure(self, data: dict) -> None:
        """Call the configure callback, passing self and data."""
        self.call_hook('configure', self, data)
        self._configure(self, data)

    def wake(self) -> None:
        """Wakes the Interface after a modem sleep cycle."""
        self.call_hook('wake', self)
        if callable(self.wake_func):
            self.wake_func(self)

    def receive(self) -> Datagram|None:
        """Returns a datagram if there is one or None."""
        self.call_hook('receive', self)
        return self.inbox.popleft() if len(self.inbox) else None

    def send(self, datagram: Datagram) -> None:
        """Puts a datagram into the outbox."""
        self.call_hook('send', self, datagram)
        self.outbox.append(datagram)

    def broadcast(self, datagram: Datagram) -> None:
        """Puts a datagram into the castbox."""
        self.call_hook('broadcast', datagram)
        self.castbox.append(datagram)

    async def process(self):
        """Process Interface actions."""
        self.call_hook('process')
        if self.receive_func:
            datagram = self.receive_func(self)
            while datagram:
                self.call_hook('process:receive', datagram)
                self.inbox.append(datagram)
                datagram = self.receive_func(self)
        elif self.receive_func_async:
            datagram = await self.receive_func_async(self)
            while datagram:
                self.call_hook('process:receive_async', datagram)
                self.inbox.append(datagram)
                datagram = await self.receive_func_async(self)

        if len(self.outbox):
            datagram = self.outbox.popleft()
            self.call_hook('process:send', datagram)
            if self.send_func:
                self.send_func(datagram)
            elif self.send_func_async:
                await self.send_func_async(datagram)

        if len(self.castbox):
            datagram = self.castbox.popleft()
            self.call_hook('process:broadcast', datagram)
            if self.broadcast_func:
                self.broadcast_func(datagram)
            elif self.broadcast_func_async:
                await self.broadcast_func_async(datagram)

    def validate(self) -> bool:
        """Returns False if the interface does not have all required methods
            and attributes, or if they are not the proper types. Otherwise
            returns True.
        """
        self.call_hook('validate', self)
        if not hasattr(self, 'supported_schemas') or \
            type(self.supported_schemas) is not list or \
            not all([type(i) is int for i in self.supported_schemas]):
            return False
        if not callable(self._configure):
            return False
        if not callable(self.send_func) and not callable(self.send_func_async):
            return False
        if not callable(self.receive_func) and not callable(self.receive_func_async):
            return False
        if not callable(self.broadcast_func) and not callable(self.broadcast_func_async):
            return False
        return True

    def add_hook(self, name: str, hook: Callable):
        self._hooks[name] = hook

    def call_hook(self, name: str, *args, **kwargs):
        if name in self._hooks:
            self._hooks[name](self, *args, **kwargs)


# @micropython.native
class Address:
    tree_state: int
    address: bytes
    coords: list[int,]

    def __init__(
            self, tree_state: int, address: bytes|bytearray|None = None,
            coords: list[int]|None = None
        ) -> None:
        if type(tree_state) is not int:
            raise TypeError("tree_state must be an int")
        if address is coords is None:
            raise ValueError("must provide at least one of address or coords")
        if address is not None and type(address) not in (bytes, bytearray):
            raise TypeError("address must be bytes|bytearray")
        if coords is not None:
            if type(coords) not in (list, tuple):
                raise TypeError("coords must be list[int,] or tuple[int,]")
            if not all([type(i) is int for i in coords]):
                raise TypeError("coords must be list[int,] or tuple[int,]")
        self.tree_state = tree_state
        self.address = bytes(address if address else Address.encode(coords))
        self.coords = coords if coords is not None else Address.decode(address)

    def __hash__(self) -> int:
        return hash((self.tree_state, bytes(self.address)))

    def __eq__(self, other: 'Address') -> bool:
        return hash(self) == hash(other)

    def __str__(self) -> str:
        """User-friendly string representation of the address."""
        addr = list(self.address.hex())
        addr = [addr[i] + addr[i+1] for i in range(0, len(addr), 2)]
        formatted, empty = '', False
        for i in range(len(addr)):
            pair = addr[i]
            if pair != '00':
                formatted += pair
            elif not empty:
                if len(addr) > i + 1 and addr[i+1] != '00':
                    formatted += '00'
                else:
                    formatted += '::'
                    empty = True
            elif formatted[-2:] != '::':
                formatted += pair

        return f'{self.tree_state}-{formatted}'

    def __repr__(self) -> str:
        """String representation of the address for debugging."""
        return f'Address({self})'

    @classmethod
    def from_str(cls, formatted: str) -> 'Address':
        """Reconstruct an Address from a user-friendly string representation."""
        formatted = formatted.replace('Address', '')
        formatted = formatted.replace('(', '')
        formatted = formatted.replace(')', '')
        formatted = formatted.replace(' ', '')
        tree_state, addr = formatted.split('-')
        parts = addr.split('::')
        if len(parts) == 1:
            addr = parts[0] + '0' * (32 - len(parts[0]))
        else:
            prefix, postfix = parts
            addr = prefix + '0' * (32 - len(prefix) - len(postfix)) + postfix
        return cls(int(tree_state), address=bytes.fromhex(addr))

    @staticmethod
    def decode(address: bytes|bytearray) -> list[int,]:
        """Decode an address into a list of coordinates."""
        if len(address) != 16:
            raise ValueError("address must be 16 bytes")
        nibbles = []
        for i in range(len(address)):
            nibbles.append(address[i] >> 4)
            nibbles.append(address[i] & 15)
        nibbles.reverse()

        coordinates = []
        while len(nibbles):
            n = nibbles.pop()
            if n < 8 or len(nibbles) == 0:
                coordinates.append(n)
            else:
                coordinates.append(((n & 7) << 4) + nibbles.pop() + 8)

        # trim final empty coords
        while len(coordinates) and coordinates[-1] == 0:
            coordinates.pop()

        return coordinates

    @staticmethod
    def encode(coordinates: list[int,]) -> bytearray:
        """Encode a list of coordinates into an address."""
        nibbles = []
        for coord in coordinates:
            if coord < 8:
                nibbles.append(coord)
            else:
                # subtract 8 and set the high bit of an octet
                coord = ((coord - 8) & 127) | 128
                # append two nibbles
                nibbles.append(coord >> 4)
                nibbles.append(coord & 15)
        if len(nibbles) % 2:
            nibbles.append(0)
        address = bytearray()
        for i in range(0, len(nibbles), 2):
            address.append((nibbles[i] << 4) | nibbles[i+1])
        while len(address) < 16:
            address.append(0)
        return address[:16]

    @staticmethod
    def cpl(x1: list[int,], x2: list[int,]) -> int:
        """Calculate the common prefix length of two addresses."""
        cpl = 0
        for i in range(min(len(x1), len(x2))):
            if x1[i] != x2[i]:
                break
            cpl += 1
        return cpl

    def dTree_coords(self) -> list[int,]:
        """Return the routable coordinates for tree distance."""
        if 0 in self.coords:
            return self.coords[:self.coords.index(0)]
        return self.coords

    @staticmethod
    def dTree(x1: 'Address', x2: 'Address') -> int:
        """Calculate the tree distance between two addresses."""
        x1 = x1.dTree_coords()
        x2 = x2.dTree_coords()
        return len(x1) + len(x2) - 2 * Address.cpl(x1, x2)

    def dCPL_coords(self) -> list[int,]:
        """Return the routable coordinates for CPL distance."""
        if len(self.coords) == 32:
            return self.coords
        return self.coords + [0] * (32 - len(self.coords))

    @staticmethod
    def dCPL(x1: 'Address', x2: 'Address') -> int:
        """Calculate the CPL distance between two addresses."""
        x1 = x1.dCPL_coords()
        x2 = x2.dCPL_coords()
        if x1 == x2:
            return 0
        return 33 - Address.cpl(x1, x2) - 1 / (len(x1) + len(x2) + 1)


# @micropython.native
class Peer:
    """Class for tracking local peer connectivity info. Peer id should
        be a public key, and interfaces must be a dict mapping MAC
        address bytes to associated Interface.
    """
    id: bytes
    interfaces: list[tuple[bytes, Interface],]
    addrs: deque[Address]
    timeout: int # drop peers that turn off
    throttle: int # congestion control
    last_rx: int # timestamp of last received transmission
    can_tx: bool
    queue: deque[Datagram] # queue of Packets or seq_id to send

    def __init__(self, id: bytes, interfaces: list[tuple[bytes, Interface],]) -> None:
        self.id = id
        self.interfaces = interfaces
        self.addrs = deque([], 2)
        self.timeout = 4
        self.throttle = 0
        self.last_rx = time_ms()
        self.queue = deque([], 10)

    def set_addr(self, addr: Address):
        """Appends the Address to the peer's address deque, first
            removing any old addresses with the same tree state.
        """
        addrs = [self.addrs.popleft() for _ in range(len(self.addrs))]
        for a in addrs:
            if a.tree_state != addr.tree_state:
                self.addrs.append(a)
        self.addrs.append(addr)

    @property
    def can_tx(self) -> bool:
        return self.last_rx + 800 > time_ms()


# @micropython.native
class Node:
    """Class for tracking nodes and the apps they support."""
    id: bytes
    apps: list[bytes]

    def __init__(self, id: bytes, apps: list[bytes] = []) -> None:
        self.id = id
        self.apps = apps


# @micropython.native
class Application:
    name: str
    description: str
    version: int
    id: bytes
    receive_func: Callable
    callbacks: dict[str, Callable]
    params: dict
    _hooks: dict[str, Callable]

    def __init__(
            self, name: str, description: str, version: int,
            receive_func: Callable, callbacks: dict = {}, params: dict = {}
        ) -> None:
        self.name = name
        self.description = description
        self.version = version
        name = name.encode()
        description = description.encode()
        self.id = sha256(pack(
            f'!{len(name)}s{len(description)}sI',
            name,
            description,
            version
        )).digest()[:16]
        self.receive_func = receive_func
        self.callbacks = callbacks
        self.params = params
        self._hooks = {}

    def add_hook(self, name: str, callback: Callable):
        self._hooks[name] = callback

    def receive(self, blob: bytes, intrfc: Interface, mac: bytes):
        """Passes self, blob, intrfc, and mac to the receive_func callback."""
        if 'receive' in self._hooks:
            self._hooks['receive'](self, blob, intrfc, mac)
        self.receive_func(self, blob, intrfc, mac)

    def available(self, name: str|None = None) -> list[str]|bool:
        """If name is passed, returns True if there is a callback with
            that name and False if there is not. Otherwise, return a
            list[str] of callback names.
        """
        return name in self.callbacks if name else [n for n in self.callbacks]

    def invoke(self, name: str, *args, **kwargs):
        """Tries to invoke the named callback, passing self, args, and
            kwargs. Returns None if the callback does not exist or the
            result of the function call. If the callback is async, a
            coroutine will be returned.
        """
        if 'invoke' in self._hooks:
            self._hooks['invoke'](self, name, *args, **kwargs)
        if name in self._hooks:
            self._hooks[name](self, *args, **kwargs)
        return (self.callbacks[name](self, *args, **kwargs)) if name in self.callbacks else None


# @micropython.native
class Event:
    ts: int # in milliseconds
    id: bytes
    handler: Callable
    args: tuple
    kwargs: dict
    def __init__(self, ts: int, id: bytes, handler: Callable,
                 *args, **kwargs) -> None:
        self.ts = ts
        self.id = id
        self.handler = handler
        self.args = args
        self.kwargs = kwargs
    def __repr__(self) -> str:
        return f'Event(ts={self.ts}, id=0x{self.id.hex()}, ' + \
            f'handler={self.handler}, args={self.args}, kwargs={self.kwargs})'


# @micropython.native
class InSequence:
    seq: Sequence
    src: bytes|Address
    retry: int
    intrfc: Interface
    def __init__(self, seq: Sequence, src: bytes|Address, intrfc: Interface) -> None:
        self.seq = seq
        self.src = src
        self.intrfc = intrfc
        self.retry = 3


# @micropython.native
class Cache:
    limit: int
    items: dict[bytes, tuple[int, object]]
    lowest_expiry: int

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.items = {}
        self.lowest_expiry = -1

    def add(self, key: bytes|str|int, value: object, ttl: int = 60):
        self.items.pop(key, None)
        # if we hit the limit, remove the item that has the lowest expiry
        if len(self.items) >= self.limit:
            self.remove_lowest_expiry()
        expiry = time_ms() + ttl * 1000
        self.items[key] = (expiry, value)
        if expiry < self.lowest_expiry or self.lowest_expiry == -1:
            self.lowest_expiry = expiry

    def get(self, key: bytes) -> object|None:
        if key in self.items:
            pair = self.items[key]
            if pair[0] < time_ms():
                self.items.pop(key)
                return None
            return pair[1]
        return None

    def clear(self):
        self.items.clear()
        self.lowest_expiry = -1

    def remove_lowest_expiry(self):
        for key, value in self.items.items():
            if value[0] == self.lowest_expiry:
                self.items.pop(key)
                break
        self.lowest_expiry = min(self.items.values(), key=lambda x: x[0])[0]

    def invalidate_expired(self):
        keys_to_remove = []
        for key, value in self.items.items():
            if value[0] < time_ms():
                keys_to_remove.append(key)
        for key in keys_to_remove:
            self.items.pop(key)
        self.lowest_expiry = min(self.items.values(), key=lambda x: x[0])[0] if self.items else -1


# @micropython.native
class Packager:
    version: str = VERSION
    interfaces: list[Interface] = []
    seq_id: int = 0
    packet_id: int = 0
    seq_cache: Cache = Cache(10)
    packet_cache: Cache = Cache(10)
    in_seqs: dict[int, InSequence] = {}
    peers: dict[bytes, Peer] = {}
    inverse_peers: dict[tuple[bytes, bytes], bytes] = {} # map (mac, intrfc.id): peer_id
    routes: dict[Address, bytes] = {}
    inverse_routes: dict[bytes, deque[Address]] = {}
    banned: list[bytes] = []
    node_id: bytes = b''
    node_addrs: deque[Address] = deque([], 2)
    apps: dict[bytes, Application] = {}
    schedule: dict[bytes, Event] = {}
    new_events: deque[Event] = deque([], 64)
    cancel_events: deque[bytes] = deque([], 64)
    running: bool = False
    sleepskip: deque[bool] = deque([], 10)
    _hooks: dict[str, list[Callable]] = {}

    @classmethod
    def reset(cls):
        cls.interfaces.clear()
        cls.seq_id = 0
        cls.packet_id = 0
        cls.seq_cache.clear()
        cls.packet_cache.clear()
        cls.in_seqs.clear()
        cls.peers.clear()
        cls.inverse_peers.clear()
        cls.routes.clear()
        cls.inverse_routes.clear()
        cls.banned.clear()
        clear(cls.node_addrs)
        cls.apps.clear()
        cls.schedule.clear()
        clear(cls.new_events)
        clear(cls.cancel_events)
        cls.running = False
        clear(cls.sleepskip)
        cls._hooks.clear()

    @classmethod
    def add_hook(cls, name: str, hook: Callable):
        if name not in cls._hooks:
            cls._hooks[name] = []
        if hook not in cls._hooks[name]:
            cls._hooks[name].append(hook)

    @classmethod
    def call_hook(cls, name: str, *args, **kwargs):
        if name in cls._hooks:
            for hook in cls._hooks[name]:
                hook(cls, *args, **kwargs)

    @classmethod
    def remove_hook(cls, name: str, hook: Callable):
        if name in cls._hooks and hook in cls._hooks[name]:
            cls._hooks[name].remove(hook)

    @classmethod
    def clear_hook(cls, name: str):
        if name in cls._hooks:
            del cls._hooks[name]

    @classmethod
    def clear_all_hooks(cls):
        cls._hooks.clear()

    @classmethod
    def add_interface(cls, interface: Interface):
        """Adds an interface. Raises AssertionError if it does not meet
            the requirements for a network interface.
        """
        cls.call_hook('add_interface', interface)
        assert interface.validate()
        cls.interfaces.append(interface)

    @classmethod
    def remove_interface(cls, interface: Interface):
        """Removes a network interface."""
        cls.call_hook('remove_interface', interface)
        cls.interfaces.remove(interface)

    @classmethod
    def add_peer(cls, peer_id: bytes, interfaces: list[tuple[bytes, Interface],]):
        """Adds a peer to the local peer list. Packager will be able to
            send Packages to all such peers.
        """
        cls.call_hook('add_peer', peer_id, interfaces)
        if peer_id in cls.banned:
            return
        if peer_id not in cls.peers:
            cls.peers[peer_id] = Peer(peer_id, interfaces)
        peer = cls.peers[peer_id]
        for mac, intrfc in interfaces:
            if (mac, intrfc) not in peer.interfaces:
                peer.interfaces.append((mac, intrfc))
            if (mac, intrfc.id) not in cls.inverse_peers:
                cls.inverse_peers[(mac, intrfc.id)] = peer_id
        peer.last_rx = time_ms()
        peer.timeout = 4

    @classmethod
    def remove_peer(cls, peer_id: bytes):
        """Removes a peer from the local peer list. Packager will be
            unable to send Packages to this peer.
        """
        cls.call_hook('remove_peer', peer_id)
        if peer_id in cls.peers:
            peer = cls.peers.pop(peer_id)
            for addr in peer.addrs:
                if addr in cls.routes:
                    cls.routes.pop(addr)
            if peer.id in cls.inverse_routes:
                del cls.inverse_routes[peer.id]
            for mac, intrfc in peer.interfaces:
                if (mac, intrfc.id) in cls.inverse_peers:
                    del cls.inverse_peers[(mac, intrfc.id)]

    @classmethod
    def add_route(cls, node_id: bytes, address: Address):
        """Adds an address for a peer. Will also store the previous
            Address for the peer to maintain routability during tree
            state transitions if the new tree state is different
            (maintains only one route per tree state).
        """
        cls.call_hook('add_route', node_id, address)
        if node_id in cls.banned:
            return
        if node_id in cls.peers:
            addrs = cls.peers[node_id].addrs
            if address not in addrs:
                cls.peers[node_id].set_addr(address)
        cls.routes[address] = node_id
        if node_id not in cls.inverse_routes:
            cls.inverse_routes[node_id] = deque([], 2)
        cls.inverse_routes[node_id].append(address)

    @classmethod
    def remove_route(cls, address: Address):
        """Removes the route to the peer with the given address."""
        cls.call_hook('remove_route', address)
        if address not in cls.routes:
            return
        peer_id = cls.routes.pop(address)
        if peer_id in cls.inverse_routes:
            addrs = [
                cls.inverse_routes[peer_id].popleft()
                for _ in range(len(cls.inverse_routes[peer_id]))
            ]
            for a in addrs:
                if a != address:
                    cls.inverse_routes[peer_id].append(a)

    @classmethod
    def ban(cls, node_id: bytes):
        """Bans a node from being a peer or known route."""
        cls.call_hook('ban', node_id)
        cls.banned.append(node_id)
        cls.remove_peer(node_id)

    @classmethod
    def unban(cls, node_id: bytes):
        """Unbans a node from being a peer or known route."""
        cls.call_hook('unban', node_id)
        if node_id in cls.banned:
            cls.banned.remove(node_id)

    @classmethod
    def set_addr(cls, addr: Address):
        """Sets the current tree embedding address for this node,
            preserving the previous address to maintain routability
            between tree state transitions. If the new address shares a
            tree state with a previous address, the previous address
            will be removed prior to adding the new address; otherwise,
            the new address will just be added.
        """
        cls.call_hook('set_addr', addr)
        addrs = [cls.node_addrs.popleft() for _ in range(len(cls.node_addrs))]
        for a in addrs:
            if a.tree_state != addr.tree_state:
                cls.node_addrs.append(a)
        cls.node_addrs.append(addr)

    @classmethod
    def broadcast(cls, app_id: bytes, blob: bytes, interface: Interface|None = None) -> bool:
        """Create a Package from the blob and broadcast it on all
            interfaces that support broadcast. Uses the schema supported
            by all interfaces. Returns False if no schemas could be
            found that are supported by all interfaces.
        """
        cls.sleepskip.append(True)
        # cls.sleepskip.extend([True for _ in range(MODEM_INTERSECT_RTX_TIMES)])
        cls.call_hook('broadcast', app_id, blob, interface)
        sids: set[int] = set()
        schemas: list[Schema] = []
        schema: Schema
        chosen_intrfcs: list[Interface]
        if interface:
            sids = set(interface.supported_schemas)
            schemas = [
                s for s in get_schemas(interface.supported_schemas)
                if s.max_blob >= len(blob) + 32
            ]
            schema = schemas.sort(key=lambda s: s.max_body, reverse=True)[0]
            chosen_intrfcs = [interface]
        else:
            # use only a schema supported by all interfaces
            sids = set(cls.interfaces[0].supported_schemas)
            for interface in cls.interfaces:
                sids.intersection_update(set(interface.supported_schemas))
            schemas = [s for s in get_schemas(list(sids)) if s.max_blob >= len(blob) + 32]
            if len(schemas) == 0:
                return False
            # choose the schema with the largest body size
            schemas.sort(key=lambda s: s.max_body, reverse=True)
            schema = schemas[0]
            chosen_intrfcs = cls.interfaces

        p = Package.from_blob(app_id, blob).pack()
        fl = Flags(0)
        fields = {'body':p, 'packet_id': cls.packet_id, 'seq_id': cls.seq_id, 'seq_size': 1}
        p1 = Packet(schema, fl, fields)
        # try to send as a single packet if possible
        if len(p) <= schema.max_body:
            packets = [p1]
        else:
            sids.intersection_update(SCHEMA_IDS_SUPPORT_SEQUENCE)
            if len(sids) == 0:
                return False
            schemas = [s for s in get_schemas(list(sids)) if s.max_blob >= len(p)]
            if len(schemas) == 0:
                return False
            schemas.sort(key=lambda s: s.max_body, reverse=True)
            schema = schemas[0]
            s = Sequence(schema, cls.seq_id, len(p))
            s.set_data(p)
            packets = [s.get_packet(i, fl, fields) for i in range(s.seq_size)]
            cls.seq_cache.add(cls.seq_id, s)
            cls.seq_id = (cls.seq_id + 1) % 256

        for intrfc in chosen_intrfcs:
            br = intrfc.broadcast
            for p in packets:
                br(Datagram(p.pack(), intrfc.id))
        return True

    @classmethod
    def next_hop(
        cls, to_addr: Address, metric: int = dTree
    ) -> tuple[Peer, Address]|None:
        """Returns the next hop for the given to_addr if one can be
            found for the given tree_state. Returns None if no next
            hop can be found.
        """
        cls.call_hook('next_hop', to_addr, metric)
        if to_addr in cls.routes:
            peer_id = cls.routes[to_addr]
            if peer_id in cls.peers:
                return (cls.peers[peer_id], to_addr)

        # first filter peers by tree_state
        peers: list[tuple[Peer, Address]] = []
        for peer in cls.peers.values():
            for addr in peer.addrs:
                if addr.tree_state == to_addr.tree_state:
                    peers.append((peer, addr))

        # bail; should result in an error response
        if len(peers) == 0:
            return None

        # then sort by appropriate distance metric
        if metric == dCPL:
            peers.sort(key=lambda p: Address.dCPL(p[1], to_addr))
        else:
            peers.sort(key=lambda p: Address.dTree(p[1], to_addr))
        return (peers[0][0], peers[0][1])

    @classmethod
    def send(
        cls, app_id: bytes, blob: bytes, node_id: bytes|None = None,
        to_addr: Address|None = None, schema: int = None, metric: int = dTree,
        retry_count: int = SEND_RETRY_COUNT
    ) -> bool:
        """Attempts to send a Package containing the app_id and blob to
            the specified node. Returns True if it can be sent and False
            if it cannot (i.e. if it is not a known peer and there is
            not a known route to the node).
        """
        cls.call_hook('send', app_id, blob, node_id, to_addr, schema)
        if node_id is None and to_addr is None:
            raise TypeError('at least one of node_id or to_addr is required')
        islocal = node_id in cls.peers
        if not islocal and \
            node_id not in [r for a, r in cls.routes.items()] and \
            to_addr is None and node_id != cls.node_id:
            return False

        p = Package.from_blob(app_id, blob).pack()
        schema: Schema = None
        if islocal:
            peer = cls.peers[node_id]
        else:
            if not to_addr:
                # find the address for the given node_id
                if node_id not in cls.inverse_routes:
                    return False
                for addr in cls.inverse_routes[node_id]:
                    to_addr = addr
                    break
                if not to_addr:
                    return False
            next_hop = cls.next_hop(to_addr, metric)
            if not next_hop:
                return False
            peer = next_hop[0]

        intrfcs = peer.interfaces
        sids = set(intrfcs[0][1].supported_schemas)
        for _, ntrfc in intrfcs:
            sids.intersection_update(set(ntrfc.supported_schemas))
        if not islocal:
            sids.intersection_update(set(SCHEMA_IDS_SUPPORT_ROUTING))
        sids = get_schemas(list(sids))
        sids = [s for s in sids if s.max_blob >= len(p)]
        sids.sort(key=lambda s: s.max_body, reverse=True)
        schema = sids[0]
        intrfcs.sort(key=lambda mi: mi[1].bitrate, reverse=True)
        intrfc = intrfcs[0]
        fields = {'body':p, 'packet_id': cls.packet_id, 'seq_id': cls.seq_id, 'seq_size': 1}
        if not islocal:
            fields = {
                k:v for k,v in fields.items()
            }
            fields['to_addr'] = to_addr.address
            fields['from_addr'] = cls.node_addrs[-1].address
            fields['tree_state'] = to_addr.tree_state
            fields['ttl'] = 255
        if schema.max_blob > schema.max_body:
            seq = Sequence(schema, cls.seq_id, len(p))
            seq.set_data(p)
            for i in range(seq.seq_size):
                cls._send_datagram(Datagram(
                    seq.get_packet(i, Flags(0), fields).pack(),
                    intrfc[1].id,
                    intrfc[0]
                ), peer)
        else:
            fields['body'] = p
            flags = Flags(0)
            flags.ask = True
            p = Packet(schema, flags, fields)
            cls._send_datagram(Datagram(p.pack(), intrfc[1].id, intrfc[0]), peer)
            cls.packet_cache.add(cls.packet_id, p)
            cls.packet_id = (cls.packet_id + 1) % 256
            eid = b'RP' + p.id.to_bytes(1, 'big')
            cls.new_events.append(Event(
                time_ms() + SEND_RETRY_DELAY_MS,
                eid,
                cls.retry_send,
                p.fields['packet_id'],
                retry_count,
                to_addr,
                node_id,
                metric,
            ))

        return True

    @classmethod
    def retry_send(
        cls, pid: int, count: int, to_addr: Address|None = None,
        node_id: bytes|None = None, metric: int = dTree
    ):
        p: Packet|None = cls.packet_cache.get(pid)

        if count <= 0 or p is None:
            return

        if to_addr is None and node_id is None:
            return
        p2 = Package.unpack(p.body)
        cls.send(p2.app_id, p2.blob, node_id, to_addr, p.schema, metric, count-1)

    @classmethod
    def get_interface(
        cls, node_id: bytes|None = None, to_addr: Address|None = None,
        exclude: list[bytes,] = [], metric: int = dTree
    ) -> tuple[bytes|None, Interface|None, Peer|None]:
        """Get the proper Interface and MAC for direct transmission to
            the neighbor with the given node_id or for direct
            transmission to the best candidate for routing toward the
            given to_addr. Returns None if neither node_id nor to_addr
            are passed or if an Interface cannot be found. If exclude is
            passed, the Interfaces for those nodes with ids specified in
            the list will be excluded from consideration.
        """
        cls.call_hook('get_interface', node_id, to_addr, exclude)
        if node_id in cls.peers and node_id not in exclude:
            # direct neighbors
            intrfcs = cls.peers[node_id].interfaces
            intrfcs.sort(key=lambda mi: mi[1].bitrate, reverse=True)
            return (intrfcs[0][0], intrfcs[0][1], cls.peers[node_id])
        elif node_id in cls.inverse_routes:
            # known node reachable via routing; prepare to find next hop
            # set to_addr
            addrs = cls.inverse_routes[node_id]
            nowaddrs = [a for a in addrs if a.tree_state == cls.node_addrs[-1].tree_state]
            if len(nowaddrs):
                to_addr = nowaddrs[0]
            to_addr = addrs[0]

        if to_addr:
            # unknown node; find next hop
            next_hop = cls.next_hop(to_addr, metric)
            if not next_hop:
                return (None, None, None)
            peer = next_hop[0]
            if peer.id in exclude:
                return (None, None, None)
            intrfcs = peer.interfaces
            intrfcs.sort(key=lambda mi: mi[1].bitrate, reverse=True)
            return (intrfcs[0][0], intrfcs[0][1], peer)
        else:
            return (None, None, None)

    @classmethod
    def rns(cls, peer_id: bytes, intrfc_id: bytes,
            retries: int = MODEM_INTERSECT_RTX_TIMES):
        """Send RNS if one has not been sent in the last
            MODEM_INTERSECT_INTERVAL ms, otherwise update the event.
        """
        cls.call_hook('rns', peer_id, intrfc_id, retries)
        eid = b'rns'+peer_id+intrfc_id
        now = time_ms()
        if eid in [e.id for e in cls.new_events]:
            return # do not add a duplicate event

        if peer_id not in cls.peers:
            # dropped peer, so drop attempt to contact
            return

        peer = cls.peers[peer_id]

        if retries < 1:
            # clear queue and drop the attempts
            q = peer.queue
            while len(q):
                q.popleft()
            return

        # queue event and send RNS
        event = Event(
            now + MODEM_INTERSECT_INTERVAL,
            eid,
            cls.rns,
            peer_id,
            intrfc_id,
            retries=retries-1
        )
        cls.queue_event(event)
        flags = Flags(0)
        flags.rns = True
        intrfc = [i for i in cls.interfaces if i.id == intrfc_id][0]
        mac = [
            mac for mac, i in peer.interfaces
            if i.id == intrfc_id
        ][0]
        intrfc.send(Datagram(
            Packet(intrfc.default_schema, flags, {
                'packet_id': cls.packet_id,
                'body': b'',
            }).pack(),
            intrfc_id,
            mac
        ))
        cls.packet_id = (cls.packet_id + 1) % 256

    @classmethod
    def _send_datagram(cls, dgram: Datagram, peer: Peer):
        """Sends a Datagram on the appropriate interface. Raises
            AssertionError if the interface ID is invalid.
        """
        cls.call_hook('_send_datagram', dgram, peer)
        cls.sleepskip.append(True)
        # cls.sleepskip.extend([True for _ in range(MODEM_INTERSECT_RTX_TIMES)])
        assert dgram.intrfc_id in [i.id for i in cls.interfaces]
        intrfc = [i for i in cls.interfaces if i.id == dgram.intrfc_id][0]
        if peer.can_tx:
            intrfc.send(dgram)
        else:
            # queue the datagram and try sending RNS on the interface instead
            peer.queue.append(dgram)
            cls.rns(peer.id, intrfc.id)

    @classmethod
    def send_packet(cls, packet: Packet, node_id: bytes = None) -> bool:
        """Attempts to send a Packet either to a specific node or toward
            the to_addr field (decrement ttl); if flags.error is set,
            send toward the from_addr field (increment ttl). Returns
            False if it cannot be sent.
        """
        cls.call_hook('send_packet', packet, node_id)
        if node_id in cls.peers:
            # direct neighbors
            mac, intrfc, peer = cls.get_interface(node_id)
        elif node_id in cls.inverse_routes:
            # known node reachable via routing
            mac, intrfc, peer = cls.get_interface(node_id)
        elif 'to_addr' in packet.fields and 'from_addr' in packet.fields:
            # this is an intermediate hop
            metric = dCPL if packet.flags.mode else dTree
            to_addr = Address(packet.fields['tree_state'], packet.fields['to_addr'])
            from_addr = Address(packet.fields['tree_state'], packet.fields['from_addr'])
            if 'ttl' not in packet.fields:
                # if the destination is not a peer, and the error flag is not set,
                # set the error flag
                to_in_routes = to_addr in cls.routes
                to_in_peers = to_in_routes and cls.routes[to_addr] in cls.peers
                from_in_routes = from_addr in cls.routes
                from_in_peers = from_in_routes and cls.routes[from_addr] in cls.peers
                if not packet.flags.error and (not to_in_routes or not to_in_peers):
                    packet.flags.error = True
                elif packet.flags.error and (not from_in_routes or not from_in_peers):
                    # error is set and the sender is not a peer, so drop the packet
                    return False
            if packet.flags.error:
                exclude = [cls.routes[to_addr]] if to_addr in cls.routes else []
                mac, intrfc, peer = cls.get_interface(
                    to_addr=from_addr, exclude=exclude, metric=metric
                )
            else:
                exclude = [cls.routes[from_addr]] if from_addr in cls.routes else []
                mac, intrfc, peer = cls.get_interface(
                    to_addr=to_addr, exclude=exclude, metric=metric
                )

            if 'ttl' in packet.fields:
                packet.fields['ttl'] += 1 if packet.flags.error else -1

            if packet.fields.get('ttl', 1) <= 0 and not packet.flags.error:
                # drop the packet
                return False
            if packet.fields.get('ttl', 1) >= 255 and packet.flags.error:
                # drop the packet
                return False
        else:
            return False

        if not mac:
            return False

        cls._send_datagram(Datagram(packet.pack(), intrfc.id, mac), peer)
        return True

    @classmethod
    def sync_sequence(cls, seq_id: int):
        """Requests retransmission of any missing packets."""
        cls.call_hook('sync_sequence', seq_id)
        if seq_id not in cls.in_seqs:
            return
        seq = cls.in_seqs[seq_id]
        if seq.retry <= 0:
            # drop sequence because the originator is not responding to rtx
            cls.in_seqs.pop(seq_id)
            return

        flags = Flags(0)
        flags.rtx = True
        fields = {
            'body': b'',
            'seq_id': seq_id,
            'seq_size': seq.seq.seq_size - 1,
        }

        if isinstance(seq.src, Address):
            tree_state = seq.src.tree_state
            from_addr = [a for a in cls.node_addrs if a.tree_state == tree_state]
            if len(from_addr) == 0:
                # drop sequence because of tree state transition
                cls.in_seqs.pop(seq_id)
                return
            fields['to_addr'] = seq.src.address
            fields['tree_state'] = tree_state
            fields['from_addr'] = from_addr[0]
            seq.src = cls.routes[seq.src]

        for pid in seq.seq.get_missing():
            fields['packet_id'] = pid
            cls.send_packet(Packet(
                seq.seq.schema,
                flags,
                fields
            ), seq.src)

        # decrement retry counter and schedule event
        seq.retry -= 1
        eid = b'SS' + seq.seq.id.to_bytes(2, 'big')
        cls.queue_event(Event(
            time_ms() + SEQ_SYNC_DELAY_MS,
            eid,
            cls.sync_sequence,
            seq_id
        ))

    @classmethod
    def _send_ack(cls, p: Packet, src: bytes|None = None):
        flags = Flags(p.flags.state)
        flags.ask = False
        flags.ack = True
        fields = {
            'packet_id': p.id,
            'body': b'',
        }
        if 'to_addr' in p.fields:
            fields['to_addr'] = p.fields['from_addr']
            fields['from_addr'] = p.fields['to_addr']
            fields['tree_state'] = p.fields['tree_state']
        if 'ttl' in p.fields:
            fields['ttl'] = 255
        if 'seq_id' in p.fields:
            fields['seq_size'] = p.fields['seq_size']
            fields['seq_id'] = p.fields['seq_id']
        cls.send_packet(Packet(
            p.schema,
            flags,
            fields
        ), src)

    @classmethod
    def receive(cls, p: Packet, intrfc: Interface, mac: bytes) -> None:
        """Receives a Packet and determines what to do with it. If it is
            a routable packet, forward to the next hop using send_packet;
            if that fails, set the error flag and transmit backwards
            through the route.
        """
        cls.call_hook('receive', p, intrfc, mac)
        cls.sleepskip.append(True)
        # cls.sleepskip.extend([True for _ in range(MODEM_INTERSECT_RTX_TIMES)])
        if p.schema.version > PROTOCOL_VERSION:
            # drop the packet
            return
        src = b'' # source of Packet
        if 'to_addr' in p.fields:
            addr = Address(p.fields['tree_state'], p.fields['to_addr'])
            if addr not in cls.node_addrs:
                # forward
                cls.send_packet(p)
                return
        for nid, peer in cls.peers.items():
            if mac in (i[0] for i in peer.interfaces if i[1] is intrfc):
                src = nid
                break

        if 'seq_id' in p.fields and not p.flags.rtx:
            # try to reconstitute the sequence
            # first cancel pending sequence synchronization event
            seq_id = p.fields['seq_id']
            eid = b'SS' + seq_id.to_bytes(2, 'big')
            if eid in cls.schedule:
                cls.cancel_events.append(eid)
            if seq_id not in cls.in_seqs:
                cls.in_seqs[seq_id] = InSequence(
                    Sequence(p.schema, seq_id, seq_size=p.fields['seq_size']+1),
                    src,
                    intrfc
                )
            seq = cls.in_seqs[seq_id]
            seq.retry = 3 # reset retries because the originator is reachable
            if seq.seq.add_packet(p):
                cls.deliver(Package.unpack(seq.seq.data), intrfc, mac)
                cls.in_seqs.pop(seq_id)
                cls.cancel_events.append(eid)
            else:
                # schedule sequence sync event
                cls.queue_event(Event(
                    time_ms() + SEQ_SYNC_DELAY_MS,
                    eid,
                    cls.sync_sequence,
                    seq_id
                ))
            if p.flags.ask:
                # send ack
                cls._send_ack(p, src)
            return
        elif 'seq_id' in p.fields and p.flags.rtx:
            # request for retransmission: send packet if the sequence is still in the cache
            seq_id = p.fields['seq_id']
            seq: Sequence|None = cls.seq_cache.get(seq_id)
            if seq is None:
                return
            p = seq.get_packet(p.id, Flags(0))
            if p is None:
                return
            cls.send_packet(p)
            return
        elif p.flags.rtx:
            # request retransmission of a non-sequence packet
            pid = p.fields['packet_id']
            packet = cls.packet_cache.get(pid)
            if packet is None:
                return
            cls.send_packet(packet)
            return
        elif p.flags.nia and len(src):
            # peer responded to RNS: cancel event, update peer.last_rx
            cls.call_hook('receive:nia', p, intrfc, mac)
            peer = cls.peers[src]
            eid = b'rns'+peer.id+intrfc.id
            cls.cancel_events.append(eid)
            peer.last_rx = time_ms()
            return
        elif p.flags.rns and len(src):
            # peer sent RNS: send NIA
            cls.call_hook('receive:rns', p, intrfc, mac)
            peer = cls.peers[src]
            flags = Flags(0)
            flags.nia = True
            intrfc.send(Datagram(
                Packet(intrfc.default_schema, flags, {
                    'packet_id': cls.packet_id,
                    'body': b'',
                }).pack(),
                intrfc.id,
                mac
            ))
            cls.packet_id = (cls.packet_id + 1) % 256
            return
        elif p.flags.ack:
            cls.cancel_events.append(b'RP' + p.id.to_bytes(1, 'big'))
            return

        if p.flags.ask:
            # send ack
            cls._send_ack(p, src)

        # parse and deliver the Package
        cls.deliver(Package.unpack(p.body), intrfc, mac)

    @classmethod
    def deliver(cls, p: Package, i: Interface, mac: bytes) -> bool:
        """Attempt to deliver a Package. Returns False if the Package
            half_sha256 is invalid for the blob, or if the Application
            was not registered, or if the Application's receive method
            errors. Otherwise returns True.
        """
        cls.call_hook('deliver', p, i, mac)
        if p.half_sha256 != sha256(p.blob).digest()[:16] or p.app_id not in cls.apps:
            cls.call_hook('deliver:checksum_failed', p, i, mac)
            return False
        try:
            cls.call_hook('deliver:receive', p, i, mac)
            cls.apps[p.app_id].receive(p.blob, i, mac)
            return True
        except:
            cls.call_hook('deliver:receive_failed', p, i, mac)
            return False

    @classmethod
    def add_application(cls, app: Application):
        """Registers an Application to accept Package delivery."""
        cls.call_hook('add_application', app)
        cls.apps[app.id] = app

    @classmethod
    def remove_appliation(cls, app: Application|bytes):
        """Deregisters an Application to no longer accept Package delivery."""
        cls.call_hook('remove_application', app)
        if isinstance(app, Application):
            app = app.id
        cls.apps.pop(app)

    @classmethod
    def queue_event(cls, event: Event):
        """Queues a new event. On the next call to cls.process(), it
            will be added to the schedule, overwriting any event with
            the same ID.
        """
        cls.call_hook('queue_event', event)
        cls.new_events.append(event)

    @classmethod
    async def process(cls):
        """Process interface actions, then process Packager actions."""
        cls.call_hook('process')
        # schedule new events
        while len(cls.new_events):
            event = cls.new_events.popleft()
            cls.schedule[event.id] = event

        # remove all canceled events from the schedule
        while len(cls.cancel_events):
            eid = cls.cancel_events.popleft()
            if eid in cls.schedule:
                cls.schedule.pop(eid)

        # process interface actions
        tasks = []
        for intrfc in cls.interfaces:
            tasks.append(intrfc.process())
        await asyncio.gather(*tasks)

        # read from interfaces
        for intrfc in cls.interfaces:
            while len(intrfc.inbox):
                dgram = intrfc.inbox.popleft()
                if dgram:
                    try:
                        cls.receive(
                            Packet.unpack(dgram.data),
                            intrfc,
                            dgram.addr
                        )
                    except BaseException:
                        ...

        # handle scheduled events
        ce = []
        cos = []
        now = time_ms()
        for eid, event in cls.schedule.items():
            if now >= event.ts:
                t = event.handler(*event.args, **event.kwargs)
                if iscoroutine(t):
                    cos.append(t)
                ce.append(eid)
        if len(cos):
            await asyncio.gather(cos)

        # remove all processed events from the schedule
        for eid in ce:
            cls.schedule.pop(eid)

        # send queued datagrams for reachable peers
        for _, peer in cls.peers.items():
            if peer.can_tx:
                while len(peer.queue):
                    dgram = peer.queue.popleft()
                    intrfc = [
                        i for _, i in peer.interfaces
                        if i.id == dgram.intrfc_id
                    ][0]
                    intrfc.send(dgram)

    @classmethod
    async def work(cls, interval_ms: int = 1, use_modem_sleep: bool = False,
                   modem_sleep_ms: int = MODEM_SLEEP_MS,
                   modem_active_ms: int = MODEM_WAKE_MS):
        """Runs the process method in a loop. If use_modem_sleep is True,
            lightsleep(modem_sleep_ms) will be called periodically to
            save battery, then the method will continue for at least
            modem_active_ms. If the sleepskip queue is not empty and the
            process is eligible for a sleep cycle, an item will be
            popped off the queue and the cycle will be skipped.
        """
        cls.call_hook('work', interval_ms, use_modem_sleep, modem_sleep_ms, modem_active_ms)
        cls.running = True
        modem_cycle = 0
        ts = time_ms()
        while cls.running:
            await cls.process()
            await sleep_ms(interval_ms)
            if use_modem_sleep:
                modem_cycle = time_ms() - ts
                if modem_cycle > modem_active_ms:
                    modem_cycle = 0
                    if len(cls.sleepskip):
                        cls.call_hook('sleepskip')
                        cls.sleepskip.popleft()
                    else:
                        cls.call_hook('modemsleep')
                        lightsleep(modem_sleep_ms)
                        for intrfc in cls.interfaces:
                            intrfc.wake()
                    ts = time_ms()

    @classmethod
    def stop(cls):
        """Sets cls.running to False for graceful shutdown of worker."""
        cls.call_hook('stop')
        cls.running = False


Packager.node_id = sha256(sha256(unique_id()).digest()).digest()


# Interface for inter-Application communication.
iai_box: deque[Datagram] = deque([], 10)
_iai_config = {}

InterAppInterface = Interface(
    name='InterAppInterface',
    bitrate=1_000_000_000,
    configure=lambda _, d: _iai_config.update(d),
    supported_schemas=SCHEMA_IDS,
    receive_func=lambda _: iai_box.popleft() if len(iai_box) else None,
    send_func=lambda d: iai_box.append(d),
    broadcast_func=lambda d: iai_box.append(d),
)

Packager.add_interface(InterAppInterface)


BeaconMessage = namedtuple("BeaconMessage", ['op', 'peer_id', 'apps'])
seen_bm: deque[BeaconMessage] = deque([], 10)
sent_bm: deque[BeaconMessage] = deque([], 10)
beacon_app_id = b''


def serialize_bm(bmsg: BeaconMessage):
    apps = b''.join([aid for aid in bmsg.apps])
    return bmsg.op + bmsg.peer_id + apps

def deserialize_bm(blob: bytes) -> BeaconMessage:
    op = blob[:1]
    pid = blob[1:33]
    apps = []
    if len(blob) > 33:
        app_ids = blob[33:]
        while len(app_ids) >= 16:
            apps.append(app_ids[:16])
            app_ids = app_ids[16:]
    return BeaconMessage(op, pid, apps)

def receive_bm(app: Application, blob: bytes, intrfc: Interface, mac: bytes):
    bmsg = deserialize_bm(blob)
    seen_bm.append(bmsg)
    node_id = Packager.node_id
    if bmsg.peer_id != node_id:
        Packager.add_peer(bmsg.peer_id, [(mac, intrfc)])

        if bmsg.op == b'\x00':
            # respond
            Beacon.invoke('respond', bmsg.peer_id)

def get_bmsgs(op: bytes):
    # cache values in local scope
    node_id = Packager.node_id
    apps = tuple(Packager.apps.keys())
    index = 0
    bmsgs = []
    while index < len(apps):
        if len(apps[index:]) > 10:
            app_ids = apps[index:index+10]
            index += 10
        else:
            app_ids = apps[index:]
            index = len(apps)
        bmsgs.append(BeaconMessage(
            op,
            node_id,
            app_ids
        ))
    return bmsgs

def send_beacon(pid: bytes):
    bmsgs = get_bmsgs(b'\x00')
    for bm in bmsgs:
        # send the BeaconMessage in a Package
        Packager.send(beacon_app_id, serialize_bm(bm), pid)

def respond_beacon(pid: bytes):
    bmsgs = get_bmsgs(b'\x01')
    for bm in bmsgs:
        # send the BeaconMessage in a Package
        Packager.send(beacon_app_id, serialize_bm(bm), pid)
    sent_bm.extend(bmsgs)

def broadcast_beacon():
    bmsgs = get_bmsgs(b'\x00')
    for bm in bmsgs:
        # broadcast the BeaconMessage in a Package
        Packager.broadcast(beacon_app_id, serialize_bm(bm))
    sent_bm.extend(bmsgs)

def timeout_peers():
    tdc = []
    for pid, peer in Packager.peers.items():
        peer.timeout -= 1
        if peer.timeout <= 0:
            tdc.append(pid)
    for pid in tdc:
        Packager.remove_peer(pid)

def periodic_beacon(count: int):
    """Broadcasts count times with a 30ms delay between."""
    if count <= 0:
        timeout_peers()
        return schedule_beacon()
    Beacon.invoke('broadcast')
    Packager.new_events.append(Event(
        time_ms() + Beacon.params['beacon_period'],
        beacon_app_id,
        periodic_beacon,
        count - 1
    ))

def schedule_beacon():
    """Schedules the periodic_beacon event to begin broadcasting after
        60s.
    """
    if beacon_app_id+b's' in Packager.schedule:
        return
    Packager.new_events.append(Event(
        time_ms() + Beacon.params['beacon_interval'],
        beacon_app_id+b's',
        periodic_beacon,
        Beacon.params['beacon_count']
    ))

def stop_beacon():
    Packager.cancel_events.append(beacon_app_id)
    Packager.cancel_events.append(beacon_app_id+b's')

Beacon = Application(
    name='Beacon',
    description='Dev Beacon App',
    version=0,
    receive_func=receive_bm,
    callbacks={
        'broadcast': lambda _: broadcast_beacon(),
        'send': lambda _, pid: send_beacon(pid),
        'respond': lambda _, pid: respond_beacon(pid),
        'get_bmsgs': lambda _, op: get_bmsgs(op),
        'serialize': lambda _, bm: serialize_bm(bm),
        'deserialize': lambda _, blob: deserialize_bm(blob),
        # 'start': lambda _: periodic_beacon(MODEM_INTERSECT_RTX_TIMES),
        'start': lambda _: periodic_beacon(2),
        'stop': lambda _: stop_beacon(),
        'get_seen': lambda _: seen_bm,
        'get_sent': lambda _: sent_bm,
    },
    params={
        'beacon_interval': 60_000,
        'beacon_period': MODEM_INTERSECT_INTERVAL,
        # 'beacon_count': MODEM_INTERSECT_RTX_TIMES,
        'beacon_count': 1,
    }
)
beacon_app_id = Beacon.id

Packager.add_application(Beacon)


GossipOp = enum(
    REQUEST = 0,
    REQUEST_IDS = 1,
    NOTIFY = 15,
    PUBLISH = 240,
    RESPOND = 254,
    RESPOND_IDS = 255,
)
GossipMessage = namedtuple("GossipMessage", ['op', 'topic_id', 'data'])
# map of topic_id to list of application_ids
subscriptions: dict[bytes, list[bytes]] = {}
# buffer of seen message ids (half_sha256)
seen_gm: deque[bytes] = deque([], 100)
# cache of GossipMessages
message_cache: Cache = Cache(limit=100)
# id of this gossip application
gossip_app_id: bytes = b''

def serialize_gm(gm: GossipMessage):
    return gm.op.to_bytes(1, 'big') + gm.topic_id + gm.data

def deserialize_gm(blob: bytes) -> GossipMessage:
    return GossipMessage(blob[0], blob[1:17], blob[17:])

def receive_gm(app: Application, blob: bytes, intrfc: Interface, mac: bytes):
    gm = deserialize_gm(blob)
    peer_id = Packager.inverse_peers.get((mac, intrfc.id), None)
    if gm.op == GossipOp.REQUEST:
        respond_gossip_request(peer_id or gm.data, gm.topic_id)
    elif gm.op == GossipOp.REQUEST_IDS:
        respond_gossip_ids(peer_id or gm.data, gm.topic_id)
    elif gm.op == GossipOp.NOTIFY:
        if message_cache.get(gm.data) is None and peer_id is not None:
            request_gossip_message(gm.data, peer_id)
    elif gm.op in (GossipOp.PUBLISH, GossipOp.RESPOND):
        deliver_gossip(gm)
    elif gm.op == GossipOp.RESPOND_IDS:
        if len(gm.data) % 16 or peer_id is None:
            # malformed or cannot contact originating node
            return
        ids = []
        for i in range(0, len(gm.data), 16):
            ids.append(gm.data[i:i+16])
        for id in ids:
            if id not in seen_gm:
                request_gossip_message(id, peer_id)

def publish_gossip(topic_id: bytes, data: bytes):
    gm = GossipMessage(GossipOp.PUBLISH, topic_id, data)
    deliver_gossip(gm)

def deliver_gossip(gm: GossipMessage):
    gm_id = sha256(serialize_gm(gm)).digest()[:16]
    if gm_id in seen_gm:
        return
    # add to cache if it is a PUBLISH or RESPOND
    if gm.op in (GossipOp.PUBLISH, GossipOp.RESPOND):
        seen_gm.append(gm_id)
        message_cache.add(gm_id, gm, ttl=300)
    # deliver to subscribed applications
    for app_id in subscriptions.get(gm.topic_id, []):
        app = Packager.apps.get(app_id, None)
        if app is None:
            continue
        app.receive(gm.data, InterAppInterface, gossip_app_id)
    # skip forward/notify if it was a RESPOND and the size is not too large for simple PUBLISH
    if gm.op == GossipOp.RESPOND and len(gm.data) <= 235 - 17 - 32:
        # i.e. it is not a new message; it is a response to a sync request
        return
    # forward or notify
    if len(gm.data) > 235 - 17 - 32:
        notify_gossip(gm.topic_id, gm_id)
    else:
        broadcast_gossip(gm)

def broadcast_gossip(gm: GossipMessage, count: int = 1):
    Packager.broadcast(gossip_app_id, serialize_gm(gm))
    if count <= 0:
        return
    Packager.new_events.append(Event(
        time_ms() + Gossip.params['echo_delay_ms'],
        b'b' + sha256(serialize_gm(gm)).digest()[:16],
        broadcast_gossip,
        gm,
        count - 1,
    ))

def notify_gossip(topic_id: bytes, gm_id: bytes, count: int = 1):
    gm = GossipMessage(GossipOp.NOTIFY, topic_id, gm_id)
    Packager.broadcast(gossip_app_id, serialize_gm(gm))
    if count <= 0:
        return
    Packager.new_events.append(Event(
        time_ms() + Gossip.params['echo_delay_ms'],
        b'n' + sha256(serialize_gm(gm)).digest()[:16],
        notify_gossip,
        topic_id,
        gm_id,
        count - 1,
    ))

def request_gossip_message(message_id: bytes, peer_id: bytes, count: int = 1):
    gm = GossipMessage(GossipOp.REQUEST, message_id, Packager.node_id)
    Packager.send(gossip_app_id, serialize_gm(gm), peer_id)
    if count <= 0:
        return
    Packager.new_events.append(Event(
        time_ms() + Gossip.params['echo_delay_ms'],
        b'q' + sha256(serialize_gm(gm)).digest()[:16],
        request_gossip_message,
        message_id,
        peer_id,
        count - 1,
    ))

def respond_gossip_request(peer_id: bytes, gm_id: bytes, count: int = 1):
    gm: GossipMessage|None = message_cache.get(gm_id)
    if gm is None:
        return
    if len(gm.data) > 235 - 17 - 32:
        # was a request from a notification; do not modify the op
        Packager.send(gossip_app_id, serialize_gm(gm), peer_id)
    else:
        # was a request following message ids; modify the op so it is not forwarded
        new_gm = GossipMessage(GossipOp.RESPOND, gm.topic_id, gm.data)
        Packager.send(gossip_app_id, serialize_gm(new_gm), peer_id)
    if count <= 0:
        return
    Packager.new_events.append(Event(
        time_ms() + Gossip.params['echo_delay_ms'],
        b'r' + sha256(serialize_gm(gm)).digest()[:16],
        respond_gossip_request,
        peer_id, gm_id, count - 1,
    ))

def request_gossip_ids(topic_id: bytes, peer_id: bytes):
    gm = GossipMessage(GossipOp.REQUEST_IDS, topic_id, Packager.node_id)
    Packager.send(gossip_app_id, serialize_gm(gm), peer_id)

def schedule_request_gossip_ids(topic_id: bytes, peer_id: bytes):
    Packager.new_events.append(Event(
        0,
        sha256(gossip_app_id + topic_id + peer_id).digest()[:16],
        request_gossip_ids,
        topic_id, peer_id,
    ))

def respond_gossip_ids(peer_id: bytes, topic_id: bytes):
    ids = []
    for gm_id, (_, gm) in message_cache.items.items():
        if gm.topic_id == topic_id:
            ids.append(gm_id)
    gm = GossipMessage(GossipOp.RESPOND_IDS, topic_id, b''.join(ids))
    Packager.send(gossip_app_id, serialize_gm(gm), peer_id)

def subscribe_gossip(topic_id: bytes, app_id: bytes):
    if topic_id not in subscriptions:
        subscriptions[topic_id] = []
    if app_id not in subscriptions[topic_id]:
        subscriptions[topic_id].append(app_id)

def unsubscribe_gossip(topic_id: bytes, app_id: bytes):
    if topic_id in subscriptions and app_id in subscriptions[topic_id]:
        subscriptions[topic_id].remove(app_id)
    if topic_id in subscriptions and len(subscriptions[topic_id]) == 0:
        del subscriptions[topic_id]

def add_peer_callback(_, pid: bytes, intrfcs: list[tuple[bytes, Interface]]):
    if pid not in Packager.peers:
        for topic_id in subscriptions:
            schedule_request_gossip_ids(topic_id, pid)

def sync_all_peers():
    for pid in Packager.peers:
        for topic_id in subscriptions:
            Gossip.invoke('request_ids', topic_id, pid)

    Packager.new_events.append(Event(
        time_ms() + Gossip.params['schedule_delay']*1000,
        Gossip.id,
        sync_all_peers,
    ))

def start_gossip_app():
    Packager.add_hook('add_peer', add_peer_callback)
    if Gossip.id in Packager.schedule:
        return
    Packager.new_events.append(Event(
        time_ms() + Gossip.params['start_delay']*1000,
        Gossip.id,
        sync_all_peers,
    ))

def stop_gossip_app():
    Packager.remove_hook('add_peer', add_peer_callback)
    Packager.cancel_events.append(Gossip.id)

def get_messages(topic_id: bytes):
    res = []
    for _, val in message_cache.items.items():
        if val[1].topic_id == topic_id:
            res.append(val[1])
    return res


Gossip = Application(
    name='Gossip',
    description='Dev Gossip App',
    version=0,
    receive_func=receive_gm,
    callbacks={
        'publish': lambda _, topic_id, data: publish_gossip(topic_id, data),
        'notify': lambda _, topic_id, data: notify_gossip(topic_id, data),
        'respond': lambda _, topic_id, data: respond_gossip_request(topic_id, data),
        'respond_ids': lambda _, peer_id, topic_id: respond_gossip_ids(peer_id, topic_id),
        'request': lambda _, topic_id, peer_id: request_gossip_message(topic_id, peer_id),
        'request_ids': lambda _, topic_id, peer_id: request_gossip_ids(topic_id, peer_id),
        'subscribe': lambda _, topic_id, app_id: subscribe_gossip(topic_id, app_id),
        'unsubscribe': lambda _, topic_id, app_id: unsubscribe_gossip(topic_id, app_id),
        'deliver_gossip': lambda _, gm: deliver_gossip(gm),
        'sync': lambda _: sync_all_peers(),
        'start': lambda _: start_gossip_app(),
        'stop': lambda _: stop_gossip_app(),
        'get_seen': lambda _: seen_gm,
        'get_subscriptions': lambda _: subscriptions,
        'get_cache': lambda _: message_cache,
        'get_messages': lambda _, topic_id: get_messages(topic_id),
        'serialize': lambda _, gm: serialize_gm(gm),
        'deserialize': lambda _, blob: deserialize_gm(blob),
    },
    params={
        'start_delay': 10,
        'schedule_delay': 20,
        'echo_delay_ms': 20,
    }
)
gossip_app_id = Gossip.id

Packager.add_application(Gossip)


TreeOp = enum(
    SEND = 0,
    RESPOND = 15,
    REQUEST_ADDRESS_ASSIGNMENT = 240,
    ASSIGN_ADDRESS = 255,
)

def tree_state(claim: bytes):
    return crc32(claim).to_bytes(4, 'big')[0]

root_id_targets = (
    b'1234' * 8,
    b'4321' * 8,
    b'5678' * 8,
    b'8765' * 8,
)
now = lambda: int(time_ns() / 1_000_000)
TreeMessage = namedtuple("TreeMessage", ['op', 'ts', 'age', 'claim', 'address', 'node_id'])
seen_tm: deque[TreeMessage] = deque([], 10)
tree_app_id = b''
gossip_app_id = bytes.fromhex('849969c1f22797d66f5a94db2afe634a')
tree_maintenance_rounds = 0

current_children: dict[bytes, int] = {} # map of child peer ids to coordinates
current_parent: bytes = b''
tree_last_ts = int(time())
# tuple of (claim, dTree from root, peer_id)
known_claims: deque[tuple[bytes, int, int, bytes]] = deque([], 10)

# elect self as initial root
current_best_root_id = Packager.node_id
Packager.set_addr(Address(tree_state(Packager.node_id), coords=[]))

tree_age = lambda: int(time()) - tree_last_ts
is_root = lambda: Packager.node_id == current_best_root_id

def xor(b1: bytes, b2: bytes) -> bytes:
    """XOR two equal-length byte strings together."""
    b3 = bytearray()
    for i in range(len(b1)):
        b3.append(b1[i] ^ b2[i])

    return bytes(b3)

def claim_score(node_id: bytes, overlay_idx: int = 0) -> int:
    """Calculate the distance from the target root id. Lower is better."""
    return int.from_bytes(xor(node_id, root_id_targets[overlay_idx]), 'big')

def serialize_tm(tmsg: TreeMessage):
    return pack('!BQB32s16s32s', tmsg.op, tmsg.ts, tmsg.age, tmsg.claim, tmsg.address, tmsg.node_id)

def deserialize_tm(blob: bytes) -> TreeMessage:
    op, ts, age, claim, address, node_id = unpack('!BQB32s16s32s', blob)
    return TreeMessage(op, ts, age, claim, address, node_id)

def lwst_avlbl_coord() -> int|None:
    vals = set(current_children.values())
    for i in range(1, 136):
        if i not in vals:
            return i
    return None

def remove_peer(_, pid: bytes):
    # remove the peer from the current children
    if pid in current_children:
        del current_children[pid]
    # remove the peer from the known claims
    claims = [known_claims.popleft() for _ in range(len(known_claims))]
    for claim, ts, dTree, peer_id in claims:
        if peer_id != pid:
            known_claims.append((claim, ts, dTree, peer_id))

def receive_tm(app: Application, blob: bytes, intrfc: Interface, mac: bytes):
    global current_best_root_id, current_parent, tree_last_ts
    tmsg = deserialize_tm(blob)
    seen_tm.append(tmsg)
    peer_id = Packager.inverse_peers.get((mac, intrfc.id), None)
    their_score = claim_score(tmsg.claim)
    our_score = claim_score(current_best_root_id)

    if tmsg.op == TreeOp.SEND:
        if tmsg.node_id is not None and tmsg.node_id != Packager.node_id:
            Packager.add_route(
                tmsg.node_id, Address(tree_state(tmsg.claim), address=tmsg.address)
            )
            if tmsg.node_id != peer_id:
                # gossip message for app/service discovery; do not respond
                return
        if tmsg.age < SpanningTree.params['max_tree_age']:
            # add the claim to the known claims
            addr = Address(tree_state(tmsg.claim), address=tmsg.address)
            root = Address(tree_state(tmsg.claim), coords=[])
            known_claims.append((tmsg.claim, int(time())-tmsg.age, addr.dTree(root, addr), peer_id))
        if our_score < their_score:
            # we have a better claim, so respond with it
            SpanningTree.invoke('respond', peer_id)
    elif tmsg.op == TreeOp.RESPOND:
        # received a response to a periodic broadcast
        if tmsg.age < SpanningTree.params['max_tree_age']:
            # add the claim to the known claims
            addr = Address(tree_state(tmsg.claim), address=tmsg.address)
            root = Address(tree_state(tmsg.claim), coords=[])
            known_claims.append((tmsg.claim, int(time())-tmsg.age, addr.dTree(root, addr), peer_id))
    elif tmsg.op == TreeOp.REQUEST_ADDRESS_ASSIGNMENT:
        # received an address assignment request
        if tree_state(tmsg.claim) == Packager.node_addrs[-1].tree_state:
            # only respond if the request is for the current tree_state
            if peer_id in current_children:
                # if the node is already child, send its existing address
                coords = list(Packager.node_addrs[-1].coords) + [current_children[peer_id]]
                SpanningTree.invoke('assign_address', peer_id, coords)
                return
            # respond with the address assignment
            coords = list(Packager.node_addrs[-1].coords)
            coord = lwst_avlbl_coord()
            if coord is None or peer_id is None:
                # no available coordinates, or peer_id not found, so reject the request
                return
            coords.append(coord)
            current_children[peer_id] = coord
            SpanningTree.invoke('assign_address', peer_id, coords)
    elif tmsg.op == TreeOp.ASSIGN_ADDRESS:
        # received an address assignment response
        if their_score < our_score and tmsg.node_id != Packager.node_id:
            # accept the address and set the new best claim
            current_best_root_id = tmsg.claim
            current_parent = peer_id
            current_children.clear()
            Packager.set_addr(Address(tree_state(tmsg.claim), tmsg.address))
        else:
            # we have a better claim, so respond with it
            SpanningTree.invoke('respond', peer_id)

    # update the tree last ts if the message is from the parent
    if tmsg.node_id == current_parent:
        tree_last_ts = int(time()) - tmsg.age

def broadcast_tree_message():
    tmsg = TreeMessage(
        TreeOp.SEND,
        now(),
        tree_age(),
        current_best_root_id,
        Packager.node_addrs[-1].address,
        Packager.node_id
    )
    Packager.broadcast(tree_app_id, serialize_tm(tmsg))

def send_tree_message(pid: bytes):
    tmsg = TreeMessage(
        TreeOp.SEND,
        now(),
        tree_age(),
        current_best_root_id,
        Packager.node_addrs[-1].address,
        Packager.node_id
    )
    Packager.send(tree_app_id, serialize_tm(tmsg), pid)

def respond_tree_message(pid: bytes):
    tmsg = TreeMessage(
        TreeOp.RESPOND,
        now(),
        tree_age(),
        current_best_root_id,
        Packager.node_addrs[-1].address,
        Packager.node_id
    )
    Packager.send(tree_app_id, serialize_tm(tmsg), pid)

def request_address_assignment(pid: bytes, claim: bytes):
    tmsg = TreeMessage(
        TreeOp.REQUEST_ADDRESS_ASSIGNMENT,
        now(),
        0,
        claim,
        b'\x00' * 16,
        Packager.node_id
    )
    Packager.send(tree_app_id, serialize_tm(tmsg), pid)

def assign_address(pid: bytes, coords: list[int]):
    addr = Address(tree_state(current_best_root_id), coords=coords)
    tmsg = TreeMessage(
        TreeOp.ASSIGN_ADDRESS,
        now(),
        tree_age(),
        current_best_root_id,
        addr.address,
        Packager.node_id
    )
    Packager.send(tree_app_id, serialize_tm(tmsg), pid)

def periodic_tree_message(count: int):
    """Broadcasts count times with a 30ms delay between."""
    if count <= 0:
        return schedule_tree_maintenance()
    SpanningTree.invoke('broadcast')
    Packager.new_events.append(Event(
        now() + SpanningTree.params['broadcast_interval'],
        tree_app_id,
        periodic_tree_message,
        count - 1
    ))

def send_gossip_tree_message(addr: Address|None = None):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is not None:
        tm = TreeMessage(
            TreeOp.SEND,
            now(),
            tree_age(),
            current_best_root_id,
            addr.address if addr is not None else Packager.node_addrs[-1].address,
            Packager.node_id
        )
        Gossip.invoke('publish', tree_app_id, serialize_tm(tm))

def maintain_tree():
    """Maintains the spanning tree: 1) when a parent has disconnected,
        reset the local state; 2) if there is no parent and there are
        known claims, request an address assignment from the best claim;
        3) begin the periodic_tree_message event; 4) send a gossip
        message every 5th maintenance event.
    """
    global current_best_root_id, current_parent, current_children
    global tree_maintenance_rounds, tree_last_ts

    # check if tree is too old
    if tree_age() > SpanningTree.params['max_tree_age']:
        # reset the local state
        current_best_root_id = Packager.node_id
        current_parent = b''
        current_children.clear()
        Packager.set_addr(Address(tree_state(Packager.node_id), coords=[]))

    # remove expired claims
    claims = [known_claims.pop() for _ in range(len(known_claims))]
    for claim, ts, dTree, peer_id in claims:
        if int(time()) - ts < SpanningTree.params['max_tree_age']:
            known_claims.append((claim, ts, dTree, peer_id))

    # evaluate known claims
    if len(known_claims) > 0:
        current_dTree = Address.dTree(
            Packager.node_addrs[-1],
            Address(tree_state(current_best_root_id), coords=[])
        )
        # get the best known claim (and shortest distance from root)
        claims = list(known_claims)
        claims.sort(key=lambda t: claim_score(t[0]) + t[2])
        best_claim, _, dTree, peer_id = claims[0]
        if claim_score(best_claim) < claim_score(current_best_root_id) or (
            claim_score(best_claim) == claim_score(current_best_root_id) and
            dTree < current_dTree - 1
        ):
            # request an address assignment from the best claim with shortest distance from root
            SpanningTree.invoke('request_address_assignment', peer_id, best_claim)

    # begin broadcasting
    if current_best_root_id == Packager.node_id:
        tree_last_ts = int(time())
    periodic_tree_message(SpanningTree.params['broadcast_count'])

    # tree_maintenance_rounds += 1
    # if tree_maintenance_rounds >= 5:
    #     tree_maintenance_rounds = 0
        # send_gossip_tree_message()
    send_gossip_tree_message()
    # schedule the next maintenance event
    schedule_tree_maintenance()

def schedule_tree_maintenance():
    """Schedules the tree maintenance event for 60s in the future."""
    Packager.new_events.append(Event(
        now() + SpanningTree.params['tree_maintenance_delay'],
        tree_app_id+b's',
        maintain_tree,
    ))

def set_addr_gossip_callback(_, addr: Address):
    send_gossip_tree_message(addr)

def schedule_start(pub = None, sub = None):
    """Schedules the app to start broadcasting with a random delay up to
        params['max_start_delay'] ms.
    """
    if type(pub) is bool:
        SpanningTree.params['pub'] = pub
    if type(sub) is bool:
        SpanningTree.params['sub'] = sub
    global current_best_root_id
    Packager.add_hook('remove_peer', remove_peer)
    current_best_root_id = Packager.node_id
    Packager.set_addr(Address(tree_state(Packager.node_id), coords=[]))
    Packager.new_events.append(Event(
        now() + randint(0, SpanningTree.params['max_start_delay']),
        tree_app_id + b's',
        maintain_tree,
    ))
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is not None:
        if SpanningTree.params.get('pub'):
            Packager.add_hook('set_addr', set_addr_gossip_callback)
        if SpanningTree.params.get('sub'):
            Gossip.invoke('subscribe', tree_app_id, tree_app_id)

def stop_tree_app():
    """Cancels all events and removes all hooks."""
    Packager.remove_hook('remove_peer', remove_peer)
    Packager.remove_hook('set_addr', set_addr_gossip_callback)
    Packager.cancel_events.append(tree_app_id)
    Packager.cancel_events.append(tree_app_id+b's')
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is not None:
        Gossip.invoke('unsubscribe', tree_app_id, tree_app_id)

SpanningTree = Application(
    name='SpanningTree',
    description='Dev SpanningTree App',
    version=0,
    receive_func=receive_tm,
    callbacks={
        'broadcast': lambda _: broadcast_tree_message(),
        'send': lambda _, pid: send_tree_message(pid),
        'respond': lambda _, pid: respond_tree_message(pid),
        'request_address_assignment': lambda _, pid, claim: request_address_assignment(pid, claim),
        'assign_address': lambda _, pid, coords: assign_address(pid, coords),
        'remove_peer': remove_peer,
        'maintain_tree': lambda _: maintain_tree(),
        'schedule_tree_maintenance': lambda _: schedule_tree_maintenance(),
        'serialize': lambda _, tm: serialize_tm(tm),
        'deserialize': lambda _, blob: deserialize_tm(blob),
        'start': lambda _, **kwargs: schedule_start(**kwargs),
        'stop': lambda _: stop_tree_app(),
        'claim_score': lambda _, claim: claim_score(claim),
        'get_known_claims': lambda _: known_claims,
        'get_current_children': lambda _: current_children,
        'get_current_parent': lambda _: current_parent,
        'get_current_best_root_id': lambda _: current_best_root_id,
        'send_gossip_tree_message': lambda _: send_gossip_tree_message(),
        'get_seen': lambda _: seen_tm,
    },
    params={
        'max_start_delay': 10_000,
        'tree_maintenance_delay': 20_000,
        'max_tree_age': 60,
        # 'broadcast_count': MODEM_INTERSECT_RTX_TIMES,
        'broadcast_count': 1,
        'broadcast_interval': MODEM_INTERSECT_INTERVAL,
        'pub': True,
        'sub': False
    }
)
tree_app_id = SpanningTree.id

Packager.add_application(SpanningTree)


PingOp = enum(
    REQUEST = 0,
    RESPOND = 1,
    GOSSIP_REQUEST = 2,
    GOSSIP_RESPOND = 3,
)

PingMessage = namedtuple(
    "PingMessage",
    ['op', 'nonce', 'metric', 'ts1', 'ts2', 'ts3', 'tree_state', 'address', 'node_id']
)

ping_responses: deque[PingMessage] = deque([], 10)
gossip_app_id = bytes.fromhex('849969c1f22797d66f5a94db2afe634a')

def serialize_pm(pm: PingMessage) -> bytes:
    return pack(
        '!BBBQQQB16s32s',
        pm.op,
        pm.nonce,
        pm.metric,
        pm.ts1,
        pm.ts2,
        pm.ts3,
        pm.tree_state,
        pm.address,
        pm.node_id
    )

def deserialize_pm(blob: bytes) -> PingMessage:
    return PingMessage(*unpack('!BBBQQQB16s32s', blob))

def receive_pm(app: Application, blob: bytes, intrfc: Interface, mac: bytes):
    pm = deserialize_pm(blob)
    if pm.op == PingOp.REQUEST:
        # if pm.node_id is not None and pm.node_id != Packager.node_id:
            # Packager.add_route(pm.node_id, Address(pm.tree_state, address=pm.address))
        Ping.invoke('respond', pm)
    elif pm.op == PingOp.RESPOND:
        Ping.invoke('response_received', pm)
    elif pm.op == PingOp.GOSSIP_REQUEST:
        Ping.invoke('gossip_respond', pm)
    elif pm.op == PingOp.GOSSIP_RESPOND:
        Ping.invoke('gossip_response_received', pm)

def ping_request(
        nid_or_addr: bytes|Address, metric: int = dTree,
        nonce: int|None = None, callback: Callable|None = None
    ) -> bool:
    """Send a ping request to the given node id or addr. Returns False
        if it cannot be sent (no route to the node or no local address).
    """
    if len(Packager.node_addrs) == 0:
        return False
    if type(nid_or_addr) is Address:
        addr = nid_or_addr
        node_id = None
    else:
        addr = None
        node_id = nid_or_addr if type(nid_or_addr) is bytes else bytes.fromhex(nid_or_addr)
        nid_or_addr = node_id.hex()
    pm = PingMessage(
        PingOp.REQUEST,
        nonce if nonce is not None else randint(0, 255),
        metric,
        time_ms(),
        0,
        0,
        Packager.node_addrs[-1].tree_state,
        Packager.node_addrs[-1].address,
        Packager.node_id
    )
    res = Packager.send(
        Ping.id, serialize_pm(pm), node_id=node_id, to_addr=addr, metric=metric
    )
    if callback is not None:
        callback(f'ping request to {nid_or_addr} ' + ('sent' if res else 'failed to send'))
    return res

def ping_respond(pm: PingMessage):
    """Send a ping response using the information in the ping message."""
    pm = PingMessage(
        PingOp.RESPOND,
        pm.nonce,
        pm.metric,
        pm.ts1,
        time_ms(),
        0,
        pm.tree_state,
        pm.address,
        pm.node_id
    )
    return Packager.send(
        Ping.id, serialize_pm(pm), node_id=pm.node_id,
        to_addr=Address(pm.tree_state, pm.address), metric=pm.metric
    )

def ping_response_received(pm: PingMessage):
    ping_responses.append(PingMessage(
        pm.op,
        pm.nonce,
        pm.metric,
        pm.ts1,
        pm.ts2,
        time_ms(),
        pm.tree_state,
        pm.address,
        pm.node_id
    ))

def ping_gossip_request(
        node_id: bytes|str, nonce: int|None = None,
        callback: Callable|None = None
    ) -> bool:
    """Send a gossip request to the given node id. Returns False if the
        gossip application is not found or if the local node has no
        address.
    """
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None or len(Packager.node_addrs) == 0:
        return False
    node_id = bytes.fromhex(node_id) if type(node_id) == str else node_id
    pm = PingMessage(
        PingOp.GOSSIP_REQUEST,
        nonce if nonce is not None else randint(0, 255),
        0,
        time_ms(),
        0,
        0,
        Packager.node_addrs[-1].tree_state,
        Packager.node_addrs[-1].address,
        Packager.node_id
    )
    topic_id = sha256(Ping.id + node_id).digest()[:16]
    Gossip.invoke('publish', topic_id, serialize_pm(pm))
    if callback is not None:
        callback('gossip ping request sent')
    return True

def ping_gossip_respond(pm: PingMessage) -> bool:
    """Send a gossip response using the information in the ping message."""
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return False
    pm = PingMessage(
        PingOp.GOSSIP_RESPOND,
        pm.nonce,
        pm.metric,
        pm.ts1,
        time_ms(),
        0,
        pm.tree_state,
        pm.address,
        pm.node_id
    )
    topic_id = sha256(Ping.id + pm.node_id).digest()[:16]
    Gossip.invoke('publish', topic_id, serialize_pm(pm))
    return True

def ping_gossip_response_received(pm: PingMessage):
    ping_responses.append(PingMessage(
        pm.op,
        pm.nonce,
        pm.metric,
        pm.ts1,
        pm.ts2,
        time_ms(),
        pm.tree_state,
        pm.address,
        pm.node_id
    ))

def ping_list_routes():
    """List all routes known to this node."""
    print('Routes (node_id: address):')
    for addr, node_id in Packager.routes.items():
        print(f'\t{node_id.hex()}: {addr.coords} {addr.address.hex()}')

def report_ping_test(
        nonce: int, mode: str, expected_count: int,
        remote_id_or_addr: bytes|Address,
        callback: Callable|None = None
    ) -> dict:
    """Generate a report of the ping test results."""
    # take all relevant pms, then put the rest back
    pms = []
    while len(ping_responses):
        pms.append(ping_responses.popleft())
    relevant_pms = []
    while len(pms):
        pm = pms.pop()
        if pm.nonce == nonce:
            relevant_pms.append(pm)
        else:
            ping_responses.append(pm)
    # generate report
    if type(remote_id_or_addr) is bytes:
        remote = remote_id_or_addr.hex()
    else:
        remote = remote_id_or_addr
    count = len(relevant_pms)
    if count == 0:
        report = {
            'error': 'no responses',
            'remote': remote,
            'mode': mode,
            'expected_count': expected_count,
            'success_rate': '0%',
        }
        if callback is not None:
            callback(report)
        return report
    report = {
        'mode': mode,
        'remote': remote,
        'count': count,
        'expected_count': expected_count,
        'success_rate': f"{int(count / expected_count * 100)}%",
        'there': {
            'min': 10**9,
            'max': 0,
            'avg': 0,
        },
        'back': {
            'min': 10**9,
            'max': 0,
            'avg': 0,
        },
        'round_trip': {
            'min': 10**9,
            'max': 0,
            'avg': 0,
        }
    }
    for pm in relevant_pms:
        delay = pm.ts3 - pm.ts1
        there = pm.ts2 - pm.ts1
        back = pm.ts3 - pm.ts2
        # round trip
        if delay < report['round_trip']['min']:
            report['round_trip']['min'] = delay
        if delay > report['round_trip']['max']:
            report['round_trip']['max'] = delay
        report['round_trip']['avg'] += delay
        # there
        if there < report['there']['min']:
            report['there']['min'] = there
        if there > report['there']['max']:
            report['there']['max'] = there
        report['there']['avg'] += there
        # back
        if back < report['back']['min']:
            report['back']['min'] = back
        if back > report['back']['max']:
            report['back']['max'] = back
        report['back']['avg'] += back
    report['round_trip']['avg'] /= count
    report['there']['avg'] /= count
    report['back']['avg'] /= count
    if callback is not None:
        callback(report)
    return report

def run_ping_test(
        node_id: bytes|None = None, count: int = 4, timeout: int = 5,
        addr: Address|None = None, metric: int = dTree,
        callback: Callable|None = None
    ):
    """Ping a node count times, scheduling a series of pings after a
        delays calculated by multiplying the index by the timeout. Also
        schedules generation of a report after timeout * count seconds.
    """
    if callback is not None:
        callback('ping test started')
    eid = sha256(Ping.id + (node_id or addr.address)).digest()[:16]
    eid += PingOp.REQUEST.to_bytes(1, 'big')
    nonce = randint(0, 255)
    now = time_ms()
    addr = addr if addr is not None else Packager.inverse_routes.get(node_id, [None])[-1]
    for i in range(count):
        Packager.new_events.append(Event(
            now + i * 1000,
            eid + i.to_bytes(1, 'big'),
            ping_request,
            node_id or addr,
            metric,
            nonce,
            callback,
        ))
    Packager.new_events.append(Event(
        now + (timeout + count) * 1000,
        eid + count.to_bytes(1, 'big'),
        report_ping_test,
        nonce,
        'routed dTree' if metric == dTree else 'routed dCPL' if metric == dCPL else 'unknown metric',
        count,
        node_id or addr,
        callback,
    ))

def run_gossip_ping_test(
        node_id: bytes|str, count: int = 4, timeout: int = 5,
        callback: Callable|None = None
    ):
    """Ping a node count times through Gossip, scheduling a series of
        pings after a delays calculated by multiplying the index by the
        timeout. Also schedules generation of a report after timeout *
        count seconds.
    """
    if callback is not None:
        callback('gossip ping test started')
    node_id = bytes.fromhex(node_id) if type(node_id) == str else node_id
    topic_id = sha256(Ping.id + node_id).digest()[:16]
    topic_id += PingOp.GOSSIP_REQUEST.to_bytes(1, 'big')
    nonce = randint(0, 255)
    now = time_ms()
    for i in range(count):
        Packager.new_events.append(Event(
            now + i * 1000,
            topic_id + i.to_bytes(1, 'big'),
            ping_gossip_request,
            node_id,
            nonce,
            callback,
        ))
    Packager.new_events.append(Event(
        now + (timeout + count) * 1000,
        topic_id + count.to_bytes(1, 'big'),
        report_ping_test,
        nonce,
        'gossip',
        count,
        node_id,
        callback,
    ))

def start_ping_app():
    """Subscribe to the gossip topic."""
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return False
    topic_id = sha256(Ping.id + Packager.node_id).digest()[:16]
    Gossip.invoke('subscribe', topic_id, Ping.id)

def stop_ping_app():
    """Unsubscribe from the gossip topic."""
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return False
    topic_id = sha256(Ping.id + Packager.node_id).digest()[:16]
    Gossip.invoke('unsubscribe', topic_id, Ping.id)

def hexify(thing):
    if type(thing) is list:
        return [hexify(i) for i in thing]
    elif type(thing) is tuple:
        return tuple(hexify(i) for i in thing)
    elif type(thing) is bytes:
        return thing.hex()
    elif type(thing) is dict:
        return {hexify(k): hexify(v) for k, v in thing.items()}
    elif type(thing) in (int, float):
        return thing
    else:
        return thing if type(thing) is str else repr(thing)

def ping_cb(report):
    if type(report) is str:
        Ping.params['console_output'](report)
        return
    report = hexify(report)
    r = 'Ping report:\n'
    for k, v in report.items():
        r += f'  {k}: {v}\n'
    Ping.params['console_output'](r)

async def _ping_command(cmd: list[str]):
    """Ping a node."""
    if len(cmd) < 1:
        print('ping - missing required node_id|addr')
        return
    try:
        nid = bytes.fromhex(cmd[0])
        addr = None
    except:
        nid = None
        addr = Address.from_str(cmd[0])
    kwargs = {
        'node_id': nid,
        'addr': addr,
        'callback': ping_cb,
        'timeout': 2,
    }
    if len(cmd) > 1:
        kwargs['count'] = int(cmd[1])
    if len(cmd) > 2:
        kwargs['timeout'] = int(cmd[2])
    Ping.invoke('ping', **kwargs)
    await Ping.params['console_wait'](kwargs.get('count', 4) + 2)

async def _gossip_ping_command(cmd: list[str]):
    """Ping a node via gossip."""
    if len(cmd) < 1:
        print('gossip_ping - missing required subcommand')
        return
    nid = bytes.fromhex(cmd[0])
    kwargs = {
        'node_id': nid,
        'callback': ping_cb,
        'timeout': 2,
    }
    if len(cmd) > 1:
        kwargs['count'] = int(cmd[1])
    if len(cmd) > 2:
        kwargs['timeout'] = int(cmd[2])
    Ping.invoke('gossip_ping', **kwargs)
    await Ping.params['console_wait'](kwargs.get('count', 4) + 2)

def register_ping_cmds(
        add_command: Callable, add_alias: Callable, wait: Callable,
        output: Callable
    ):
    """Register console commands."""
    Ping.params['console_wait'] = wait
    Ping.params['console_output'] = output
    add_command(
        'ping',
        _ping_command,
        'ping [node_id|addr] [count] [timeout] - ping the node_id/address\n' +
            '\tcount should be <=10 (memory constraint); default value is 4\n' +
            '\ttimeout default value is 2 (seconds)\n' +
            '\tIf node_id is provided, it will attempt to find the address ' +
            'from the known routes'
    )
    add_command(
        'gossip_ping',
        _gossip_ping_command,
        'gossip_ping [node_id] [count] [timeout] - ping the node via gossip\n' +
            '\tcount should be <=10 (memory constraint); default value is 4\n' +
            '\ttimeout default value is 2 (seconds)'
    )

Ping = Application(
    name='Ping',
    description='Dev Ping App',
    version=0,
    receive_func=receive_pm,
    callbacks={
        'request': lambda _, node_id: ping_request(node_id),
        'respond': lambda _, pm: ping_respond(pm),
        'response_received': lambda _, pm: ping_response_received(pm),
        'gossip_request': lambda _, node_id: ping_gossip_request(node_id),
        'gossip_respond': lambda _, pm: ping_gossip_respond(pm),
        'gossip_response_received': lambda _, pm: ping_gossip_response_received(pm),
        'serialize': lambda _, pm: serialize_pm(pm),
        'deserialize': lambda _, blob: deserialize_pm(blob),
        'start': lambda _: start_ping_app(),
        'stop': lambda _: stop_ping_app(),
        'list_routes': lambda _: ping_list_routes(),
        'ping': lambda _, *args, **kwargs: run_ping_test(*args, **kwargs),
        'gossip_ping': lambda _, *args, **kwargs: run_gossip_ping_test(*args, **kwargs),
        'report_ping_test': lambda _, *args, **kwargs: report_ping_test(*args, **kwargs),
        'get_ping_responses': lambda _: ping_responses,
        'register_commands': lambda _, *args, **kwargs: register_ping_cmds(*args, **kwargs),
    }
)

Packager.add_application(Ping)
# save_imports
import json


DebugOp = enum(
    REQUEST_NODE_INFO = 0,
    REQUEST_PEER_LIST = 1,
    REQUEST_ROUTES = 2,
    REQUEST_NEXT_HOP = 3,
    RESPOND_NODE_INFO = 100,
    RESPOND_PEER_LIST = 101,
    RESPOND_ROUTES = 102,
    RESPOND_NEXT_HOP = 103,
    # ERROR = 199,
    OK = 200,
    AUTH_ERROR = 201,
    REQUIRE_SET_PW = 251,
    REQUIRE_BAN = 252,
    REQUIRE_UNBAN = 253,
    REQUIRE_REFLECT = 254,
    REQUIRE_RESET = 255,
)
_inverse_op = {
    DebugOp.REQUEST_NODE_INFO: 'REQUEST_NODE_INFO',
    DebugOp.REQUEST_PEER_LIST: 'REQUEST_PEER_LIST',
    DebugOp.REQUEST_ROUTES: 'REQUEST_ROUTES',
    DebugOp.REQUEST_NEXT_HOP: 'REQUEST_NEXT_HOP',
    DebugOp.RESPOND_NODE_INFO: 'RESPOND_NODE_INFO',
    DebugOp.RESPOND_PEER_LIST: 'RESPOND_PEER_LIST',
    DebugOp.RESPOND_ROUTES: 'RESPOND_ROUTES',
    DebugOp.RESPOND_NEXT_HOP: 'RESPOND_NEXT_HOP',
    # DebugOp.ERROR: 'ERROR',
    DebugOp.OK: 'OK',
    DebugOp.AUTH_ERROR: 'AUTH_ERROR',
    DebugOp.REQUIRE_SET_PW: 'REQUIRE_SET_PW',
    DebugOp.REQUIRE_BAN: 'REQUIRE_BAN',
    DebugOp.REQUIRE_UNBAN: 'REQUIRE_UNBAN',
    DebugOp.REQUIRE_REFLECT: 'REQUIRE_REFLECT',
    DebugOp.REQUIRE_RESET: 'REQUIRE_RESET',
}
DebugMessage = namedtuple('DebugMessage', ['op', 'ts', 'nonce', 'from_id', 'data'])

gossip_app_id = bytes.fromhex('849969c1f22797d66f5a94db2afe634a')
seen_results: deque[dict] = deque([], 10)

def debug_auth_check(data: bytes):
    auth_hash1 = sha256(data).digest()[:16]
    l = data[0]
    auth_hash2 = sha256(data[l+1:]).digest()[:16]
    expected = DebugApp.params['admin_pass_hash']
    return auth_hash1 == expected or auth_hash2 == expected

def serialize_dm(dm: DebugMessage):
    return pack(f'!BIH32s{len(dm.data)}s', dm.op, dm.ts, dm.nonce, dm.from_id, dm.data)

def deserialize_dm(blob: bytes) -> DebugMessage:
    return DebugMessage(*unpack(f'!BIH32s{len(blob) - 39}s', blob))

def receive_debug(app: Application, blob: bytes, intrfc: Interface, mac: bytes):
    dm = deserialize_dm(blob)
    if dm.op == DebugOp.REQUEST_NODE_INFO:
        DebugApp.invoke('handle_request_node_info', dm)
    elif dm.op == DebugOp.REQUEST_PEER_LIST:
        DebugApp.invoke('handle_request_peer_list', dm)
    elif dm.op == DebugOp.REQUEST_ROUTES:
        DebugApp.invoke('handle_request_routes', dm)
    elif dm.op == DebugOp.REQUEST_NEXT_HOP:
        DebugApp.invoke('handle_request_next_hop', dm)
    elif dm.op == DebugOp.RESPOND_NODE_INFO:
        DebugApp.invoke('handle_response', dm)
    elif dm.op == DebugOp.RESPOND_PEER_LIST:
        DebugApp.invoke('handle_response', dm)
    elif dm.op == DebugOp.RESPOND_ROUTES:
        DebugApp.invoke('handle_response', dm)
    elif dm.op == DebugOp.RESPOND_NEXT_HOP:
        DebugApp.invoke('handle_response', dm)
    elif dm.op == DebugOp.OK:
        DebugApp.invoke('handle_response', dm)
    elif dm.op == DebugOp.AUTH_ERROR:
        DebugApp.invoke('handle_response', dm)
    else:
        DebugApp.invoke('handle_require', dm)

def handle_request_node_info(dm: DebugMessage):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return
    info = {
        'node_id': Packager.node_id.hex(),
        'node_addrs': [str(addr) for addr in Packager.node_addrs],
        'apps': [app.id.hex() for app in Packager.apps.values()],
    }
    topic_id = sha256(DebugApp.id + dm.from_id).digest()[:16]
    new_dm = DebugMessage(
        DebugOp.RESPOND_NODE_INFO, int(time()), dm.nonce, Packager.node_id, json.dumps(info).encode()
    )
    Gossip.invoke('publish', topic_id, serialize_dm(new_dm))

def handle_request_peer_list(dm: DebugMessage):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return
    info = {
        'node_id': Packager.node_id.hex(),
        'peers': {
            pid.hex(): [str(addr) for addr in peer.addrs]
            for pid, peer in Packager.peers.items()
        },
    }
    topic_id = sha256(DebugApp.id + dm.from_id).digest()[:16]
    new_dm = DebugMessage(
        DebugOp.RESPOND_PEER_LIST, int(time()), dm.nonce, Packager.node_id, json.dumps(info).encode()
    )
    Gossip.invoke('publish', topic_id, serialize_dm(new_dm))

def handle_request_routes(dm: DebugMessage):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return
    info = {
        'node_id': Packager.node_id.hex(),
        'routes': {
            str(addr): pid.hex()
            for addr, pid in Packager.routes.items()
        },
    }
    topic_id = sha256(DebugApp.id + dm.from_id).digest()[:16]
    new_dm = DebugMessage(
        DebugOp.RESPOND_ROUTES, int(time()), dm.nonce, Packager.node_id, json.dumps(info).encode()
    )
    Gossip.invoke('publish', topic_id, serialize_dm(new_dm))

def handle_request_next_hop(dm: DebugMessage):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return
    metric, tree_state, addr = unpack('!BB16s', dm.data)
    next_hop = Packager.next_hop(Address(tree_state, addr), metric)
    info = {
        'next_hop': (next_hop[0].id.hex(), str(next_hop[1]))
            if next_hop is not None else None,
    }
    topic_id = sha256(DebugApp.id + dm.from_id).digest()[:16]
    Gossip.invoke('publish', topic_id, serialize_dm(DebugMessage(
        DebugOp.RESPOND_NEXT_HOP, int(time()), dm.nonce, Packager.node_id,
        json.dumps(info).encode()
    )))

def handle_require(dm: DebugMessage):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return
    topic_id = sha256(DebugApp.id + dm.from_id).digest()[:16]
    if not debug_auth_check(dm.data):
        DebugApp.invoke('output', 'DebugApp: REQUIRE_* received with invalid auth data; ignoring')
        Gossip.invoke('publish', topic_id, serialize_dm(DebugMessage(
            DebugOp.AUTH_ERROR, int(time()), dm.nonce, Packager.node_id,
            json.dumps({'op': _inverse_op[dm.op], 'error': 'AUTH_ERROR'}).encode()
        )))
        return
    if dm.op == DebugOp.REQUIRE_RESET:
        DebugApp.invoke('output', 'DebugApp: REQUIRE_RESET received; scheduling reset')
        Packager.queue_event(Event(
            time_ms() + 200,
            b'reset',
            reset
        ))
        Gossip.invoke('publish', topic_id, serialize_dm(DebugMessage(
            DebugOp.OK, int(time()), dm.nonce, Packager.node_id,
            json.dumps({'op': 'REQUIRE_RESET'}).encode()
        )))
    elif dm.op == DebugOp.REQUIRE_REFLECT:
        DebugApp.invoke('output', 'DebugApp: REQUIRE_REFLECT received')
        op = dm.data[1]
        Gossip.invoke('publish', topic_id, serialize_dm(DebugMessage(
            op, int(time()), dm.nonce, Packager.node_id, dm.data[2:]
        )))
    elif dm.op == DebugOp.REQUIRE_BAN:
        DebugApp.invoke('output', 'DebugApp: REQUIRE_BAN received')
        node_id = dm.data[1:33]
        Packager.ban(node_id)
        Gossip.invoke('publish', topic_id, serialize_dm(DebugMessage(
            DebugOp.OK, int(time()), dm.nonce, Packager.node_id,
            json.dumps({'op': 'REQUIRE_BAN', 'node_id': node_id.hex()}).encode()
        )))
    elif dm.op == DebugOp.REQUIRE_UNBAN:
        DebugApp.invoke('output', 'DebugApp: REQUIRE_UNBAN received')
        node_id = dm.data[1:33]
        Packager.unban(node_id)
        Gossip.invoke('publish', topic_id, serialize_dm(DebugMessage(
            DebugOp.OK, int(time()), dm.nonce, Packager.node_id,
            json.dumps({'op': 'REQUIRE_UNBAN', 'node_id': node_id.hex()}).encode()
        )))
    elif dm.op == DebugOp.REQUIRE_SET_PW:
        DebugApp.invoke('output', 'DebugApp: REQUIRE_SET_PW received')
        l = dm.data[0]
        new_pasw = dm.data[1:1+l]
        DebugApp.params['admin_pass_hash'] = sha256(new_pasw).digest()[:16]
        Gossip.invoke('publish', topic_id, serialize_dm(DebugMessage(
            DebugOp.OK, int(time()), dm.nonce, Packager.node_id,
            json.dumps({'op': 'REQUIRE_SET_PW'}).encode()
        )))
    else:
        DebugApp.invoke('output', 'DebugApp: REQUIRE_* received with unknown op; ignoring')

def handle_response(dm: DebugMessage):
    if len(dm.data):
        try:
            info = json.loads(dm.data.decode())
        except:
            info = {'error': 'Invalid JSON received', 'data': dm.data}
    else:
        info = {}
    result = {
        'op': _inverse_op.get(dm.op, 'UNKNOWN'),
        'from_id': dm.from_id.hex(),
    }
    result.update(info)
    DebugApp.invoke('output', result)
    seen_results.append(result)

def request_debug_info(op: int, peer_id: bytes, data: bytes = b''):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return
    peer_id = peer_id if type(peer_id) is bytes else bytes.fromhex(peer_id)
    topic_id = sha256(DebugApp.id + peer_id).digest()[:16]
    nonce = randint(0, 2**16 - 1)
    dm = DebugMessage(op, int(time()), nonce, Packager.node_id, data)
    Gossip.invoke('publish', topic_id, serialize_dm(dm))

def require_action(op: int, peer_id: bytes, pasw: bytes, more: bytes = b''):
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is None:
        return
    peer_id = peer_id if type(peer_id) is bytes else bytes.fromhex(peer_id)
    topic_id = sha256(DebugApp.id + peer_id).digest()[:16]
    nonce = randint(0, 2**16 - 1)
    data = len(more).to_bytes(1, 'big') + more + pasw
    dm = DebugMessage(op, int(time()), nonce, Packager.node_id, data)
    Gossip.invoke('publish', topic_id, serialize_dm(dm))

def start_debug_app():
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is not None:
        topic_id = sha256(DebugApp.id + Packager.node_id).digest()[:16]
        Gossip.invoke('subscribe', topic_id, DebugApp.id)

def stop_debug_app():
    Gossip = Packager.apps.get(gossip_app_id, None)
    if Gossip is not None:
        topic_id = sha256(DebugApp.id + Packager.node_id).digest()[:16]
        Gossip.invoke('unsubscribe', topic_id, DebugApp.id)

def hexify(thing):
    if type(thing) is list:
        return [hexify(i) for i in thing]
    elif type(thing) is tuple:
        return tuple(hexify(i) for i in thing)
    elif type(thing) is bytes:
        return thing.hex()
    elif type(thing) is dict:
        return {hexify(k): hexify(v) for k, v in thing.items()}
    elif type(thing) in (int, float):
        return thing
    else:
        return thing if type(thing) is str else repr(thing)

async def _debug_command(cmd: list[str]):
    """Debug a node."""
    if len(cmd) < 2:
        print('debug - missing a required arg')
        return
    nid = bytes.fromhex(cmd[0])
    cmd[1] = cmd[1].lower()
    nh_addr = b''
    if cmd[1] not in ('info', 'peers', 'routes', 'next_hop'):
        print(f'debug - unknown mode {cmd[1]}')
        return
    if cmd[1] == 'info':
        op = DebugOp.REQUEST_NODE_INFO
    elif cmd[1] == 'peers':
        op = DebugOp.REQUEST_PEER_LIST
    elif cmd[1] == 'routes':
        op = DebugOp.REQUEST_ROUTES
    elif cmd[1] == 'next_hop':
        if len(cmd) < 4:
            print('debug next_hop - missing a required arg')
            return
        nh_addr = Address.from_str(cmd[2])
        metric = dCPL if 'cpl' in cmd[3].lower() else dTree
        nh_addr = pack('!BB16s', metric, nh_addr.tree_state, nh_addr.address)
        op = DebugOp.REQUEST_NEXT_HOP
    output = DebugApp.params['console_output']
    DebugApp.add_hook('output', lambda *args: output(args[1]))
    DebugApp.add_hook(
        'request',
        lambda *args: output(f'DebugApp.request sent: {hexify(args[1:])}')
    )
    DebugApp.invoke('request', op, nid, nh_addr)
    await DebugApp.params['console_wait'](2)

async def _admin_command(cmd: list[str]):
    """Execute admin command on a node."""
    if len(cmd) < 3:
        print('admin - missing a required arg')
        return
    nid = bytes.fromhex(cmd[0])
    pasw = cmd[1].encode()
    cmd[2] = cmd[2].lower()
    if cmd[2] == 'reset':
        op = DebugOp.REQUIRE_RESET
        DebugApp.invoke('require', op, nid, pasw)
        await DebugApp.params['console_wait'](1)
    elif cmd[2] in ('ban', 'unban'):
        if len(cmd) < 4:
            print('admin - missing a required arg')
            return
        op = DebugOp.REQUIRE_BAN if cmd[2] == 'ban' else DebugOp.REQUIRE_UNBAN
        pid = bytes.fromhex(cmd[3])
        if len(pid) != 32:
            print('admin - invalid peer_id')
            return
        DebugApp.invoke('require', op, nid, pasw, pid)
        await DebugApp.params['console_wait'](1)
    elif cmd[2] == 'set_pw':
        if len(cmd) < 3:
            print('admin - missing a required arg')
            return
        new_pasw = cmd[3].encode()
        op = DebugOp.REQUIRE_SET_PW
        DebugApp.invoke('require', op, nid, pasw, new_pasw)
        await DebugApp.params['console_wait'](1)
    else:
        print(f'admin - unknown subcommand {cmd[2]}')
        return

async def _local_admin_command(cmd: list[str]):
    """Execute admin command on the local node."""
    if len(cmd) < 2:
        print('local_admin - missing a required arg')
        return
    pasw = cmd[1].encode()
    DebugApp.params['admin_pass_hash'] = sha256(pasw).digest()[:16]
    print('local_admin - admin password set')

def register_debug_cmds(
        add_command: Callable, add_alias: Callable, wait: Callable,
        output: Callable
    ):
    """Register console commands."""
    DebugApp.params['console_wait'] = wait
    DebugApp.params['console_output'] = output
    add_command(
        'debug',
        _debug_command,
        'debug [node_id] [info|peers|routes|next_hop addr metric] - get ' +
            'debug info from a node'
    )
    add_command(
        'admin',
        _admin_command,
        'admin [node_id] [passwd] [set_pw passwd|reset|ban peer_id|unban peer_id] ' +
            '- execute an admin command on a node'
    )
    add_command(
        'local_admin',
        _local_admin_command,
        'local_admin [set_pw passwd] - set the local admin password'
    )

DebugApp = Application(
    name='DebugApp',
    description='Debug App',
    version=0,
    receive_func=receive_debug,
    callbacks={
        'handle_request_node_info': lambda _, dm: handle_request_node_info(dm),
        'handle_request_peer_list': lambda _, dm: handle_request_peer_list(dm),
        'handle_request_routes': lambda _, dm: handle_request_routes(dm),
        'handle_request_next_hop': lambda _, dm: handle_request_next_hop(dm),
        'handle_response': lambda _, dm: handle_response(dm),
        'handle_require': lambda _, dm: handle_require(dm),
        'request': lambda _, op, peer_id, *args: request_debug_info(op, peer_id, *args),
        'require': lambda _, op, peer_id, pasw, *args: require_action(op, peer_id, pasw, *args),
        'deserialize': lambda _, blob: deserialize_dm(blob),
        'serialize': lambda _, dm: serialize_dm(dm),
        'start': lambda _: start_debug_app(),
        'stop': lambda _: stop_debug_app(),
        'get_seen': lambda _: seen_results,
        'auth_check': lambda _, data: debug_auth_check(data),
        'register_commands': lambda _, *args, **kwargs: register_debug_cmds(*args, **kwargs),
    },
    params={
        'admin_pass_hash': bytes.fromhex('32549bff6d8404c4d121b589f4d24ac6'),
    }
)

Packager.add_application(DebugApp)
# save_imports
import network
import espnow


_config = {}
sta_if = network.WLAN(network.STA_IF)
# sta_if.disconnect()
sta_if.active(True)
sta_if.config(channel=14)
e = espnow.ESPNow()
e.active(True)
e.add_peer(b'\xff\xff\xff\xff\xff\xff')

def wake_espnwintrfc(*args, **kwargs):
    sta_if.active(True)
    sta_if.config(channel=14)

def config_espnwintrfc(intrfc: Interface, data: dict):
    for k,v in data.items():
        _config[k] = v

def recv_espnwintrfc(intrfc: Interface) -> bytes|None:
    try:
        res = e.recv(0)
        if res and len(res) == 2 and res[0] and res[1]:
            return Datagram(res[1], intrfc.id, res[0])
    except:
        return None

def send_espnwintrfc(datagram: Datagram):
    if datagram.addr not in [p[0] for p in e.get_peers()]:
        e.add_peer(datagram.addr)
    e.send(datagram.addr, datagram.data, False)

def broadcast_espnwintrfc(datagram: Datagram):
    e.send(b'\xff\xff\xff\xff\xff\xff', datagram.data, False)


ESPNowInterface = Interface(
    name='espnow',
    bitrate=12_000_000,
    configure=config_espnwintrfc,
    supported_schemas=[i for i in range(0, 11)],
    receive_func=recv_espnwintrfc,
    send_func=send_espnwintrfc,
    broadcast_func=broadcast_espnwintrfc,
    wake_func=wake_espnwintrfc,
)

Packager.add_interface(ESPNowInterface)
"""Copyright (c) 2025 Jonathan Voss (k98kurz)

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyleft notice and this permission notice appear in
all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""

try:
    from asyncio import sleep_ms
except ImportError:
    # platform differences with CPython; enable testing
    from asyncio import sleep
    sleep_ms = lambda ms: sleep(ms / 1000)

from collections import deque
import sys
import select


line_buffer = deque([], 10)

async def ainput(prompt="", timeout=False):
    """An asynchronous line input function for MicroPython that detects arrow key sequences."""
    sys.stdout.write(prompt)
    flush = getattr(sys.stdout, 'flush', lambda: None)
    flush()
    line = ""
    while True:
        # Poll sys.stdin to see if there's any data available.
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            char = sys.stdin.read(1)

            # Detect beginning of an escape sequence.
            if char == "\x1b":
                # Give a tiny window for the next bytes (usually 2) to arrive.
                r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                if r2:
                    # Read the next two characters.
                    seq = sys.stdin.read(2)
                    if len(seq) == 2 and seq[0] == "[" and seq[1] in "ABCD":
                        # Arrow key: (ESC + '[' + [A, B, C, or D])
                        if seq[1] == "A":
                            # up
                            if len(line_buffer):
                                # clear the line
                                oldl = line
                                oldll = len(line)
                                line = line_buffer.pop()
                                line_buffer.appendleft(line)
                                if oldl == line:
                                    sys.stdout.write("\r" + prompt + " " * oldll)
                                else:
                                    sys.stdout.write("\r" + prompt + line)
                                    sys.stdout.write(" " * (oldll - len(line)))
                                # move cursor to the end of the line
                                sys.stdout.write("\r" + prompt + line)
                                flush()
                        elif seq[1] == "B":
                            # down
                            if len(line_buffer):
                                # clear the line
                                oldl = line
                                oldll = len(line)
                                line = line_buffer.popleft()
                                line_buffer.append(line)
                                sys.stdout.write("\r" + prompt + line)
                                sys.stdout.write(" " * (oldll - len(line)))
                                if oldl == line:
                                    sys.stdout.write("\r" + prompt + " " * oldll)
                                else:
                                    sys.stdout.write("\r" + prompt + line)
                                    sys.stdout.write(" " * (oldll - len(line)))
                                # move cursor to the end of the line
                                sys.stdout.write("\r" + prompt + line)
                                flush()
                        elif seq[1] == "C":
                            # right; ignore
                            ...
                        elif seq[1] == "D":
                            # left; ignore
                            ...
                        continue
                    else:
                        # Not an arrow key; treat the escape and sequence as normal text.
                        line += char + seq
                        sys.stdout.write(char + seq)
                        flush()
                        continue
                else:
                    # No additional characters found; treat the ESC as a normal character.
                    line += char
                    sys.stdout.write(char)
                    flush()
                    continue

            if char in ("\r", "\n"):
                sys.stdout.write("\n")
                flush()
                if len(line) and line not in line_buffer:
                    line_buffer.append(line)
                return line
            elif char in ("\x08", "\x7f"):
                if line:
                    line = line[:-1]
                    # Erase the last character visually.
                    sys.stdout.write("\b \b")
                    flush()
            else:
                line += char
                sys.stdout.write(char)
                flush()
        # Yield control so other asyncio tasks can run.
        await sleep_ms(10)

        # If the timeout flag is set, return None.
        if timeout:
            return None

