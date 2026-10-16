"""Locate MSVC RTTI vtables for given class names in a PE32 image."""
import struct, sys
path = sys.argv[1]; names = sys.argv[2:]
data = open(path, 'rb').read()
pe = struct.unpack_from('<I', data, 0x3c)[0]
nsec = struct.unpack_from('<H', data, pe + 6)[0]; optsz = struct.unpack_from('<H', data, pe + 20)[0]
base = struct.unpack_from('<I', data, pe + 24 + 28)[0]
secs = []; off = pe + 24 + optsz
for i in range(nsec):
    vsz, va, rsz, raw = struct.unpack_from('<IIII', data, off + 8); off += 40
    secs.append((base + va, rsz, raw))
def f2va(o):
    for v, r, raw in secs:
        if raw <= o < raw + r: return v + o - raw
def va2f(va):
    for v, r, raw in secs:
        if v <= va < v + r: return raw + va - v
def u32(va): return struct.unpack_from('<I', data, va2f(va))[0]
for name in names:
    i = data.find(name.encode() + b'\0')
    td = f2va(i) - 8  # TypeDescriptor: vfptr, spare, name
    tdb = struct.pack('<I', td)
    cols = []
    j = 0
    while True:
        j = data.find(tdb, j)
        if j < 0: break
        colva = f2va(j) - 12
        if colva and u32(colva) == 0: cols.append(colva)
        j += 1
    for col in cols:
        cb = struct.pack('<I', col); k = data.find(cb)
        while k >= 0:
            vt = f2va(k) + 4
            ents = []
            for n in range(40):
                e = u32(vt + 4 * n)
                if not (0x401000 <= e < 0x4bcc00): break
                ents.append(hex(e))
            print(name, 'COL', hex(col), 'vtable', hex(vt), ents)
            k = data.find(cb, k + 1)
