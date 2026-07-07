"""
EmperorsTouch bridge — makes the Darktide mod work with hardware beyond
Lovense Remote on this PC.

Run `python bridge.py` and a small window opens with two modes:

  Intiface / buttplug.io   Translate the Lovense local-API commands the
                           mod sends into the Buttplug protocol via
                           Intiface Central (address field = Intiface
                           websocket URL).

  Phone relay              Forward the mod's commands to the Lovense
                           Connect app on a phone on your LAN (address
                           field = the phone's IP). Fixes the phone app's
                           wrong response Content-Type, which the game
                           engine rejects.

In-game, set Mod Options -> Emperor's Touch -> Toy Backend to
"Local Bridge". The bridge always listens on port 20010.

Dependencies: pip install aiohttp buttplug-py
"""

import asyncio
import json
import sys

from aiohttp import web, ClientSession, TCPConnector

# ---------------------------------------------------------------------------
# Lovense-style response helpers (shapes the mod already parses)
# ---------------------------------------------------------------------------

def lovense_response(code, extra=None, type_="OK"):
    body = {"code": code, "type": type_}
    if extra:
        body.update(extra)
    return web.json_response(body)


def ok(extra=None):
    return lovense_response(200, extra)


def no_toys():
    # Lovense Remote answers 402 when reachable but no toys are paired
    return lovense_response(402)


def error(message):
    print(f"  !! {message}")
    return lovense_response(400, {"message": message}, type_="ERROR")


# ---------------------------------------------------------------------------
# Relay mode
# ---------------------------------------------------------------------------

class RelayBackend:
    """Forwards commands to Lovense Connect on a phone, re-tagging the
    response Content-Type as application/json (the phone app sends
    text/html, which Darktide's HTTP client rejects)."""

    def __init__(self, target):
        self.url = f"https://{target}:30010/command"
        self.session = None

    async def start(self):
        # The phone's cert is for *.lovense.club, not its LAN IP
        self.session = ClientSession(connector=TCPConnector(ssl=False))
        print(f"Relaying to {self.url}")

    async def shutdown(self):
        if self.session:
            await self.session.close()

    async def handle(self, body):
        try:
            async with self.session.post(
                self.url, json=body,
                headers={"Content-Type": "application/json"},
                timeout=10,
            ) as upstream:
                text = await upstream.text()
                print(f"  <- {upstream.status}: {text}")
                return web.Response(
                    text=text, status=200, content_type="application/json"
                )
        except Exception as e:
            print(f"  !! phone unreachable: {e}")
            return lovense_response(500, {"message": "relay: phone unreachable"}, "ERROR")


# ---------------------------------------------------------------------------
# Buttplug mode
# ---------------------------------------------------------------------------

# Lovense action name -> (max strength, preferred Buttplug ActuatorTypes)
ACTIONS = {
    "Vibrate":   (20,  ["vibrate"]),
    "Rotate":    (20,  None),               # rotatory actuators
    "Pump":      (3,   ["inflate", "constrict"]),
    "Thrusting": (20,  ["oscillate", "position"]),
    "Fingering": (20,  ["oscillate", "vibrate"]),
    "Suction":   (20,  ["constrict"]),
    "Depth":     (3,   ["position"]),
    "Stroke":    (100, None),               # linear actuators
    "Oscillate": (20,  ["oscillate"]),
}


def parse_action_string(action):
    """'Vibrate:12,Rotate:5' -> { 'Vibrate': 12, 'Rotate': 5 }; 'Stop' -> None."""
    if action == "Stop":
        return None
    levels = {}
    for part in action.split(","):
        name, _, value = part.partition(":")
        name = name.strip()
        if name in ACTIONS:
            try:
                levels[name] = float(value)
            except ValueError:
                pass
    return levels


