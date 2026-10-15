import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pboviewer import bisign as B  # noqa: E402
from pboviewer import classes as K  # noqa: E402
from pboviewer import config as C  # noqa: E402
from pboviewer import pbo as P  # noqa: E402

CONFIG = '''// комментарий
class CfgPatches
{
    class MyMod
    {
        units[] = {};
        requiredVersion = 0.1;
        requiredAddons[] = {"DZ_Data", "DZ_Gear_Food"};
    };
};
class CfgVehicles
{
    class Edible_Base;
    class MyApple: Edible_Base
    {
        scope = 2;
        displayName = "Яблоко ""мод""";
        weight = 1.5;
        model = \\dz\\gear\\food\\apple.p3d;
        itemSize[] = {1, 2};
        nested[] = {{1, "a"}, {}};
        hiddenSelections[] += {"zbytek"};
    };
    delete OldThing;
};
'''

SCRIPT = """modded class PlayerBase
{
    // class Fake extends X
    string s = "class Q extends W";
};
class MyItem extends ItemBase {}
class MyCar: CarScript
{
};
class Forward;
class Helper
{
};
"""


class ConfigTests(unittest.TestCase):
    def test_roundtrip(self):
        data = C.rapify(CONFIG)
        self.assertTrue(C.is_rapified(data))
        text = C.derapify(data)
        self.assertEqual(C.rapify(text), data)
        root = C.read_bin(data)
        veh = [c for c in root.classes() if c.name == "CfgVehicles"][0]
        apple = veh.classes()[0]
        self.assertEqual(apple.parent, "Edible_Base")
        vals = {e[1]: e[2] for e in apple.entries if e[0] in ("value", "array", "append")}
        self.assertEqual(vals["scope"], 2)
        self.assertEqual(vals["displayName"], 'Яблоко "мод"')
        self.assertAlmostEqual(vals["weight"], 1.5)
        self.assertEqual(vals["model"], "\\dz\\gear\\food\\apple.p3d")
        self.assertEqual(vals["itemSize"], [1, 2])
        self.assertEqual(vals["nested"], [[1, "a"], []])
        self.assertIn(("append", "hiddenSelections", ["zbytek"]), apple.entries)
        self.assertIn(("extern", "Edible_Base"), veh.entries)
        self.assertIn(("delete", "OldThing"), veh.entries)
        # enum-смещение указывает на конец
        self.assertEqual(struct.unpack_from("<I", data, 12)[0], len(data) - 4)

    def test_float_format(self):
        self.assertEqual(C.format_float(struct.unpack("<f", struct.pack("<f", 0.1))[0]), "0.1")
        self.assertEqual(C.format_float(2.0), "2.0")
        self.assertIsInstance(C.parse_scalar("2.0"), float)
        self.assertIsInstance(C.parse_scalar("2"), int)
        self.assertEqual(C.parse_scalar("1e-005"), 1e-5)

    def test_errors_have_line(self):
        with self.assertRaises(C.ConfigError) as cm:
            C.parse_text("class A\n{\n  x = ;\n};\n")
        self.assertEqual(cm.exception.line, 3)
        with self.assertRaises(C.ConfigError):
            C.parse_text("class A {\n")
        with self.assertRaises(C.ConfigError):
            C.parse_text('#include "x.hpp"\n')


