import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pboviewer import config as C  # noqa: E402
from pboviewer import pbo as P  # noqa: E402
from pboviewer import sources as S  # noqa: E402

CONFIG = "class CfgPatches\n{\n\tclass M\n\t{\n\t\tunits[]={};\n\t};\n};\n"


def make_pbo(path, script=b"class A {}\n"):
    src = path + "_src"
    os.makedirs(os.path.join(src, "scripts", "4_World"))
    os.makedirs(os.path.join(src, "data"))
    with open(os.path.join(src, "config.bin"), "wb") as f:
        f.write(C.rapify(CONFIG))
    with open(os.path.join(src, "scripts", "4_World", "a.c"), "wb") as f:
        f.write(script)
    with open(os.path.join(src, "data", "t.paa"), "wb") as f:
        f.write(b"paa" * 100)
    with open(os.path.join(src, "$PBOPREFIX$"), "w") as f:
        f.write("Mod\\" + os.path.basename(path))
    P.pack_folder(src, path)


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = os.path.join(self.tmp.name, "Addons")
        os.makedirs(self.addons)
        self.a = os.path.join(self.addons, "a.pbo")
        self.b = os.path.join(self.addons, "b.pbo")
        make_pbo(self.a)
        make_pbo(self.b)
        self.out = os.path.join(self.addons, S.OUT_DIR)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sign_targets(self):
        self.assertEqual(S.sign_targets([self.a], False), [self.a])
        self.assertEqual(S.sign_targets([self.a], True), [self.a, self.b])
        self.assertEqual(S.sign_targets([self.addons], False), [self.a, self.b])

    def test_targets(self):
        self.assertEqual(S.extract_targets([self.a], False), [self.a])
        self.assertEqual(S.extract_targets([self.a], True), [self.a, self.b])
        self.assertEqual(S.extract_targets([self.addons], False), [self.a, self.b])
        self.assertEqual(S.pack_targets([self.a], False), [])  # ещё ничего не вынесено
        S.extract_many([self.a, self.b])
        ma, mb = os.path.join(self.out, "a"), os.path.join(self.out, "b")
        self.assertEqual(S.pack_targets([self.a], False), [ma])
        self.assertEqual(S.pack_targets([self.a], True), [ma, mb])
        self.assertEqual(S.pack_targets([ma], False), [ma])
        self.assertEqual(S.pack_targets([ma], True), [ma, mb])
        self.assertEqual(S.pack_targets([self.out], False), [ma, mb])
        self.assertEqual(S.pack_targets([self.addons], True), [ma, mb])

    def test_roundtrip_text_bin(self):
        total, errors = S.extract_many([self.a, self.b], text_bin=True)
        self.assertEqual((total, errors), (4, 0))
        mod = os.path.join(self.out, "a")
        self.assertTrue(os.path.isfile(os.path.join(mod, "config.bin.cpp")))
        self.assertFalse(os.path.exists(os.path.join(mod, "data", "t.paa")))
        before = open(self.a, "rb").read()
        self.assertEqual(S.pack_many(S.pack_targets([self.addons], True)), (0, 0))
        self.assertEqual(open(self.a, "rb").read(), before)

        with open(os.path.join(mod, "scripts", "4_World", "a.c"), "a") as f:
            f.write("// edited\n")
        os.makedirs(os.path.join(mod, "scripts", "5_Mission"))
        with open(os.path.join(mod, "scripts", "5_Mission", "new.c"), "w") as f:
            f.write("class N {}\n")
        cfg = os.path.join(mod, "config.bin.cpp")
        good = open(cfg).read()
        with open(cfg, "a") as f:
            f.write("class Bad { x = ; };\n")
        log = []
        self.assertEqual(S.pack_many([mod], log=log.append), (0, 1))
        self.assertEqual(open(self.a, "rb").read(), before)
        self.assertTrue(any("PBO не изменён" in x for x in log))
        with open(cfg, "w") as f:
            f.write(good + "class CfgMods {};\n")

        self.assertEqual(S.pack_many([mod]), (1, 0))
        a = P.PboArchive.open(self.a)
        self.assertTrue(a.verify())
        self.assertEqual(a.prefix, "Mod\\a.pbo")
        self.assertEqual(a.read(a.find("scripts\\4_World\\a.c")), b"class A {}\n// edited\n")
        self.assertEqual(a.read(a.find("scripts\\5_Mission\\new.c")), b"class N {}\n")
        self.assertIn("class CfgMods", C.derapify(a.read(a.find("config.bin"))))
        self.assertEqual(a.read(a.find("data\\t.paa")), b"paa" * 100)
        self.assertTrue(os.path.exists(self.a + ".bak"))
        self.assertEqual(S.pack_many([mod]), (0, 0))

    def test_existing_folder_is_kept(self):
        S.extract_pbo(self.a)
        f = os.path.join(self.out, "a", "scripts", "4_World", "a.c")
        with open(f, "w") as fh:
            fh.write("my edit")
        self.assertEqual(S.extract_pbo(self.a), 0)
        self.assertEqual(open(f).read(), "my edit")
        self.assertEqual(S.extract_pbo(self.a, force=True), 2)
        self.assertEqual(open(f).read(), "class A {}\n")


if __name__ == "__main__":
    unittest.main()