class ButtplugBackend:
    def __init__(self, intiface_url):
        self.intiface_url = intiface_url
        self.client = None
        self.connected = False
        self.timers = {}      # device index -> asyncio.Task running its schedule
        self._conn_task = None

    async def start(self):
        # Import here so relay mode works without buttplug-py installed
        global Client, WebsocketConnector, ProtocolSpec
        from buttplug import Client, WebsocketConnector, ProtocolSpec

        self._conn_task = asyncio.create_task(self._connection_loop())

    async def shutdown(self):
        if self._conn_task:
            self._conn_task.cancel()
        for task in self.timers.values():
            task.cancel()
        self.timers.clear()
        if self.connected and self.client:
            try:
                for device in self.client.devices.values():
                    await self.stop_device(device)
                await self.client.disconnect()
            except Exception:
                pass
        self.connected = False

    async def _connection_loop(self):
        while True:
            if not self.connected:
                try:
                    client = Client("EmperorsTouch Bridge", ProtocolSpec.v3)
                    connector = WebsocketConnector(self.intiface_url, logger=client.logger)
                    await client.connect(connector)
                    await client.start_scanning()
                    self.client = client
                    self.connected = True
                    self._known_devices = {}
                    print(f"Connected to Intiface at {self.intiface_url}, scanning for devices...")

                    # Give scanning a moment, then report what was found
                    await asyncio.sleep(3)
                    if not self.client.devices:
                        print("No devices found yet. Check they are connected in Intiface Central; the bridge keeps watching.")
                except Exception as e:
                    print(f"Intiface not reachable ({e}); retrying in 5s")
                    self.connected = False
            else:
                # Probe liveness cheaply; buttplug-py flips connected on drop
                if not getattr(self.client, "connected", True):
                    print("Lost Intiface connection; reconnecting")
                    self.connected = False

            self._report_device_changes()
            await asyncio.sleep(5)

    def _report_device_changes(self):
        """Logs devices appearing/disappearing since the last check."""
        current = self.devices()
        known = getattr(self, "_known_devices", {})

        for index, device in current.items():
            if index not in known:
                print(f"Device found: {device.name} (toy id bp{index})")
        for index, name in known.items():
            if index not in current:
                print(f"Device lost: {name}")

        self._known_devices = {i: d.name for i, d in current.items()}

    # -- device helpers ----------------------------------------------------

    def devices(self):
        if not (self.connected and self.client):
            return {}
        return dict(self.client.devices)

    def resolve_targets(self, toy_field):
        """Lovense 'toy' field (absent/str/list of 'bp<idx>') -> device list."""
        devices = self.devices()
        if toy_field is None:
            return list(devices.values())
        ids = toy_field if isinstance(toy_field, list) else [toy_field]
        out = []
        for toy_id in ids:
            if isinstance(toy_id, str) and toy_id.startswith("bp"):
                try:
                    index = int(toy_id[2:])
                except ValueError:
                    continue
                if index in devices:
                    out.append(devices[index])
        return out

    # -- command application -------------------------------------------------

    async def apply_levels(self, device, levels):
        """Send one Lovense-style level set to a device's actuators."""
        for action, value in levels.items():
            max_strength, preferred = ACTIONS[action]
            strength = max(0.0, min(1.0, value / max_strength))

            if action == "Rotate":
                for act in getattr(device, "rotatory_actuators", []):
                    await act.command(strength, True)
                continue

            if action == "Stroke":
                # Linear actuators need a move duration; map speed to a
                # stroke cycle and let the timer task repeat it.
                for act in getattr(device, "linear_actuators", []):
                    duration_ms = int(2000 - 1700 * strength) if strength > 0 else 0
                    if duration_ms > 0:
                        await act.command(duration_ms, 0.9)
                continue

            actuators = list(getattr(device, "actuators", []))
            if not actuators:
                continue
            target = None
            if preferred:
                for act in actuators:
                    act_type = str(getattr(act, "type", "")).lower()
                    if any(p in act_type for p in preferred):
                        target = act
                        break
            target = target or actuators[0]
            await target.command(strength)

    async def stop_device(self, device):
        try:
            await device.stop()
        except Exception:
            # Older library versions: zero everything instead
            for act in getattr(device, "actuators", []):
                await act.command(0)
            for act in getattr(device, "rotatory_actuators", []):
                await act.command(0, True)

    def cancel_timer(self, device):
        task = self.timers.pop(device.index, None)
        if task:
            task.cancel()

    async def run_schedule(self, device, levels, time_sec, loop_on, loop_off):
        """Emulates Lovense timeSec / loop semantics (Buttplug has neither)."""
        try:
            loop = asyncio.get_event_loop()
            end_time = loop.time() + time_sec if time_sec > 0 else None

            if loop_on and loop_off:
                while end_time is None or loop.time() < end_time:
                    await self.apply_levels(device, levels)
                    await asyncio.sleep(loop_on)
                    await self.stop_device(device)
                    await asyncio.sleep(loop_off)
            else:
                await self.apply_levels(device, levels)
                if end_time is None:
                    return   # continuous: runs until replaced
                await asyncio.sleep(max(0, end_time - loop.time()))

            await self.stop_device(device)
        except asyncio.CancelledError:
            pass   # replaced by a newer command; do not zero here

    # -- HTTP handling -------------------------------------------------------

    async def handle(self, body):
        command = body.get("command")

        if command == "GetToys":
            devices = self.devices()
            if not devices:
                return no_toys()
            toys = {}
            for index, device in devices.items():
                toy_id = f"bp{index}"
                name = (device.name or "device").split(" ")[0].lower()
                toys[toy_id] = {
                    "id": toy_id,
                    "name": name,
                    "nickName": device.name or "",
                    "battery": 100,
                    "version": "",
                    "status": "1",
                }
            # The real app double-encodes toys as a JSON string
            return ok({
                "data": {
                    "toys": json.dumps(toys),
                    "platform": "bridge",
                    "appType": "remote",
                }
            })

        if command == "Function":
            targets = self.resolve_targets(body.get("toy"))
            if not targets:
                return no_toys()

            levels = parse_action_string(body.get("action", ""))
            time_sec = float(body.get("timeSec") or 0)
            loop_on = float(body.get("loopRunningSec") or 0)
            loop_off = float(body.get("loopPauseSec") or 0)

            for device in targets:
                # Newest command replaces whatever is scheduled — the same
                # semantics as real Lovense hardware
                self.cancel_timer(device)
                if levels is None:                       # action == "Stop"
                    await self.stop_device(device)
                else:
                    task = asyncio.create_task(
                        self.run_schedule(device, levels, time_sec, loop_on, loop_off)
                    )
                    self.timers[device.index] = task
            return ok()

        if command == "StopAll" or command == "Stop":
            for device in self.devices().values():
                self.cancel_timer(device)
                await self.stop_device(device)
            return ok()

        return error(f"Unknown command: {command}")


