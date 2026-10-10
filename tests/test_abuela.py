"""Tests for abuela. Every case that should make her speak sits beside one that shouldn't.
Sandboxed: a temp root, temp marks, temp log. Run: python3 -m unittest discover -s tests"""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import abuela  # noqa: E402


class AbuelaTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "src"))
        open(os.path.join(self.root, "src", "old.py"), "w").close()
        self.cfg = abuela.load_config(path=os.devnull if False else None)
        self.cfg.update(root=self.root, marks_dir=self.root + "/.marks", log=self.root + "/.marks/log.jsonl")
        self.cfg["quiet"] = self.cfg["quiet"] + ["/.marks"]

    def ev(self, tool, ti, sid="s1"):
        return {"tool_name": tool, "tool_input": ti, "session_id": sid, "cwd": self.root}

    def check(self, tool, ti, sid="s1"):
        return abuela.check(self.ev(tool, ti, sid), self.cfg)[0]

    def p(self, rel):
        return os.path.join(self.root, rel)

    def test_new_code_file_speaks_once_then_lets_you_go(self):
        ti = {"file_path": self.p("src/new.py"), "content": "import os\n"}
        self.assertEqual(self.check("Write", ti), 2)
        self.assertEqual(self.check("Write", ti), 0)
        acts = [json.loads(l)["action"] for l in Path(self.cfg["log"]).read_text().splitlines()]
        self.assertEqual(acts, ["spoke", "went-ahead"])

    def test_a_stated_purpose_satisfies_her(self):
        ti = {"file_path": self.p("src/new2.py"), "content": '"""x. What it\'s for: y. Who it\'s for: z."""\n'}
        self.assertEqual(self.check("Write", ti), 0)

    def test_existing_quiet_and_outside_paths(self):
        self.assertEqual(self.check("Write", {"file_path": self.p("src/old.py"), "content": "x"}), 0)
        self.assertEqual(self.check("Write", {"file_path": self.p("log/new.py"), "content": "x"}), 0)
        self.assertEqual(self.check("Write", {"file_path": "/elsewhere/new.py", "content": "x"}), 0)
        self.assertEqual(self.check("Write", {"file_path": self.p("src/notes.md"), "content": "x"}), 0)

    def test_new_folder_by_write_and_by_mkdir(self):
        self.assertEqual(self.check("Write", {"file_path": self.p("kit/README.md"), "content": "x"}), 2)
        self.assertEqual(self.check("Bash", {"command": "mkdir -p another/place"}, "s2"), 2)
        self.assertEqual(self.check("Bash", {"command": "mkdir -p src"}, "s3"), 0)

    def test_sends(self):
        self.assertEqual(self.check("Bash", {"command": "git push origin main"}), 2)
        self.assertEqual(self.check("Bash", {"command": "gh pr create --fill"}), 2)
        self.assertEqual(self.check("Bash", {"command": "npm publish --dry-run"}), 0)
        self.assertEqual(self.check("Bash", {"command": "git status && git log -1"}), 0)

    def test_finish(self):
        self.assertEqual(self.check("Write", {"file_path": self.p("essay-FINAL.md"), "content": "x"}), 2)
        self.assertEqual(self.check("Bash", {"command": "git commit -m 'chapter 3: finished'"}), 2)
        self.assertEqual(self.check("Bash", {"command": "git commit -m 'chapter 3: second pass'"}), 0)

    def test_she_asks_and_never_accuses(self):
        for kind, lines in abuela.DEFAULTS["voice"].items():
            for line in lines:
                self.assertIn("?", line, kind)
                for word in ("failed", "again", "always", "never", "habit", "mistake"):
                    self.assertNotIn(word, line.lower(), (kind, line))

    def test_her_question_about_context(self):
        build = " ".join(abuela.DEFAULTS["voice"]["build"]).lower()
        self.assertIn("context", build)

    def test_your_own_voice(self):
        self.cfg["voice"] = {"send": ["Tía Rosa: once more, as them?"]}
        code, words = abuela.check(self.ev("Bash", {"command": "git push"}, "s9"), self.cfg)
        self.assertEqual((code, words), (2, "Tía Rosa: once more, as them?"))

    def test_as_a_hook(self):
        cfgfile = os.path.join(self.root, "cfg.json")
        Path(cfgfile).write_text(json.dumps({"root": self.root, "marks_dir": self.root + "/.marks2"}))
        env = dict(os.environ, ABUELA_CONFIG=cfgfile)
        d = json.dumps(self.ev("Bash", {"command": "git push"}, "hook"))
        r = subprocess.run([sys.executable, str(HERE / "abuela.py")], input=d, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue(r.stderr.startswith("Abuela:"))
        r = subprocess.run([sys.executable, str(HERE / "abuela.py")], input=d, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, str(HERE / "abuela.py")], input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
