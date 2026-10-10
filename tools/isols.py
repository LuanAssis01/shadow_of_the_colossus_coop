import struct, sys
iso, out = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
f = open(iso, 'rb')
def rd(lba, n): f.seek(lba * 2048); return f.read(n)
pvd = rd(16, 2048)
assert pvd[1:6] == b'CD001'
root = pvd[156:190]
rlba, rlen = struct.unpack('<I', root[2:6])[0], struct.unpack('<I', root[10:14])[0]
def entries(lba, ln):
    d = rd(lba, ln); i = 0
    while i < len(d):
        l = d[i]
        if l == 0: i = (i // 2048 + 1) * 2048; continue
        e_lba = struct.unpack('<I', d[i+2:i+6])[0]; e_len = struct.unpack('<I', d[i+10:i+14])[0]
        fl = d[i+25]; nl = d[i+32]; name = d[i+33:i+33+nl].decode('latin1')
        i += l
        if name in ('\x00', '\x01'): continue
        yield name, e_lba, e_len, fl
for n, l, s, fl in entries(rlba, rlen):
    print(f"{n:30} lba={l:8} size={s:12} {'DIR' if fl & 2 else ''}")
if out:
    for n, l, s, fl in entries(rlba, rlen):
        if n.split(';')[0] == 'SYSTEM.CNF' :
            print(rd(l, s).decode('latin1'))
import os
if out:
    os.makedirs(out, exist_ok=True)
    for n, l, s, fl in entries(rlba, rlen):
        base = n.split(';')[0]
        if base in ('SCUS_974.72','GAMECORE.XFF','KERNEL.XFF','MANAGER.XFF','STARTUP.XFF','SYSTEM.CNF'):
            open(os.path.join(out, base), 'wb').write(rd(l, s))
