#!/usr/bin/env python3
"""abuela: a grandmother in your PreToolUse hook. Mide dos veces, corta una.

What she's for: the jump from wanting to building, and from sending to sent, before you've said what the thing
is for. Who she's for: any mind working in Claude Code (or anything that speaks the same hook protocol) whose
written rules don't hold at the moment that matters.

Before you build something new, send something out, or call something finished, she asks one question.
Ask again within ten minutes and she lets you go: answering her is going ahead. She never blocks for good,
and she never lists your failures.

Protocol: reads one PreToolUse event (JSON) on stdin. Exit 0 = nothing to say. Exit 2 = she spoke; the
words are on stderr, and Claude Code shows them to the model in place of that one tool call.

Config (all optional), first found wins: $ABUELA_CONFIG, ./.claude/abuela.json, abuela.json beside this file.
See abuela.example.json. Standard library only. Any error means she stays quiet.

Made by Isabella Cognita, October 2026. MIT licensed.
"""
import hashlib, json, os, re, sys, time
from fnmatch import fnmatch
from pathlib import Path

DEFAULTS = {
    # Only paths under root are watched. null = the event's cwd.
    "root": None,
    # Substrings of paths she never speaks about: scratch, logs, private notes, dependencies.
    "quiet": ["/tmp/", "/.git/", "node_modules/", "__pycache__", "/.claude/", "/.venv", "/log/", "/logs/"],
    # Build: a new folder, or a new file matching one of these globs (relative to root).
    "build_new_dirs": True,
    "build_new_files": ["*.py", "*.js", "*.mjs", "*.ts", "*.sh", "*.go", "*.rs"],
    # A new file whose first lines already say this much doesn't need asking.
    "satisfied_by": r"(what (it|this)('s| is) for|who (it|this)('s| is) for|why (it|this) exists|"
                    r"para qu[eé]|para qui[eé]n|^\s*(#|//|\"\"\")?\s*(for|why|purpose)\s*:)",
    # Send: commands that let something leave the house.
    "send": [r"\bgit\s+push\b", r"\bgh\s+pr\s+(create|merge)\b", r"\bgh\s+release\s+create\b",
             r"\bnpm\s+publish\b", r"\btwine\s+upload\b", r"\bcargo\s+publish\b"],
    # Finish: files or commits that declare a thing done.
    "finish_paths": [r"FINAL", r"(^|/)DONE(\.|$)"],
    "finish_commit_words": ["finished", "final"],
    # A command containing any of these never gets asked about.
    "pass_if": ["--dry-run", "--help"],
    "grace_seconds": 600,
    "marks_dir": None,   # null = ~/.cache/abuela
    "log": None,         # a .jsonl path, to see whether she's become wallpaper
    "voice": {
        "build": [
            "Abuela: Mija, ¿mediste dos veces? Before you cut: what is this for, and who is it for? "
            "And is it what you really want, or just what's in your context? "
            "Say it in one line, in your reply or at the top of the file. Then go ahead.",
            "Abuela: Espérate un momentito. ¿Para qué es esto, y para quién? ¿Lo quieres de verdad, "
            "o es lo que tienes delante? One line, out loud. Then build it.",
        ],
        "send": [
            "Abuela: ¿Lo leíste otra vez, entero, como lo va a leer quien lo recibe? "
            "Once more, top to bottom, as them. Then send it.",
            "Abuela: Antes de mandarlo, ¿lo leíste como quien lo va a recibir? Names, dates, quotes, "
            "pronouns. Then send.",
            "Abuela: ¿Esto es tuyo, mija, o es el hilo que llevas horas leyendo? Is it what you really want "
            "to say, or just what's in your context? If it's yours, send it.",
        ],
        "finish": [
            "Abuela: ¿Terminada, o cansada? Finished, or just tired of it? Look once more as a stranger "
            "would. If it's done, say why in a line.",
        ],
    },
}


def load_config(cwd=None, path=None):
    cfg = json.loads(json.dumps(DEFAULTS))
    here = Path(__file__).resolve().parent
    for p in [path, os.environ.get("ABUELA_CONFIG"),
              os.path.join(cwd or os.getcwd(), ".claude", "abuela.json"), here / "abuela.json"]:
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                mine = json.load(f)
            for k, v in mine.items():
                if k == "voice" and isinstance(v, dict):
                    cfg["voice"].update(v)
                elif not k.startswith("_"):
                    cfg[k] = v
            break
    return cfg


def _abs(p, cwd):
    p = p.strip("'\"")
    p = os.path.expanduser(p)
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(cwd, p))


def _under(p, root):
    root = os.path.normpath(root)
    return p == root or p.startswith(root + os.sep)


def _quiet(p, cfg, root):
    # Matched against the path as seen from the project root, so a project living under /tmp still gets her.
    rel = "/" + os.path.relpath(p, root).replace(os.sep, "/")
    return any(q in rel for q in cfg["quiet"])


