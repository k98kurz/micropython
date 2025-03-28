from asyncio import sleep_ms
from collections import deque, OrderedDict
from machine import reset, Pin
from micropycelium import (
    Packager, Address, dCPL, dTree, PROTOCOL_VERSION,
    ESPNowInterface, Beacon, Gossip, SpanningTree, Ping, DebugApp, DebugOp,
    ainput, debug, iscoroutine,
)
from micropython import const
from struct import pack
import gc

try:
    from typing import Callable
except:
    pass


MPNODE_VERSION = const('0.1.0-dev')


async def blink(p: Pin, ms: int):
    """Toggle the pin for the given number of milliseconds."""
    v = p.value()
    p.value(not v)
    await sleep_ms(ms)
    p.value(v)

async def bloop(q: deque, p: Pin):
    """Blink the pin whenever the queue has a value."""
    while True:
        while len(q):
            q.popleft()
            await blink(p, 100)
        await sleep_ms(1)

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

debug_q = deque([], 25)
def debug(*args):
    debug_q.append(args)

def debug_name(name: str):
    def inner(*args):
        args = [hexify(a) for a in args]
        debug(name, *args)
    return inner

hooks_added = False
def add_hooks():
    global hooks_added
    if hooks_added:
        return
    hooks_added = True
    Packager.add_hook('send', debug_name('Packager.send'))
    Packager.add_hook('broadcast', debug_name('Packager.broadcast'))
    Packager.add_hook('receive', debug_name('Packager.receive'))
    Packager.add_hook('receive:rns', debug_name('Packager.receive:rns'))
    Packager.add_hook('receive:nia', debug_name('Packager.receive:nia'))
    Packager.add_hook('rns', debug_name('Packager.rns'))
    Packager.add_hook('send_packet', debug_name('Packager.send_packet'))
    Packager.add_hook('deliver', debug_name('Packager.deliver'))
    Packager.add_hook('add_peer', debug_name('Packager.add_peer'))
    Packager.add_hook('add_route', debug_name('Packager.add_route'))
    Packager.add_hook('set_addr', debug_name('Packager.set_addr'))
    Packager.add_hook('remove_peer', debug_name('Packager.remove_peer'))
    Packager.add_hook('deliver:checksum_failed', debug_name('Packager.deliver:checksum_failed'))
    Packager.add_hook('deliver:receive_failed', debug_name('Packager.deliver:receive_failed'))
    Packager.add_hook('modemsleep', debug_name('modemsleep'))
    Packager.add_hook('sleepskip', debug_name('sleepskip'))

async def memrloop():
    while True:
        await sleep_ms(10_000)
        gc.collect()
        fr = gc.mem_free()
        al = gc.mem_alloc()
        debug(
            '**Memory Report**\n' +
            f'\t{fr} ({fr/(fr+al)*100:.2f}%) free\n' +
            f'\t{al} ({al/(fr+al)*100:.2f}%) allocated'
        )


commands: OrderedDict[str, tuple[Callable, str]] = OrderedDict()
cmd_aliases: dict[str, str] = {}

def add_command(name: str, func: Callable, help_text: str = ''):
    """Add a command to the console. If help_text is empty, it will not
        be mentioned when the "help" command is run.
    """
    commands[name] = (func, help_text)

def add_cmd_alias(cmd: str, alias: str):
    """Add an alias for a command."""
    cmd_aliases[alias] = cmd

def _indent(txt: str) -> str:
    txt = txt.split('\n')
    for i in range(len(txt)):
        txt[i] = '\t' + txt[i]
    return '\n'.join(txt)

def _help(cmd = []):
    if len(cmd):
        cmd = cmd[0]
        if cmd in cmd_aliases:
            cmd = cmd_aliases[cmd]
        if cmd in commands:
            print(commands[cmd][1])
            return
    print('Commands:')
    for _, v in commands.items():
        if len(v[1]):
            print(_indent(v[1]))
    print('\tq|quit - quit the program')
    print('\treset - reset the device')

outq = deque([], 2)
output = lambda res: outq.append(res)
async def wait(c = 1):
    print("Waiting for output. Hit Enter to stop waiting (command will run in background)...")
    i = 0
    while i < c or c < 0:
        if len(outq):
            print(outq.popleft())
            i += 1
        if await ainput('', True) is not None:
            break