# ---------------------------------------------------------------------------
# HTTP front end
# ---------------------------------------------------------------------------

def make_app(backend):
    async def handle_command(request):
        try:
            body = await request.json()
        except Exception:
            return error("Body was not valid JSON")
        print(f"\n--- POST /command ---")
        print(json.dumps(body))
        return await backend.handle(body)

    app = web.Application()
    app.router.add_post("/command", handle_command)
    return app


# Fixed: the mod's "Local Bridge" backend always connects here
PORT = 20010


async def run_bridge(mode, param, stop_event):
    """Runs one bridge session until stop_event is set."""
    if mode == "buttplug":
        backend = ButtplugBackend(param)
    else:
        backend = RelayBackend(param)

    await backend.start()

    runner = web.AppRunner(make_app(backend))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()
    print(f"Bridge listening on http://127.0.0.1:{PORT}/command ({mode} mode)")
    print("In-game: Mod Options -> Emperor's Touch -> Toy Backend -> Local Bridge")

    await stop_event.wait()
    await backend.shutdown()
    await runner.cleanup()

    # Give cancelled tasks (connection loop, websocket internals, timers)
    # a tick to unwind so the loop closes clean
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    print("Bridge stopped.")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

import queue
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext


class QueueWriter:
    """Redirects print() into the UI log (and the real console)."""

    def __init__(self, log_queue, real):
        self.queue = log_queue
        self.real = real

    def write(self, text):
        if text:
            self.queue.put(text)
        if self.real:
            self.real.write(text)

    def flush(self):
        if self.real:
            self.real.flush()


