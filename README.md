# Shadow of the Colossus co-op (research)

Research project to add online multiplayer to *Shadow of the Colossus* (PS2, SCUS-97472, NTSC-U v1.00)
running on PCSX2, without modifying the game image: a second Wander is spawned, driven like a puppet
from state received over UDP, with code injected live through PCSX2's PINE interface.

Start with [MULTIPLAYER.md](MULTIPLAYER.md): memory layout, function addresses, tools, status, measurements
and the list of dead ends (so they are not repeated).

## What is NOT in this repository

No game content and no third-party software is included or may be added:

- the game image, the PS2 BIOS, RAM dumps, extracted game files, savestates, screenshots
- the symbol table extracted from the game binary (`work/symbols.tsv`, regenerate it with
  `tools/xff_symbols.py` from your own copy of the game)
- Ghidra, the Emotion Engine plugin, REA (patched to accept MIPS ELF) and cb2util: download them yourself

You need your own legally obtained copy of the game and BIOS. Addresses in the code only fit the NTSC-U
v1.00 build.

## Layout

- `tools/` Python tooling (PINE client, hook installer, spawner, puppet script, UDP bridge, stage guard, Windows launcher `coop_local.py`, helpers)
- `work/scen*.json` input scenarios, `work/walk*.bin` recorded movement samples used by the tests

## Before a co-op session

The tools require Python 3 and `numpy`, plus your own PCSX2/PINE, Ghidra and REA setup described in
[`MULTIPLAYER.md`](MULTIPLAYER.md). Run the read-only preflight against the selected PINE instance before
starting a bridge:

```sh
python tools/doctor.py
python tools/doctor.py --ready
```

The first command checks the expected game build, PINE and the shared mailbox map. `--ready` additionally
requires installed, advancing frame hooks and a playable world. It never writes to the emulator.

Pure-Python regression tests do not need PCSX2 or game assets:

```sh
python -m unittest discover -s tests -v
```

The UDP bridge uses a versioned `SOTC` v1 envelope and still reads existing raw recordings/test payloads.

This is an unofficial fan research project, not affiliated with or endorsed by Sony or the game's
rights holders.