def filter(msg, greps):
    matched = len(greps) == 0
    for p in greps:
        if type(msg) is str and p in msg:
            matched = True
        elif type(msg) in (list, tuple):
            for m in msg:
                if type(m) is str and p in m:
                    matched = True
        elif type(msg) is dict:
            for k, v in msg.values():
                if (type(k) is str and p in k) or (
                    type(v) is str and p in v
                ):
                    matched = True
    return matched

async def monitor(greps: tuple[str]|list[str] = []):
    print("Hit Enter to stop monitoring")
    while True:
        if len(debug_q):
            msg = debug_q.popleft()
            if filter(msg, greps):
                if type(msg) in (tuple, list):
                    print(*msg)
                else:
                    print(msg)
        if len(outq):
            msg = outq.popleft()
            if filter(msg, greps):
                print(msg)
        else:
            if await ainput('', True) is not None:
                break

def _get(cmd):
    if len(cmd) < 1:
        print('get - missing a required arg')
        return
    if cmd[0].lower() == 'node_id':
        print(f'Node ID: {Packager.node_id.hex()}')
    elif cmd[0].lower() == 'addrs':
        addrs = [a for a in Packager.node_addrs]
        print(f'Addresses: {addrs}')
    elif cmd[0].lower() == 'peers':
        peers = [pid.hex() for pid in Packager.peers]
        print(f'Peers:')
        for peer in peers:
            print(f'  {peer}')
    elif cmd[0].lower() == 'routes':
        print(f'Routes:')
        for addr, pid in Packager.routes.items():
            print(f'  {addr} -> {pid.hex()}')
    elif cmd[0].lower() == 'banned':
        print(f'Banned:')
        for nid in Packager.banned:
            print(f'  {nid.hex()}')
    elif cmd[0].lower() == 'next_hop':
        if len(cmd) < 3:
            print('get next_hop - missing a required arg')
            return
        nh_addr = Address.from_str(cmd[1])
        metric = dCPL if 'cpl' in cmd[2].lower() else dTree
        nh = Packager.next_hop(nh_addr, metric)
        if nh is None:
            print(f'No next hop found for {nh_addr}')
        else:
            print(f'Next Hop: {nh[0].id.hex()} {nh[1]}')
    elif cmd[0].lower() == 'apps':
        print(f'Apps:')
        for _, app in Packager.apps.items():
            print(f'  {app.id.hex()} - {app.name} - version {app.version}')
    elif cmd[0].lower() in ('sched', 'schedule'):
        print(f'Schedule:')
        for _, event in Packager.schedule.items():
            print(f'  {event.id.hex()} - {event.handler.__name__} - {event.args} - {event.kwargs}')

def _set(cmd):
    if len(cmd) < 2:
        print('set - missing a required arg')
        return
    if cmd[0].lower() == 'node_id':
        if len(cmd[1]) != 64:
            print('set node_id - invalid node_id')
            return
        Packager.set_node_id(bytes.fromhex(cmd[1]))
    elif cmd[0].lower() == 'addr':
        addr = Address.from_str(cmd[1])
        Packager.set_addr(addr)
    elif cmd[0].lower() == 'route':
        if len(cmd) < 3:
            print('set route - missing a required arg')
            return
        nid = bytes.fromhex(cmd[1])
        addr = Address.from_str(cmd[2])
        Packager.add_route(nid, addr)
    else:
        print(f'Unknown set option: {cmd[0]}')

def _app(cmd):
    if len(cmd) < 2:
        print('app - missing a required arg')
        return
    app_id = bytes.fromhex(cmd[0])
    if app_id not in Packager.apps:
        print(f'Unknown app: {app_id.hex()}')
        return
    if cmd[1].lower() == 'start':
        Packager.apps[app_id].invoke('start')
    elif cmd[1].lower() == 'stop':
        Packager.apps[app_id].invoke('stop')
    elif cmd[1].lower() == 'invoke':
        Packager.apps[app_id].invoke(*cmd[2:])
    else:
        print(f'Unknown app command: {cmd[1]}')

