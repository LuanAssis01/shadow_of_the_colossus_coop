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

This is an unofficial fan research project, not affiliated with or endorsed by Sony or the game's
rights holders.
