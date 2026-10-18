import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pboviewer import obfuscate as O  # noqa: E402
from pboviewer import pbo as P  # noqa: E402

SCRIPT = '''﻿// header
modded class PlayerBase
{
    protected int m_X;   // field
    /* block
       comment */
    void Foo(string s)
    {
        string u = "http://x // y /* z */";
        if ( m_X  >  0 )  Print(u);   // trailing
    }
}

#define MAX 5
#define MULTI(a,b) \\
    ((a)+(b))
'''


class MinifyTests(unittest.TestCase):
    def test_minify(self):
        out = O.minify_script(SCRIPT)
        # комментарии вне строк убраны
        self.assertNotIn("// header", out)
        self.assertNotIn("// field", out)
        self.assertNotIn("/* block", out)
        # строка со спецсимволами сохранена целиком
        self.assertIn('"http://x // y /* z */"', out)
        # имена классов/функций на месте
        self.assertIn("modded class PlayerBase", out)
        self.assertIn("void Foo(string s)", out)
        # скобки сбалансированы, стало короче
        self.assertEqual(out.count("{"), SCRIPT.count("{"))
        self.assertEqual(out.count("}"), SCRIPT.count("}"))
        self.assertLess(len(out), len(SCRIPT))
        # BOM и директивы препроцессора сохранены
        self.assertTrue(out.startswith("﻿"))
        self.assertIn("#define MAX 5", out)
        self.assertIn("#define MULTI(a,b) \\", out)
        self.assertIn("((a)+(b))", out)
        # нет пустых строк
        self.assertNotIn("\n\n", out)
        # идемпотентность
        self.assertEqual(O.minify_script(out), out)

    def test_string_with_escaped_quote(self):
        self.assertEqual(O.minify_script('a = "x\\"// y"; // c\n'), 'a = "x\\"// y";\n')

    def test_empty(self):
        self.assertEqual(O.minify_script(""), "")
        self.assertEqual(O.minify_script("// only comment\n"), "")


class PackObfuscateTests(unittest.TestCase):
    def test_pack_obfuscates_c_only(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(os.path.join(src, "scripts"))
            with open(os.path.join(src, "scripts", "a.c"), "w") as f:
                f.write("// secret\nmodded class A\n{\n    void F() { Print(1); }\n}\n")
            with open(os.path.join(src, "config.cpp"), "w") as f:
                f.write("class CfgPatches { class M { units[]={}; }; };\n")
            with open(os.path.join(src, "$PBOPREFIX$"), "w") as f:
                f.write("My\\Mod")
            a = P.pack_folder(src, os.path.join(d, "m.pbo"), rapify_cpp=True, obfuscate_scripts=True)
            c = a.read(a.find("scripts\\a.c")).decode()
            self.assertNotIn("//", c)
            self.assertIn("modded class A", c)
            self.assertTrue(a.verify())
            # исходник не тронут
            self.assertIn("// secret", open(os.path.join(src, "scripts", "a.c")).read())


if __name__ == "__main__":
    unittest.main()