def _ban(cmd):
    if len(cmd) < 1:
        print('ban - missing a required arg')
        return
    try:
        nid = bytes.fromhex(cmd[0])
    except:
        print(f'ban - invalid node_id: {cmd[0]}')
        return
    Packager.ban(nid)

def _unban(cmd):
    if len(cmd) < 1:
        print('unban - missing a required arg')
        return
    try:
        nid = bytes.fromhex(cmd[0])
    except:
        print(f'unban - invalid node_id: {cmd[0]}')
        return
    Packager.unban(nid)

def _version(_):
    print(f'MPNode version: {MPNODE_VERSION}')
    print(f'Packager version: {Packager.version}')
    print(f'Protocol version: {PROTOCOL_VERSION}')

# register default console commands
add_command('help', _help, '')
add_cmd_alias('help', '?')
add_cmd_alias('help', 'h')

add_command(
    'monitor', monitor,
    'm|monitor [grep1] [grep2] ... - monitors debug messages\n' +
        '\tgreps are optional; if supplied, only messages containing a ' +
        'grep will be displayed'
)
add_cmd_alias('monitor', 'm')

add_command(
    'wait', lambda cmd: wait(int(cmd[0])) if cmd else wait(-1),
    'w|wait [count] - wait for [count=-1] output messages (count<0 ' +
        'waits indefinitely)'
)
add_cmd_alias('wait', 'w')

add_command(
    'get', _get,
    'get [node_id|addrs|apps|sched|schedule|peers|routes|banned|next_hop addr metric] - ' +
        'get info from the local node'
)

add_command(
    'set', _set,
    'set [node_id hex|addr str|route peer_id addr] - ' +
        'set or add a config value for the local node'
)

add_command(
    'app', _app,
    'app [app_id] [start|stop|invoke name ...args] - start or stop an app, or ' +
        'invoke an app command'
)

add_command(
    'ban', _ban, 'ban [node_id] - ban a node from being a peer or known route'
)

add_command(
    'unban', _unban, 'unban [node_id] - unban a node from being a peer or known route'
)

Ping.invoke('register_commands', add_command, add_cmd_alias, wait, output)
DebugApp.invoke('register_commands', add_command, add_cmd_alias, wait, output)

add_command('version', _version, 'version - show version information')

async def console(add_debug_hooks = True, pub_routes = True, sub_routes = False):
    if add_debug_hooks:
        add_hooks()
    SpanningTree.params['pub'] = pub_routes
    SpanningTree.params['sub'] = sub_routes
    DebugApp.add_hook('output', lambda *args: output(args[1]))
    await monitor()
    while True:
        cmd = (await ainput("μpycelium> ")).split()
        if len(cmd) == 0:
            continue
        cmd[0] = cmd[0].lower()
        try:
            if cmd[0] in cmd_aliases:
                cmd[0] = cmd_aliases[cmd[0]]
            if cmd[0] in commands:
                co = commands[cmd[0]][0](cmd[1:])
                if co is not None and iscoroutine(co):
                    await co
            elif cmd[0] in ('quit', 'q'):
                raise Exception('quit')
            elif cmd[0] == 'reset':
                reset()
            else:
                print(f'Unknown command: {cmd[0]}')
                _help()
        except Exception as e:
            if str(e) == 'quit':
                raise e
            print(f'Error: {e}')
# save_imports
from asyncio import run, gather, create_task
from neopixel import NeoPixel

# RGB LED of the M5stamp-Pico
rgb = NeoPixel(Pin(27, Pin.OUT), 1)
rq = deque([], 10)
async def rloop():
    while True:
        r = rq.popleft() if len(rq) else (0, 0, 0)
        rgb.fill(r)
        rgb.write()
        await sleep_ms(100 if any(r) else 1)

btn = Pin(39, Pin.IN)
btnq = deque([], 5)

async def monitor_btn(p: Pin, q: deque, debounce_ms: int, inverse: bool = True):
    while True:
        if (inverse and not p.value()) or (not inverse and p.value()):
            q.append(1)
            Beacon.invoke('start')
            SpanningTree.invoke('broadcast')
            await sleep_ms(debounce_ms)
        await sleep_ms(1)

