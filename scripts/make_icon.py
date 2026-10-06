"""Draw a small original shield/energy icon using only the Python standard library."""
import struct
import zlib
from pathlib import Path

SIZE = 256
TARGET = Path(__file__).resolve().parents[1] / 'custom_components/senec_marstek_gate/brand/icon.png'


def polygon(x, y, points):
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


shield = [(128, 43), (200, 70), (192, 158), (166, 195), (128, 218),
          (90, 195), (64, 158), (56, 70)]
inner = [(128, 55), (188, 78), (180, 155), (158, 184), (128, 204),
         (98, 184), (76, 155), (68, 78)]
bolt = [(140, 79), (101, 135), (126, 132), (112, 179), (160, 115), (136, 119)]
scanlines = bytearray()
for y in range(SIZE):
    scanlines.append(0)
    for x in range(SIZE):
        if polygon(x, y, bolt):
            color = (248, 192, 62, 255)
        elif polygon(x, y, inner):
            color = (18, 58, 75, 255)
        elif polygon(x, y, shield):
            color = (125, 217, 202, 255)
        elif (x - 128) ** 2 + (y - 128) ** 2 <= 124 ** 2:
            color = (13, 36, 55, 255)
        else:
            color = (0, 0, 0, 0)
        scanlines.extend(color)
TARGET.parent.mkdir(parents=True, exist_ok=True)
TARGET.write_bytes(b'\x89PNG\r\n\x1a\n'
                   + chunk(b'IHDR', struct.pack('>IIBBBBB', SIZE, SIZE, 8, 6, 0, 0, 0))
                   + chunk(b'IDAT', zlib.compress(bytes(scanlines), level=9))
                   + chunk(b'IEND', b''))
print(TARGET, 'bytes', TARGET.stat().st_size)
