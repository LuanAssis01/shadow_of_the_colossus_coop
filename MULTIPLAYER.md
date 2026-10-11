# Shadow of the Colossus (SCUS-97472, NTSC-U v1.00) — multiplayer research

Goal: full online multiplayer (players, colossi, health, world state) on PCSX2,
using REA (patched for MIPS) + Ghidra + the Emotion Engine plugin for analysis
and PINE for live memory access and code injection.

## Tooling (everything under `tools/`)

| Path | What |
|---|---|
| `ghidra_12.1.4_PUBLIC/` | Ghidra 12.1.4 + `ghidra-emotionengine-reloaded` v2.1.38 (R5900) |
| `rea/` | REA 6.2.0 source, patched to admit MIPS ELF (`e_machine` 8) |
| `rea-ps2` | wrapper: runs patched REA with `GHIDRA_INSTALL_DIR` set |
| `rea_session.py` | persistent REA MCP session (`serve TARGET.elf` / `q TOOL JSON`) |
| `dec.sh` | batch decompile by address or symbol, with recovered names substituted |
| `pine.py` | PINE client (read/write EE memory, savestates); socket in the Flatpak runtime dir |
| `dump_ee.py` | dump 32 MiB EE RAM, wrap as R5900 ELF; `--slice` for a sub-range |
| `memscan.py` | differential float scanner (snapshots while the player moves) |
| `xff_symbols.py` | recover the XFF modules' `Elf32_Sym` tables from RAM → `work/symbols.tsv` |
| `chars.py` | list live character objects with kind and position |
| `mips.py` | tiny R5900 assembler |
| `hook.py` | install per-frame hook; teleport and generic EE function calls |
| `spawn.py [K]` | spawn a complete extra Wander K = 0..3 (player, sheath, hair, 2 alias bodies) |
| `puppet.py [CLONE [SLOT]]` | swap a clone's script for an injected puppet script driven via PINE (one state block per slot) |
| `netbridge.py` | UDP state bridge (echo / host / join / recv / sendfile), 30 Hz, multi-peer star relay, disconnect watchdog |
| `snap.py` | "screenshot" through a PINE savestate (`Screenshot.png` + RAM); Wayland blocks real grabs |
| `disasm.py` | tiny R5900 disassembler over live RAM |
| `cb2util/` + `cbdec` | CodeBreaker v7 decryption (used to bootstrap the HP/grip addresses) |

PCSX2: Flatpak `net.pcsx2.PCSX2` 2.8.2, PINE enabled (backup of the original
ini: `PCSX2.ini.bak-before-multiplayer`). Launch with
`flatpak run --filesystem="$PWD" net.pcsx2.PCSX2 "<iso>"`.

Typical analysis loop:

```sh
python3 tools/dump_ee.py work/dumps/game1                       # live RAM
python3 tools/dump_ee.py --slice work/dumps/game1.bin 0x1000000 0x1600000 work/dumps/game1_core.elf
python3 tools/rea_session.py serve work/dumps/game1_core.elf &  # ~80 s import, then fast
tools/dec.sh GAMEUTIL_get_player_obj setPlayerHp
```

## Memory layout facts

- Boot ELF `SCUS_974.72` is only a loader. Game code lives in XFF modules
  (`GAMECORE.XFF`, `KERNEL.XFF`, `MANAGER.XFF`) loaded at runtime to
  `0x01100000–0x01500000`; their symbol tables stay in RAM
  (`0x01291638` → 2746 syms, `0x0146cc80` → 4639 syms). 6477 original names.
- Free RAM used by the hook: `0x000F0000` (stub), `0x000F8000` (mailbox);
  `0x95064–0x100000` was zero in every snapshot.
- PINE batches: ≤ 40000 commands per message on PCSX2 2.8.2.

### Player state (module globals)

| Address | Meaning | Accessors |
|---|---|---|
| `0x012DE264` | current HP (float, 100 = full) | `getPlayerHp` `setPlayerHp` `addPlayerHp` |
| `0x012DE260` | current grip | `getPlayerGrip` `setPlayerGrip` `addPlayerGrip` |
| `_gameFWorkChk(0xb)` | max HP (table at `[0x01465bb0]`) | `getPlayerMaxHp` |
| `0x01465940` / `0x01465944` | group handles: player / horse | `SCRGetFirstObj` |

### Objects