class BridgeUI:
    def __init__(self, root):
        self.root = root
        self.loop = None
        self.thread = None
        self.stop_event = None

        root.title("EmperorsTouch Bridge")
        root.resizable(True, False)

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)

        # -- mode checkboxes + inputs -------------------------------------
        self.use_buttplug = tk.BooleanVar(value=True)
        self.use_relay    = tk.BooleanVar(value=False)

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Checkbutton(
            row, text="Intiface / buttplug.io", variable=self.use_buttplug,
            command=lambda: self._exclusive(self.use_buttplug),
        ).pack(side="left")
        self.intiface_entry = ttk.Entry(row)
        self.intiface_entry.insert(0, "ws://127.0.0.1:12345")
        self.intiface_entry.pack(side="right", fill="x", expand=True, padx=(12, 0))

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Checkbutton(
            row, text="Phone relay (Lovense Connect)", variable=self.use_relay,
            command=lambda: self._exclusive(self.use_relay),
        ).pack(side="left")
        self.relay_entry = ttk.Entry(row)
        self.relay_entry.insert(0, "192.168.1.100")
        self.relay_entry.pack(side="right", fill="x", expand=True, padx=(12, 0))

        # -- start/stop + status ------------------------------------------
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(10, 4))
        self.start_button = ttk.Button(row, text="Start", command=self.toggle)
        self.start_button.pack(side="left")
        self.status = ttk.Label(row, text="Stopped")
        self.status.pack(side="left", padx=12)

        # -- log ------------------------------------------------------------
        self.log = scrolledtext.ScrolledText(frame, height=14, width=72, state="disabled")
        self.log.pack(fill="both", expand=True, pady=(6, 0))

        self.log_queue = queue.Queue()
        sys.stdout = QueueWriter(self.log_queue, sys.__stdout__)
        sys.stderr = QueueWriter(self.log_queue, sys.__stderr__)
        self._poll_log()

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _exclusive(self, chosen):
        # The two modes are mutually exclusive; checking one clears the other
        if chosen is self.use_buttplug and self.use_buttplug.get():
            self.use_relay.set(False)
        elif chosen is self.use_relay and self.use_relay.get():
            self.use_buttplug.set(False)

    def _poll_log(self):
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", text)
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)

    # -- lifecycle ----------------------------------------------------------

    def toggle(self):
        if self.thread and self.thread.is_alive():
            self.stop()
        else:
            self.start()

    def start(self):
        if self.use_buttplug.get():
            mode, param = "buttplug", self.intiface_entry.get().strip()
        elif self.use_relay.get():
            mode, param = "relay", self.relay_entry.get().strip()
        else:
            print("Select a mode first.")
            return
        if not param:
            print("Fill in the address field for the selected mode.")
            return

        self.loop = asyncio.new_event_loop()
        self.stop_event = None

        def runner():
            asyncio.set_event_loop(self.loop)
            self.stop_event = asyncio.Event()
            try:
                self.loop.run_until_complete(run_bridge(mode, param, self.stop_event))
            except Exception as e:
                print(f"Bridge error: {e}")
            finally:
                self.loop.close()

        self.thread = threading.Thread(target=runner, daemon=True)
        self.thread.start()
        self.start_button.configure(text="Stop")
        self.status.configure(text=f"Running ({mode})")

    def stop(self):
        # Idempotent: safe to call after the bridge already stopped
        if self.thread and self.thread.is_alive():
            if self.loop and self.stop_event and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.stop_event.set)
            self.thread.join(timeout=5)
        self.thread = None
        self.loop = None
        self.stop_event = None
        self.start_button.configure(text="Start")
        self.status.configure(text="Stopped")

    def on_close(self):
        try:
            self.stop()
        finally:
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    BridgeUI(root)
    root.mainloop()