# colors
blue = (0, 0, 255)
red = (255, 0, 0)
green = (0, 255, 0)
yellow = (255, 255, 0)
orange = (255, 165, 0)
white = (255, 255, 255)
purple = (128, 0, 128)
pink = (255, 192, 203)

def action_hook(name: str, c: tuple, q: deque):
    def inner(*args):
        args = [hexify(a) for a in args]
        debug(name, *args)
        q.append(c)
    return inner

# add some hooks
Beacon.add_hook('receive', action_hook('Beacon.receive', blue, rq))
Beacon.add_hook('broadcast', action_hook('Beacon.broadcast', red, rq))
Beacon.add_hook('respond', action_hook('Beacon.respond', green, rq))
Beacon.add_hook('send', debug_name('Beacon.send'))

Gossip.add_hook('receive', action_hook('Gossip.receive', purple, rq))
Gossip.add_hook('publish', debug_name('Gossip.publish'))
Gossip.add_hook('respond', action_hook('Gossip.respond', purple, rq))

SpanningTree.add_hook('receive', action_hook('SpanningTree.receive', white, rq))
SpanningTree.add_hook('broadcast', action_hook('SpanningTree.broadcast', pink, rq))
SpanningTree.add_hook('send', action_hook('SpanningTree.send', orange, rq))
SpanningTree.add_hook('respond', debug_name('SpanningTree.respond'))
SpanningTree.add_hook('assign_address', debug_name('SpanningTree.assign_address'))
SpanningTree.add_hook(
    'request_address_assignment',
    debug_name('SpanningTree.request_address_assignment')
)

def ping_respond_hook(*args, **kwargs):
    debug('Ping.respond', *args)
    rq.append(red)
    rq.append(white)
    rq.append(blue)
    rq.append(red)
    rq.append(white)
    rq.append(blue)

Ping.add_hook('request', debug_name('Ping.request'))
Ping.add_hook('respond', ping_respond_hook)
Ping.add_hook('response_received', debug_name('Ping.response_received'))
Ping.add_hook('gossip_request', debug_name('Ping.gossip_request'))
Ping.add_hook('gossip_respond', debug_name('Ping.gossip_respond'))
Ping.add_hook('gossip_response_received', debug_name('Ping.gossip_response_received'))

DebugApp.add_hook('output', debug_name('DebugApp.output'))
DebugApp.add_hook('receive', debug_name('DebugApp.receive'))

tasks = None

async def _start(
        additional_tasks = [],
        add_debug_hooks = True, pub_routes = True, sub_routes = False,
        add_intrfc_debug_hooks = False,
    ):
    if add_intrfc_debug_hooks:
        ESPNowInterface.add_hook(
            'process:receive',
            debug_name(f'Interface({ESPNowInterface.name}).process:receive')
        )
        ESPNowInterface.add_hook(
            'process:send',
            debug_name(f'Interface({ESPNowInterface.name}).process:send')
        )
        ESPNowInterface.add_hook(
            'process:broadcast',
            debug_name(f'Interface({ESPNowInterface.name}).process:broadcast')
        )
    Beacon.invoke('start')
    Gossip.invoke('start')
    SpanningTree.invoke('start')
    Ping.invoke('start')
    DebugApp.invoke('start')
    global tasks
    try:
        if tasks:
            for task in tasks:
                try:
                    task.cancel()
                except:
                    pass
        tasks = [
            create_task(Packager.work(use_modem_sleep=False)),
            create_task(rloop()),
            create_task(monitor_btn(btn, btnq, 800)),
            create_task(memrloop()),
            create_task(console(add_debug_hooks, pub_routes, sub_routes)),
        ]
        for task in additional_tasks:
            tasks.append(task)
        while True:
            try:
                await gather(*tasks)
            except Exception as e:
                if str(e) == 'quit':
                    break
                raise e
    except OSError as e:
        print('OSError encountered; resetting device')
        reset()

def start(
        additional_tasks = [],
        add_debug_hooks = True, pub_routes = True, sub_routes = False,
        add_intrfc_debug_hooks = False,
    ):
    run(_start(
        additional_tasks, add_debug_hooks, pub_routes, sub_routes,
        add_intrfc_debug_hooks,
    ))