class ClassTests(unittest.TestCase):
    def test_script_scan(self):
        found = K.scan_script(SCRIPT, "scripts\\4_World\\a.c")
        got = {(c.name, c.parent, c.kind, c.line) for c in found}
        self.assertEqual(got, {("PlayerBase", "PlayerBase", "modded", 1), ("MyItem", "ItemBase", "script", 6),
                               ("MyCar", "CarScript", "script", 7), ("Helper", None, "script", 11)})

    def test_config_scan_and_describe(self):
        found = K.scan_config(CONFIG, "config.cpp")
        names = {(c.name, c.parent, c.section) for c in found}
        self.assertIn(("MyApple", "Edible_Base", "CfgVehicles"), names)
        self.assertIn(("MyMod", None, "CfgPatches"), names)
        K.ClassIndex(found)
        apple = [c for c in found if c.name == "MyApple"][0]
        self.assertEqual(apple.desc_from, "Edible_Base")
        self.assertEqual(apple.chain[:3], ("MyApple", "Edible_Base", "Inventory_Base"))
        src = K.class_source(CONFIG, apple.line)
        self.assertTrue(src.startswith("class MyApple: Edible_Base"))
        self.assertTrue(src.rstrip().endswith("};"))

    def test_search(self):
        found = K.scan_script(SCRIPT, "a.c")
        K.ClassIndex(found)
        self.assertEqual([c.name for c in K.search(found, "mYc", "name")], ["MyCar"])
        self.assertEqual([c.name for c in K.search(found, "машин", "desc")], ["MyCar"])
        builtin = K.builtin_classes()
        K.ClassIndex(builtin)
        self.assertTrue(any(c.name == "PlayerBase" for c in K.search(builtin, "игрок", "desc")))

    def test_archive_scan_with_bin(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "mod")
            os.makedirs(os.path.join(src, "scripts"))
            with open(os.path.join(src, "config.bin"), "wb") as f:
                f.write(C.rapify(CONFIG))
            with open(os.path.join(src, "scripts", "a.c"), "w") as f:
                f.write(SCRIPT)
            a = P.pack_folder(src, os.path.join(d, "mod.pbo"))
            found = K.scan_archive(a)
            self.assertIn("MyApple", {c.name for c in found})
            self.assertIn("MyCar", {c.name for c in found})


DATA = os.path.join(os.path.dirname(__file__), "data")


class SignTests(unittest.TestCase):
    def test_matches_dssignfile(self):
        """Подписи побайтно совпадают с DSSignFile.exe 1.03 (DayZ Tools) на том же ключе.

        fixture.pbo: префикс с «\\» на конце, записи не по алфавиту, LZSS-запись, кириллица,
        пустой файл, расширения в разном регистре."""
        key = B.PrivateKey.load(os.path.join(DATA, "test.biprivatekey"))
        pbo = os.path.join(DATA, "fixture.pbo")
        with tempfile.TemporaryDirectory() as d:
            for version in (2, 3):
                out = B.sign_pbo(pbo, key, version, dest=os.path.join(d, f"v{version}.bisign"))
                with open(out, "rb") as f, \
                        open(os.path.join(DATA, f"fixture.pbo.v{version}.dssignfile.bisign"), "rb") as ref:
                    self.assertEqual(f.read(), ref.read(), f"v{version}")

    def test_sign_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "mod")
            os.makedirs(src)
            with open(os.path.join(src, "config.cpp"), "w") as f:
                f.write(CONFIG)
            with open(os.path.join(src, "$PBOPREFIX$"), "w") as f:
                f.write("MyMod")
            pbo = os.path.join(d, "mod.pbo")
            P.pack_folder(src, pbo)
            key = B.generate_key("tester")
            key.save(os.path.join(d, "tester.biprivatekey"))
            key.public.save(os.path.join(d, "tester.bikey"))
            loaded = B.PrivateKey.load(os.path.join(d, "tester.biprivatekey"))
            self.assertEqual(loaded, key)
            pub = B.PublicKey.load(os.path.join(d, "tester.bikey"))
            for version in (2, 3):
                path = B.sign_pbo(pbo, loaded, version)
                self.assertEqual(path, pbo + ".tester.bisign")
                sig = B.Signature.load(path)
                self.assertEqual(sig.version, version)
                self.assertTrue(B.verify_pbo(pbo, pub, sig))
            # формат файлов
            with open(os.path.join(d, "tester.bikey"), "rb") as f:
                raw = f.read()
            self.assertEqual(raw[:7], b"tester\0")
            self.assertEqual(struct.unpack_from("<I", raw, 7)[0], 148)
            self.assertEqual(len(raw), 7 + 4 + 148)
            with open(path, "rb") as f:
                self.assertEqual(len(f.read()), 7 + 4 + 148 + 3 * (4 + 128) + 4)
            # изменённый архив — подпись больше не подходит
            other = B.generate_key("other")
            self.assertFalse(B.verify_pbo(pbo, other.public, sig))
            plan = P.PboArchive.open(pbo).edit()
            extra = os.path.join(d, "x.hpp")
            with open(extra, "w") as f:
                f.write("x")
            plan.add_file("x.hpp", extra)
            plan.commit()
            self.assertFalse(B.verify_pbo(pbo, pub, sig))


if __name__ == "__main__":
    unittest.main()
