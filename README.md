# EmperorsTouch Bridge

Optional companion for the Emperor's Touch Darktide mod. The mod works
out of the box with **Lovense Remote on the same PC** — you only need this
bridge for:

- **buttplug.io / Intiface Central** — control any Buttplug-supported
  device (non-Lovense hardware).
- **Lovense Connect on a phone** — including its simulated toys. The phone
  app's responses are rejected by Darktide's HTTP client, so the bridge
  relays and fixes them.

## Setup

```
pip install aiohttp buttplug-py
```

(`buttplug-py` is only needed for buttplug mode.)

## Usage

```
python bridge.py
```

A small window opens. Pick a mode, fill in its address field, press
**Start**:

- **Intiface / buttplug.io** — start Intiface Central first (server on
  `ws://127.0.0.1:12345` by default, which is pre-filled) with your
  devices connected.
- **Phone relay (Lovense Connect)** — enter your phone's LAN IP (shown in
  the Lovense Connect app).

The log pane shows every command the game sends — the first place to look
when something doesn't buzz.

**In-game**: Mod Options → Emperor's Touch → **Toy Backend** → *Local
Bridge*. Press **Get Toys** in the toys view (F10). The bridge always
listens on port 20010 (the mod expects exactly that).

## How buttplug mode maps commands

- Toy ids are `bp<device index>`; device names come from Intiface.
- Lovense action strengths (Vibrate 0–20 etc.) map to Buttplug 0.0–1.0.
- Vibrate/Oscillate/Suction/Pump/Thrusting/Fingering/Depth go to the
  device's best-matching scalar actuator (falling back to the first one);
  Rotate uses rotary actuators; Stroke uses linear actuators (approximate).
- Buttplug has no timed commands, so the bridge emulates `timeSec` and
  loop on/off cycles itself. A new command for a device replaces its
  running one — the same semantics as real Lovense hardware.
- If Intiface isn't running the bridge reports "no toys" and reconnects
  automatically every 5 seconds.

## Notes

- The bridge listens on 127.0.0.1 only — nothing on your network can
  reach it.
- Keep the console open; it logs every command, which is the first thing
  to check when something doesn't buzz.
