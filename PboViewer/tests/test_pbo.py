import hashlib
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pboviewer import pbo as P  # noqa: E402


def lzss_compress_literal(data: bytes) -> bytes:
    """Простейший «сжатый» поток: только литералы (для проверки декодера)."""
    out = bytearray()
    for i in range(0, len(data), 8):
        block = data[i:i + 8]
        out.append(0xFF)
        out += block
    return bytes(out)


class PboTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.src = os.path.join(self.d, "mymod")
        os.makedirs(os.path.join(self.src, "scripts", "4_World"))
        os.makedirs(os.path.join(self.src, "data"))
        self.w(self.src, "config.cpp", b"class CfgPatches {};\n")
        self.w(self.src, "scripts/4_World/player.c", b"modded class PlayerBase {}\n" * 100)
        self.w(self.src, "data/big.bin", os.urandom(3 * 1024 * 1024 + 17))
        self.w(self.src, "$PBOPREFIX$", b"Artemida\\mymod\n")

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, base, rel, data):
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def pack(self):
        dest = os.path.join(self.d, "mymod.pbo")
        P.pack_folder(self.src, dest)
        return dest

    def test_pack_and_read(self):
        dest = self.pack()
        a = P.PboArchive.open(dest)
        self.assertEqual(a.prefix, "Artemida\\mymod")
        names = sorted(e.name for e in a.entries)
        self.assertEqual(names, ["config.cpp", "data\\big.bin", "scripts\\4_World\\player.c"])
        self.assertTrue(a.verify())
        for e in a.entries:
            with open(os.path.join(self.src, P.safe_relpath(e.name)), "rb") as f:
                self.assertEqual(a.read(e), f.read())
        dirs, files = a.list_dir("")
        self.assertEqual(dirs, ["data", "scripts"])
        self.assertEqual([f.name for f in files], ["config.cpp"])
        dirs, files = a.list_dir("SCRIPTS")
        self.assertEqual(dirs, ["4_World"])

    def test_checksum_layout(self):
        dest = self.pack()
        with open(dest, "rb") as f:
            data = f.read()
        self.assertEqual(data[-21], 0)
        self.assertEqual(hashlib.sha1(data[:-21]).digest(), data[-20:])

    def test_replace_add_delete_rename(self):
        dest = self.pack()
        a = P.PboArchive.open(dest)
        big_before = a.read(a.find("data\\big.bin"))
        new_cfg = self.w(self.d, "new/config.cpp", b"class CfgPatches { class X {}; };\n")
        extra = self.w(self.d, "extra/readme.txt", b"hello")
        plan = a.edit()
        self.assertEqual(plan.add_file("CONFIG.CPP", new_cfg), "replaced")
        self.assertEqual(plan.add_file("docs/readme.txt", extra), "added")
        plan.commit(backup=True)
        self.assertTrue(os.path.exists(dest + ".bak"))
        self.assertEqual(a.read(a.find("config.cpp")), b"class CfgPatches { class X {}; };\n")
        self.assertEqual(a.find("config.cpp").name, "config.cpp")  # регистр сохранён
        self.assertEqual(a.read(a.find("docs\\readme.txt")), b"hello")
        self.assertEqual(a.read(a.find("data\\big.bin")), big_before)
        self.assertTrue(a.verify())
        self.assertEqual(a.prefix, "Artemida\\mymod")

        plan = a.edit()
        self.assertEqual(plan.rename("scripts", "Scripts2"), 1)
        self.assertEqual(plan.delete("data"), 1)
        plan.commit()
        self.assertIsNone(a.find("data\\big.bin"))
        self.assertEqual(a.read(a.find("Scripts2\\4_World\\player.c")),
                         b"modded class PlayerBase {}\n" * 100)
        self.assertTrue(a.verify())

        plan = a.edit()
        with self.assertRaises(P.PboError):
            plan.rename("docs\\readme.txt", "config.cpp")

    def test_extract_all(self):
        dest = self.pack()
        a = P.PboArchive.open(dest)
        out = os.path.join(self.d, "out")
        a.extract_all(out)
        with open(os.path.join(out, "$PREFIX$")) as f:
            self.assertEqual(f.read(), "Artemida\\mymod")
        # повторная упаковка даёт тот же набор данных
        dest2 = os.path.join(self.d, "again.pbo")
        b = P.pack_folder(out, dest2)
        self.assertEqual(b.prefix, "Artemida\\mymod")
        self.assertEqual(sorted(e.key for e in a.entries), sorted(e.key for e in b.entries))

    def test_compressed_entry_and_no_header(self):
        # PBO без заголовка Vers, с одной упакованной записью
        payload = b"The quick brown fox " * 5
        comp = lzss_compress_literal(payload)
        body = b"a.txt\0" + struct.pack("<5I", P.CPRS, len(payload), 0, 0, len(comp))
        body += b"\0" + struct.pack("<5I", 0, 0, 0, 0, 0)
        body += comp
        body += b"\0" + hashlib.sha1(body).digest()
        path = os.path.join(self.d, "old.pbo")
        with open(path, "wb") as f:
            f.write(body)
        a = P.PboArchive.open(path)
        self.assertFalse(a.has_header)
        self.assertEqual(a.read(a.entries[0]), payload)
        self.assertTrue(a.verify())
        # добавление файла сохраняет упакованную запись как есть
        plan = a.edit()
        plan.add_file("b.txt", self.w(self.d, "b.txt", b"bbb"))
        plan.commit()
        self.assertFalse(a.has_header)
        self.assertEqual(a.read(a.find("a.txt")), payload)
        self.assertEqual(a.read(a.find("b.txt")), b"bbb")
        self.assertTrue(a.verify())

    def test_lzss_backref(self):
        # 'abc' литералами, затем ссылка назад на 3 байта длиной 6 -> 'abcabcabc'
        flags = 0b0111  # 3 литерала, затем ссылка
        b1 = 3
        b2 = (6 - 3)
        data = bytes([flags]) + b"abc" + bytes([b1, b2])
        self.assertEqual(P.lzss_decompress(data, 9), b"abcabcabc")

    def test_path_safety(self):
        self.assertEqual(P.safe_relpath("..\\..\\etc\\passwd"), os.path.join("etc", "passwd"))
        with self.assertRaises(P.PboError):
            P.to_pbo_path("a/../b")


if __name__ == "__main__":
    unittest.main()