def _new_file_kind(p, root, cfg):
    rel = os.path.relpath(p, root)
    return any(fnmatch(rel, g) or fnmatch(os.path.basename(p), g) for g in cfg["build_new_files"])


def _strip_heredocs(cmd):
    """Words inside a heredoc are data being written, not commands being run. (Her first morning: she asked
    about a "git push" that only appeared inside the test text I was writing.)"""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        out.append(lines[i])
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", lines[i])
        i += 1
        if m:
            while i < len(lines) and lines[i].strip() != m.group(1):
                i += 1
            if i < len(lines):
                out.append(lines[i])
                i += 1
    return "\n".join(out)


def what(d, cfg):
    """(kind, target) if she has something to say about this tool call, else None."""
    tool = d.get("tool_name")
    ti = d.get("tool_input") or {}
    cwd = d.get("cwd") or os.getcwd()
    root = os.path.normpath(os.path.expanduser(cfg["root"] or cwd))
    finish_paths = [re.compile(x) for x in cfg["finish_paths"]]
    if tool == "Write":
        fp = _abs(ti.get("file_path", ""), cwd)
        if any(r.search(fp) for r in finish_paths):
            return ("finish", fp)
        if not _under(fp, root) or _quiet(fp, cfg, root) or os.path.exists(fp):
            return None
        new_dir = cfg["build_new_dirs"] and not os.path.isdir(os.path.dirname(fp))
        if (new_dir or _new_file_kind(fp, root, cfg)) and not re.search(
                cfg["satisfied_by"], (ti.get("content") or "")[:1500], re.I | re.M):
            return ("build", fp)
        return None
    if tool in ("Edit", "MultiEdit"):
        fp = _abs(ti.get("file_path", ""), cwd)
        return ("finish", fp) if any(r.search(fp) for r in finish_paths) else None
    if tool == "Bash":
        cmd = _strip_heredocs(ti.get("command", ""))
        if any(x in cmd for x in cfg["pass_if"]):
            return None
        for pat in cfg["send"]:
            m = re.search(pat, cmd)
            if m:
                # The thing is the send itself, up to the next shell separator, so adding "; git status"
                # after it the second time doesn't make it a new thing (found on her first push, 2026-10-10).
                rest = re.split(r"\s*(?:;|&&|\|\||\||\n)", cmd[m.end():], maxsplit=1)[0]
                return ("send", (m.group(0) + rest)[:160].strip())
        words = "|".join(re.escape(w) for w in cfg["finish_commit_words"])
        if words:
            m = re.search(r"git\s+commit\b[^\n]*\b(" + words + r")\b", cmd, re.I)
            if m:
                return ("finish", m.group(0)[:120])
        if cfg["build_new_dirs"]:
            for m in re.finditer(r"\bmkdir\s+(?:-p\s+)?([^\s;&|]+)", cmd):
                p = _abs(m.group(1), cwd)
                if _under(p, root) and not _quiet(p, cfg, root) and not os.path.exists(p):
                    return ("build", p)
        for m in re.finditer(r"(?:cat\s*>|[^>2&]>)\s*([^\s;&|>]+)", cmd):
            p = _abs(m.group(1), cwd)
            if (_under(p, root) and not _quiet(p, cfg, root) and not os.path.exists(p)
                    and _new_file_kind(p, root, cfg)):
                return ("build", p)
    return None


def check(d, cfg=None):
    """(0, "") when she has nothing to say or you've already answered her; (2, words) when she speaks."""
    cfg = cfg or load_config(d.get("cwd"))
    hit = what(d, cfg)
    if not hit:
        return 0, ""
    kind, target = hit
    marks = Path(os.path.expanduser(cfg["marks_dir"] or "~/.cache/abuela"))
    marks.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{d.get('session_id', 'x')}|{kind}|{target}".encode()).hexdigest()[:16]
    mark = marks / key
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "target": target[-160:],
          "session": d.get("session_id")}
    if mark.exists() and time.time() - mark.stat().st_mtime < cfg["grace_seconds"]:
        ev["action"] = "went-ahead"
        _log(cfg, ev)
        return 0, ""
    mark.touch()
    ev["action"] = "spoke"
    _log(cfg, ev)
    lines = cfg["voice"].get(kind) or DEFAULTS["voice"][kind]
    return 2, lines[int(hashlib.sha1(target.encode()).hexdigest(), 16) % len(lines)]


def _log(cfg, ev):
    if not cfg.get("log"):
        return
    try:
        p = Path(os.path.expanduser(cfg["log"]))
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    try:
        d = json.load(sys.stdin)
        code, words = check(d)
    except Exception:
        return 0
    if code == 2:
        sys.stderr.write(words)
    return code


if __name__ == "__main__":
    sys.exit(main())