- Layout object: `+0x04` class/vtable, `+0x08` layout type byte
  (9 character, 0xd, 0x16, 0xb script, 10 …).
- Character class = `obj + 0x90` (`GetCharacterClass`); `class + 0x134` →
  position vec4 (for characters it is `obj + 0xA0`); `class + 0x370` short = kind
  (2 = player Wander, 3 = Agro, 5 = birds, 11 = Mono / accessories).
- Character objects have class word `0x01206DD8`. Heap addresses change per load;
  find them with `chars.py`.

### Useful functions

| Function | Address | Use |
|---|---|---|
| `DebugSetObjectGeometry(obj, quat*, pos*)` | `0x01341218` | teleport any object (verified in game) |
| `gameflowSetPos` | `0x013522e0` | user-facing pos: x,z negated + Euler YXZ deg → quat |
| `GetQuaternionByDegEulerYXZ` | `0x001bda70` | build rotation |
| `GAMEUTIL_get_player_obj` | `0x01343450` | player object |
| `graphFrameFinish` | `0x01189c58` | called once per frame from `0x013463f8` (hook site) |
| `stagemanager_loadstagecore(grp, seg, -1, 0)` | `0x013664b8` | refcounted segment load; player = seg `0xb6` |
| `initstage` / `initlayout` / `FUN_013600a8` | `0x013d0318` / `0x01360548` / `0x013600a8` | instantiate layout entries (skips entries whose `+0x48` is set) |
| `SetCharacterObjDemoMotionMode` | `0x011b7cb0` | put a character in demo (animation-driven) mode |
| `RequestCharacterClassMotionChange` | `0x011cce30` | request an animation |

## Status

- [x] Toolchain: Ghidra + EE plugin + REA (MIPS patch) + PINE.
- [x] Live RAM dump → ELF → decompile with original names.
- [x] Player HP/grip, player/horse objects and positions identified.
- [x] Per-frame code injection via PINE; calling game functions (teleport verified).
- [x] Second Wander instance, **complete and visible** (solved 2026-10-09). The Wander is
      5 layout objects: segment 0xb6 (player + sheath script), 0xb8 (hair char,
      nico_parentSync), **0xb7 and 0xb9 (alias objects drawing torso/arms/legs/cape)**. The
      earlier clone showed only sword+head because the 0xb7/0xb9 alias objects were never
      cloned. `spawn.py` now clones every segment from the globals at 0x01465e00..e10.
- [x] Avatar driven by a puppet script (2026-10-09, user-verified in echo mode: animations
      copy correctly, clone walks and jumps). `puppet.py` replaces the clone's script
      (script class obj+0x20: +0x38 restart flag, +0x3c info{?,entry}) with a loop reading a
      state block at 0xF8200 (PINE). Per frame, in this order:
        1. flag TELEPORT: DebugSetObjectGeometry (pos+quat; it calls ResetCharacterObj, which
           wipes the motion) ; else flag POSE: SetLayoutClassRootQuaternion only;
        2. motion: if class+0x1c0 != wanted: ForceAcceptMotion($f12=start frame, class, motion)
           + InitAnimationClass(class+0x1c0, motion) + ResetCharacterClassAnimeFrameTableInfo;
        3. frame: class+0x1cc = old class+0x1c8; class+0x1c8 = frame (prev frame drives root motion).
      Requirements: class+0xd90 = 3 on the clone (else GetMainAnimation -> FUN_011cc260 re-derives
      the motion from the absent pad). Never restart a coroutine that already is the puppet
      (hangs the game logic; make_puppet is idempotent). Motion ids are indices into the
      character's motion table (class+0x2ac, 0xdc stride), portable between machines.
      Position: the engine recomputes it every frame from a hidden base + the animation's
      root motion, so writing obj+0xa0/0x550/... does NOT stick (neither do the resets in
      ResetCharacterObj one by one). Only the teleport sets the base. So `netbridge.py` uses
      dead reckoning: the animation walks the clone, a teleport fixes drift > 2.5 (horizontal)
      or > 0.5 (vertical; jumps are physics, not root motion). Teleporting every frame froze
      the game when the frame reached the motion's end; more teleports also add horizontal pops.
      Replay metrics (60 s, 90 m recorded walk): mean error ~0.7, 6 teleports, no freeze.
      Hidden-base hunt (negative): writes to obj+0xa0/0xb0/0xd0/0x550/0x560/0x590/0x1140/0x11e0,
      bone matrices (lc+0x15c/0x160/0x2d8), class+0x744/0x51c, and each partial reset of
      ResetCharacterObj all revert within a frame. The recompute is ExecCharaGeometry (0x011b9c70)
      -> GetCharaRootDynamics (0x011d4340, mode pointer at class+0x438; the engine rewrites it
      every frame) -> CalcRootFitting. A write only "sticks" while the clone is far away
      (outside the active area, geometry exec skipped), not usable. Don't repeat these.
      **Jump/fall stepping: SOLVED (2026-10-10), no hidden base needed.** The clone runs the
      engine's own gravity/ground physics (obj+0xc4 = vertical velocity m/s, class+0xab0 / 0x914
      fall speed); what broke it was ResetCharacterObj inside every teleport zeroing that velocity,
      and the 0.5 vertical teleport limit re-triggering it. With TELEPORT_HEIGHT=1.0 a single
      teleport at takeoff lets the clone fall by itself: 6 m drop (local lifted by hook.teleport),
      clone-vs-local error ~0.03, 2 teleports; walk1.bin replay: error mean 0.38, 1 teleport.
      Without any vertical teleport the clone just stays on the floor (it is grounded, the -200 m/s
      "velocity" is gravity piling up against the floor). Test scripts used: record local+clone
      each tick, `fallcmp`-style (teleport the local player up with hook.teleport, compare y).
      Remaining: after a mid-air teleport the fall speed restarts from 0 (could write obj+0xc4
      from the remote's dy/dt); large jumps from a run were not tested in open ground.
      Test tools: `netbridge.py record FILE SEC` (store the local Wander), `replay FILE DX DZ`
      (drive the puppet from it and print error/roughness), `echo DX DZ` (live mirror). Use ONE
      PINE client at a time (a second reader stalled the bridge's writes). Emulator log:
      ~/.var/app/net.pcsx2.PCSX2/config/PCSX2/logs/emulog.txt. Recording: work/walk1.bin.
- [~] Net bridge (UDP) reading/writing state via PINE: `netbridge.py`. Local UDP path verified
      with `sendfile` -> `recv` (60 s recorded walk): tracking error mean 0.71, p95 0.81, also
      with 10% loss + 80 ms jitter/reordering (0.76 / 0.87). Packets carry a seq (old ones are
      dropped). Two real PCSX2 instances verified (host <-> join, both sending, each simulating a
      local walker from work/walk1.bin via SIM/SIM_START): tracking error mean 0.29 / 0.48.
      Second instance: `flatpak run --filesystem=$P --command=sh net.pcsx2.PCSX2 -c "export
      XDG_CONFIG_HOME=$P/work/pcsx2_b; exec /app/bin/pcsx2-qt ISO"` (flatpak overrides --env
      XDG_CONFIG_HOME, hence the sh wrapper); its ini has PINESlot=28012 and an absolute BIOS path;
      use PINE_SLOT=28012 for every tool (pine.py) and SSTATES=work/pcsx2_b/PCSX2/sstates (snap.py).
      **PCSX2 PINE serves ONE client at a time** (a second process just hangs until the first
      disconnects; this explained the earlier "stalls"). Everything that touches an emulator must
      live in one process: the bridge also runs the pad driver and the snapshots.
      Controller injection (verified): the logic hook (hook.py MB_PAD_*) overwrites player 1's pad
      state each frame before scripts run: button words at 0x01287a00 (held), +8 (push), +0xc
      (release), +0x10 (repeat) and the left stick bytes at +0x54 (0x7f = centre, y inverted).
      Button bits (swept one by one on a standing Wander, 2026-10-10; table in `pad.py` BUTTONS):
      0x80 jump (motions 89/91); 0x40 -> motion 453; 0x20 -> 511-513; 0x10 -> 44/46/63/57/58
      (screen blur); 0x08 and 0x04 open the MAP/pause screen (motions 253-255 / 32) which STAYS open
      after release (a scenario must send the "back" input or reload a state); 0x01, 0x02 and
      0x100..0x8000 do nothing on flat ground (context actions: grip, horse, camera...). The
      PS2 face-button names of the bits were NOT verified. The push edge must last >= ~3 game frames
      (`pad.press` does it). `PlayerControlButtonContext` at 0x014659a4 = action bit indices
      [15, 13, 4, 7, 5, 6, 3, 2, 1]. `pad.py` is the helper; `netbridge.py` runs a SCENARIO
      (JSON timeline of stick/hold/snap events) so a scripted player can drive each emulator.
      Coop test (two real emulators, two scripted players, host<->join): each side's clone showed
      the other's walk (motions 17/21/22/23), jump (91) and sword/other actions at the right
      times, tracking error mean 0.3-0.9 (p95 ~1.7), no freeze. Snapshots in work/snapsA|B.
      2026-10-10 additions (all verified live):
      * Disconnect / reconnect: a remote silent for DISCONNECT_SECONDS (3 s) is parked (puppet
        teleport to y+3000, nothing is drawn or collided, no freeze); when packets return (even from
        a restarted sender with seq 0) the first sample teleports it back ("peer back after 9.1s").
        NB: hook.teleport() on a puppeted clone does NOT stick (the puppet rewrites its state block
        every frame): move clones only through puppet.write_state(teleport=True).
      * More than two players: `spawn.py K` spawns clone K (0..3, ~2 s each; pointers at mailbox
        0x70 + K*0x20, `puppet.clone_address(p, K)`), puppet slot K per clone. Wire packet's last
        field is now `pid`: `host PORT` assigns pid 1,2,.. per source address, relays every client's
        state to the other clients stamped with its pid and sends its own state with pid 0; `join`
        maps each incoming pid to its own clone (`Remotes`). Tested: 2 fake senders (sendfile) ->
        2 independent clones; fake client socket received pid 0 and pid 1 relays; two real
        emulators host(B) <-> join(A) with scenarios: tracking error mean 0.36 (A) / 0.52 (B),
        1 / 10 teleports (B differs because the two test worlds stood at different ground heights).
      * Height-triggered teleports are rate limited (MIN_TELEPORT_INTERVAL 0.3 s) so a persistent
        ground-height disagreement cannot teleport on every packet (froze the game before).
      * Real two-person play (DualSense on A, GameSir on B; user reported the remote Wander looks
        buggy - "floating, physics and animations" - while it is normal on its owner's screen).
        New `TRACE=file.csv` (+ SNAPDIR, SSTATES) in netbridge logs every tick remote-target vs clone and
        screenshots persistent anomalies (M motion differs, U motion not in table, V dy>0.4, H dx>1).
        150 s of real play: motions matched in ~99% of ticks, no U, |dy| <= 0.35, no position pops;
        the only measured fault was a STATIC OFFSET: after a stop the clone stayed 0.4-1.3 m from the
        player (error < TELEPORT_DISTANCE was never corrected; B stood 1.27 m off for 50 s). Fixed with a
        "settle" teleport once the target has been still for 8 packets (SETTLE_ERROR 0.25, once per second
        at most): error while the target is still p50 0.39 -> 0.01. The screenshot-visible "floating" was NOT
        reproduced numerically; cloth/sheath of the clone seen from the front looked deformed in one
        capture (work/snapsB/trace_097_H.png) - unconfirmed. `netbridge echo` now honours DURATION.
      * Remote Wander looked "windy / floating / sliding" in real play (user report, 2026-10-10). Causes found:
        1. puppet wrote the position fields every frame; the engine recomputes position from its hidden
           base, so the object jumped by the tracking error and back and the cloth read it as violent
           wind (reproduced with a constant 0.8 error; gone once the writes were removed);
        2. physics-driven animations (51 climb: target +2.1 m/s, clone 0.0; 65: -1.5 vs +0.4) left the clone
           on the floor: root motion carries it horizontally only, a full teleport is the only setter and
           it resets motion/velocity/cloth (ResetCharacterObj, CallLayoutObjDebugResetFunc).
        Fix = FLAG_MOVE in puppet.py ("light move"): DebugSetObjectGeometry with those two functions
        temporarily patched to `jr ra` (restored right after, same script pass; same trick as spawn.py's
        allocator detour). It moves the base position and keeps motion, velocity and cloth. netbridge now
        uses it for every correction (MOVE_DISTANCE 0.35 horizontal, MOVE_HEIGHT 0.6, >=0.1 s apart, plus
        a settle once the target is still); full teleport only for the first sample / jumps > 5 m or 8 m.
        Measured: walk1 replay error mean 0.08 (was 0.37-0.51), synthetic 6 m climb at 2 m/s + descent
        vertical error mean 0.05-0.11 max 0.66-0.77, 6 m fall max 0.93 (a move also zeroes the fall speed:
        0.25 gave 1.9, so 0.6), two real emulators host/join: error mean 0.07 / 0.11, screenshots show
        the clone in the same pose and height as the local player while climbing a wall.
        Gotchas: (a) a screenshot (snap) blocks the tick loop ~1 s and the clone freezes mid-pose, which
        looks exactly like a bug -> `snap.AsyncSnap`, the scenario/tracer never block; (b) my first "pop at
        loop wrap" was an artefact of a synthetic frame feed, real wraps are fine; (c) a session started for
        N seconds ends: tell the user the end time (the clone only moves while the bridge runs).
      * NOT done: latency/NAT tests across real machines (needs a second computer); the clone does
        not collide or get grabbed (postponed to the colossus phase); relay topology is a star (host
        forwards), no mesh; 3 real emulators never ran at once (relay logic only tested with fakes).
- [ ] Colossus state sync (object, HP, weak points, grab points). Recon so far (static, from
      the loaded core; no boss fight state captured yet):
      * boss table: `PTR_s_DEFAULT_0156b440` -> [4] = array of 0xec-byte entries; active boss index
        `_gameWorkChk(8)` (`bossStatGetActiveBoss` 0x0134a4c0, `bossStatSetActiveBoss` 0x01349518,
        defeat flags `bossStatSetDeadBoss/GetDeadBoss/ChkDeadBoss`).
      * boss object: first object of group 0x01465948 (`GAMEUTIL_get_boss_obj` 0x013439f0).
      * per-boss life: `GetSCRCharaAIWorkPtr(obj)` -> work `*w`: part count at `*w+0x54`; part defs
        at `*(*w+0x10)` (0x58 bytes each, part id/weak id keys); CURRENT life floats at
        `*(*w+0xe8)+0x24` + 8*part (max in the def: parts +0x1c word 7, weak points +0x1c).
        `getBossPartsLifeRatio` 0x013784f8, `getBossWeakLifeRatio` 0x01378648.
      * Plan (host-authoritative): host sends boss skeleton pose + life; client forces its
        boss to that pose and disables its AI. Needs a savestate inside a colossus fight and
        pad automation (write the pad buffer `padManagerData` 0x01288998.. from the hook) to
        reach one without a human.
- [ ] World/progress sync (stage loads, cutscenes, deaths).

Two real controllers (2026-10-10): DualSense = SDL-0 -> instance A (default config, PINE 28011);
GameSir Cyclone 2 = SDL-1 -> instance B (work/pcsx2_b ini, `Pad1 = SDL-1/...`, backup
`PCSX2.ini.bak-before-pad-sdl1`). Both ini files must keep that order and the DualSense must be
connected first (SDL assigns ids by connection order). `padwatch.py` shows which emulator a pad
drives (verified: each pad moves only its own instance). Real play = no SCENARIO, MB_PAD_EN stays 0.
Start/Select open the map/Options menu: the game then freezes character scripts (the remote clone
stops updating on that machine until the menu is closed) - not a bridge bug; after pad mashing, reload
state 4 + `hook.py install` + `spawn.py 0`. Live session: `PINE_SLOT=28012 DURATION=600 netbridge.py
host 5020` and `DURATION=600 netbridge.py join 127.0.0.1 5020` (a client that restarts with a new port
gets a new pid and reuses the parked clone).

Savestates: slot 1 = before any hook; 2 = hook installed, shrine; 4 = hook installed, no clone;
8 = complete clone (spawn.py) next to the real Wander; 9 = scratch (snap.py).

## Windows setup and session 2026-10-10 (night): horse, deaths, memory

**Windows setup.** PCSX2 2.9.85 serves PINE over TCP (`127.0.0.1:<slot>`, `pine.py` does that on `nt`). It has no
`-settings` switch, so each instance is a portable copy of the emulator folder: `work/pcsx2_a/emu` and
`work/pcsx2_b/emu` (each with `portable.txt`, `inis/PCSX2.ini` with `EnablePINE`, slots 28011 / 28012,
controllers SDL-0 / SDL-1 plus keyboard, instance B muted, savestates/memcards/logs in its own folder).
`tools/coop_local.py` starts both, loads the clean shrine state (slot 1, saved once with no hook) and runs
host (B) / client (A); `--stop` closes everything. `socket.ioctl` cannot set SIO_UDP_CONNRESET: `netbridge.py`
uses `WSAIoctl` through ctypes (without it a peer that vanished kills the other bridge).

**Horse.** The horse is segment `*(0x01465E14)` (0xba): the horse character (kind 3) and a helper object. It is
cloned like the Wander (`spawn.py horse K`, pointers at mailbox 0x120 + K*0x10) and driven by the same puppet
script (puppet slots 4..7). The wire packet is now 84 bytes: the 44-byte Wander sample + a horse sample
(`HORSE`, flag bit0 = the sender has a horse). The horse clone is spawned lazily, when the first packet with a
horse arrives, and parked when the sender has none.

**Why the game froze after dying (found, fixed).** Not the stage reload: the *game heap is tiny*. `0x1E0BF0` is
its free-byte counter: ~380 KB free at the shrine, a Wander clone costs ~62 KB and a horse clone ~45 KB, the
biggest single request is 77 KB (`0x12d14`). The engine has no out-of-memory path for these allocations: a
failed one either spins forever in `initlayout`'s dependency loop (a child waits for a parent that never
spawned) or crashes in `initParticleObject` (null +0x108). Deleting the clones when the game restarts the
player (death at the shrine) and spawning them again lost ~60 KB per cycle until a spawn failed (4th death).
Fixes: clones now SURVIVE a restart (death at the shrine keeps the segments' assets; 15 deaths in 10 minutes
tested, heap stable); `spawn.heap_fits()` refuses a spawn unless the free count and a probe block fit
(`SpawnRefused`, retried later); the spawn routine checks every entry's preconditions before touching anything.
`guard.py` (EE-side) marks every clone with a delete handler and deletes all clones before
`stagemanager_unloadstagecore` runs (a real stage unload frees the assets they draw with). Untested: a
real death in the open field (needs a savestate there); emulating it with `gStatUnloadHorseChara` crashes the
game for an unrelated reason (the local horse's scripts keep using it).

Useful facts: `isysRequestDeleteObj` (0x001C7548) is the engine's own delete; `iosMallocAlignNoCheck(heap, size,
align)` returns NULL instead of the recovery path; main heap = 0x001E0C88; `PINE` allows one client, so
tests that must poke the game while the bridge runs use env hooks (`DEBUG_KILL_EVERY`, `DEBUG_AUTO_RETRY`,
`DEBUG_UNLOAD_HORSE_AT`). A freeze can be diagnosed from a savestate: extract `PCSX2 Internal Structures.dat`
(`cpuRegs` tag +32: GPRs, PC) and disassemble `eeMemory.bin`.

**Colossus table** (`warp.py list`): `*(0x0156B440+0x10)` + 0xEC*i, +0x51 area, +0x64 arrival (relative to
`YattsukeGetSeamlessAreaCenter(area)`, x and z mirrored), +0xBC max HP, +0xC0 max grip. Colossus 0 (Valus)
arrival = (-305, 3.8, 271.5), reachable on foot in the same coordinate space as the shrine. Teleporting there
does not stream the terrain in by itself (`warp.py boss N` holds the player in the air for 20 s; not yet verified).

**Open-field death (tested 2026-10-11).** Savestate 2 (the player at colossus 0's arrival point, made with
`warp.py boss 0`) + `DEBUG_KILL_EVERY` in the echo bridge: the first death happens in the field, the retry
reloads the shrine; the clones survive it and the bridge resumes ("local player is back"), 5 deaths, no freeze.

**Colossus recon (`boss.py`, `warp.py`).** `warp.py boss 0` holds the player 25 m above the arrival point for 20 s
while the terrain streams in, then drops it: it works (collision around the arrival point only; the arena
behind a wall at z ~ 298 is not reachable by teleport, the player falls through). The colossus object exists
and is loaded (kind 4, 19 parts, ids 0x160..0x1d0) but is dormant: its AI (`updateScriptDataSystem`
interpreter) only starts when the game's event script runs `BootBoss` (= `bossStatStartDemo`); setting boot flag 71
(`_gameFlagSet`) or calling `bossStatStartDemo` by hand does nothing, and life values are 0 until the fight
starts. Blind navigation with the pad is camera-relative and the first corridor is a dead end, so a real fight
needs a human-made savestate (walk/ride to the colossus once, save). Time Attack is not reachable (no
memory card: the game goes straight into the opening).

**Next (not done):** boss sync. Plan unchanged (host-authoritative pose: motion index + frame + root transform
puppeted like the Wander, life merged with min() per part, defeat triggered on both); needs a savestate at a
colossus first. Also: field-death test, 3+ players, latency/NAT between two machines.


## Shared colossus fight (2026-10-11)

State 2 of both instances is a real colossus-0 encounter saved by the user. Findings and what is implemented:

* The colossus is an ordinary character object (kind 4). The boss table's "life" is really **damage taken** per
  part: `*(*(work)+0xE8) + 0x24 + 8*i` (0 at the start), max in the part definition (+0x1C): `getBossPartsLifeRatio`
  = (max - damage) / max. Damage only grows, so two machines merge it with a per-part **max**.
* Animation id: word +8 of the motion descriptor (offset in the loaded animation file, equal on every machine);
  descriptor address = `table - [table+8] + id` with `table = *(class+0x2ac)`. The index relative to the table
  is NOT stable for colossi (several tables).
* `bosssync.py`: an EE routine called every frame from the logic hook (new `MB_FRAME_CB` in `hook.py`) forces the
  host's motion, frame and rotation on the client's colossus and moves it (light move) when it drifts; the local AI
  keeps running (its damage bookkeeping works on the pose that is shown). Whoever has the colossus running on its
  own AI is in charge (host wins ties); a mirrored colossus counts as not awake. Verified: one instance with a
  +60 m position offset (error 0.25 m), two instances (client mirrors host and vice versa, error < 1 m), damage
  exchanged in both directions (`DEBUG_DAMAGE_AT`).
* `barrier.py`: the encounter starts when an event script runs `BootBoss = bossStatStartDemo` (called once, then
  `gameModeSet(1)` .. `gameModeCls(1)` for the intro cutscene; measured with `probe.py`). A detour makes that script
  wait (yielding with `scrRun`, the world keeps running) until the peer is also waiting / already fighting or
  `BOSS_BARRIER_SECONDS` (15) pass, so both players get the cutscene together. Verified with two scripted players.
* Traps: a savestate carries the hook/guard code of the build that saved it, so the bridge rewrites them once per
  session; the colossus sits at (-267, 8, 316) (a marker) until woken, then the object follows the body; pad
  injection is camera-relative. `DEBUG_*` environment variables inject faults for tests.
* Not done: cutscenes other than the encounter start (death cutscene of the colossus, ending...), pulling the
  other player to the arena, colossus death handled by the merged damage but untested (needs a real kill),
  more than two players on one colossus (pose only from the host / the player whose AI runs it).


## STATUS (2026-10-11) - done and missing

Done and verified (two PCSX2 instances on one Windows PC, `python tools/coop_local.py`):
- [x] Windows setup: portable PCSX2 copies per instance, PINE over TCP, launcher with clean/boss savestates.
- [x] Second Wander (clone) driven by the remote player, light moves, settle, disconnect parking.
- [x] Horse clone (visible, synced), lazy spawn, heap-safe spawn (`heap_fits`), delete guard on stage unload.
- [x] Deaths: clones survive restarts, bridge pauses/resumes (15 deaths in a row, also from the open field).
- [x] Colossus: pose mirror (motion id + frame + rotation + position) with the local AI running, damage merged
      with max in both directions, "who runs the AI is in charge" arbitration.
- [x] Encounter start barrier (BootBoss): both players get the intro cutscene together (or after 15 s).
- [x] Tools: boss.py, bosssync.py, barrier.py, guard.py, warp.py, probe.py, bosstest.py, coop_local.py.

Missing / unverified:
- [ ] Verify by eye with two people (pose smoothness, horse, hits on the mirrored colossus register damage,
      the client takes no phantom hits from its own AI while mirroring).
- [ ] Colossus death: merged damage should trigger the death sequence on both machines; needs a real kill.
- [ ] Other cutscenes: colossus death/ending/other demos are per machine (no barrier); only the encounter start
      is aligned. Idea: detours on `gameModeSet(1)` / `demoManStart` with the same wait-for-peer barrier.
- [ ] Pull the other player to the arena when one starts an encounter (regroup teleport, needs the terrain-streaming
      hold trick of `warp.py`).
- [ ] World progress sync (game flags `_gameFlagSet/_gameFlagChk`, dead-boss flags, checkpoints, deaths of the
      colossus on both save files).
- [ ] 3+ players on one colossus; latency/NAT/VPN test between two real machines; mesh instead of star relay.
- [ ] The pose is forced every game frame from 30 Hz packets (some stutter expected); interpolation not done.
