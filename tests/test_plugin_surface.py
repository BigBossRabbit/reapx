#!/usr/bin/env python3
"""test_plugin_surface.py — the register() contract, rehearsed exactly the
way ``hermes plugins validate`` runs it (the recording-stub probe) plus the
engine-adapter mechanics. Hermes-free (a hermes import inside register()'s
optional steps degrades gracefully — same as under the validate probe).

Guards (each maps to a gate that would otherwise only fail at admission):
  * register() runs against a recording stub WITHOUT raising, registers the
    3 declared tools (R06) and nothing else;
  * register() never touches the property surface the recording stub cannot
    provide (ctx.llm / ctx.state / ctx.manifest) — the validate probe's
    stub raises AttributeError for those, so touching one fails admission;
  * every tool schema is a dict with a dict ``parameters`` (registry.register
    raises at load time otherwise) and declares its name;
  * tools.py handlers NEVER raise (the never-raise contract) and always
    return a JSON string;
  * the tool arg allowlist: schemas declare only the permitted args
    (settings are not callable);
  * _engine namespace adapter: load() imports the shared engine under the
    plugin's own namespace (never top-level), a missing engine import
    becomes a RuntimeError (never a bare ImportError), the fetch chain is
    never imported, and evict() pops the one reapx-exclusive top-level name;
  * register_skill with a non-existent path is a guarded error (the bundled
    skill loop never kills the load).
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The hermes checkout (the R09 no-shadow probe puts it on the subprocess
# path so the engine load happens with hermes core's top-level packages
# importable — the exact runtime shape).
HERMES_CHECKOUT = Path("/Users/fromthejump/.hermes/profiles/thanos/"
                       "hermes-agent")

PLUGIN_PKG = "reapx_under_test"


def _load_plugin(pkg=PLUGIN_PKG):
    """Import the plugin the way the loader does (spec + package path)."""
    if pkg in sys.modules:
        return sys.modules[pkg]
    spec = importlib.util.spec_from_file_location(
        pkg, REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)])
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(REPO_ROOT)]
    sys.modules[pkg] = module
    spec.loader.exec_module(module)
    return module


class RecordingContext:
    """A faithful copy of the validate probe's recording stub (the class the
    admission check runs register() against): real attributes for the
    surface the stub provides, a hard AttributeError for the property
    surface the real PluginContext provides only as instance/state
    attributes (ctx.llm, ctx.state, ctx.manifest) — touching one from
    register() fails validation, so this stub enforces the same rule."""

    plugin_config = {}
    profile_name = "default"
    plugin_id = "reapx_under_test"

    def __init__(self):
        self.recorded = {"tools": [], "hooks": [], "middleware": [],
                         "commands": [], "providers": [], "skills": [],
                         "blueprints": [], "aux_tasks": [], "on_unload": []}
        self.raised = []

    def register_tool(self, name, *args, **kwargs):
        self.recorded["tools"].append(str(name))

    def register_hook(self, hook_name, callback, **kwargs):
        self.recorded["hooks"].append(str(hook_name))

    def register_middleware(self, kind, callback):
        self.recorded["middleware"].append(str(kind))

    def register_command(self, name, *args, **kwargs):
        self.recorded["commands"].append(str(name))

    def get_config(self, key, default=None):
        return default

    def register_auxiliary_task(self, key, **kwargs):
        self.recorded["aux_tasks"].append(str(key))

    def register_skill(self, name, path, *args, **kwargs):
        # the REAL one requires an existing path; the stub records
        import pathlib
        if not pathlib.Path(path).exists():
            raise FileNotFoundError(f"SKILL.md not found at {path}")
        self.recorded["skills"].append(str(name))

    def register_automation_blueprint(self, key, **kwargs):
        self.recorded["blueprints"].append(str(key))

    def on_unload(self, callback):
        self.recorded["on_unload"].append(callback)

    def __getattr__(self, name):
        # exactly the probe's rule: names that are NOT real PluginContext
        # methods raise; everything real (register_*) we provide above.
        self.raised.append(name)
        raise AttributeError(name)


class RegisterContractTests(unittest.TestCase):
    """register() under the recording stub == the validate probe run."""

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        self.plugin = _load_plugin()
        self.ctx = RecordingContext()

    def tearDown(self):
        # the engine adapter may have registered an on_unload evictor — run
        # the callbacks + drop our package copy
        try:
            self.plugin.runtime.run_unload()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def test_register_records_exactly_the_three_tools(self):
        self.plugin.register(self.ctx)
        self.assertEqual(sorted(self.ctx.recorded["tools"]),
                         sorted(["reapx_import", "reapx_status",
                                 "reapx_review"]))

    def test_register_never_touches_the_stub_forbidden_surface(self):
        self.plugin.register(self.ctx)
        # __getattr__ was never needed => register() only used the real
        # recording surface (get_config / register_*). ctx.llm / ctx.state /
        # ctx.manifest would have landed in `raised`.
        self.assertEqual(self.ctx.raised, [])

    def test_register_aux_task_underscore_key(self):
        self.plugin.register(self.ctx)
        self.assertEqual(self.ctx.recorded["aux_tasks"],
                         ["reapx_enricher"])
        # A hyphen in the key raises ValueError in the REAL registrar and
        # fails the whole load (mission-C probe discovery): assert the key
        # is alnum+underscore only.
        self.assertRegex("reapx_enricher", r"^[A-Za-z0-9_]+$")

    def test_register_bundled_index_skill(self):
        self.plugin.register(self.ctx)
        self.assertIn("reapx-index", self.ctx.recorded["skills"])

    def test_register_blueprint(self):
        self.plugin.register(self.ctx)
        self.assertEqual(self.ctx.recorded["blueprints"], ["harvest"])

    def test_register_on_unload(self):
        self.plugin.register(self.ctx)
        self.assertEqual(len(self.ctx.recorded["on_unload"]), 1)

    def test_register_with_a_broken_optional_step_degrades(self):
        """A failing aux task (the recording stub raising, say) must never
        kill the load: register() still records the 3 tools."""
        ctx = RecordingContext()

        def boom(*a, **k):
            raise ValueError("aux task key already taken")

        ctx.register_auxiliary_task = boom
        self.plugin.register(ctx)
        self.assertEqual(len(ctx.recorded["tools"]), 3)

    def test_schemas_are_well_formed(self):
        from reapx_under_test import schemas  # the package copy
        for name, schema in schemas.ALL.items():
            self.assertIsInstance(schema, dict, name)
            self.assertEqual(schema["name"], name)
            params = schema.get("parameters")
            self.assertIsInstance(params, dict, name)
            self.assertIn("type", params)
            self.assertIn("description", schema)


class NeverRaiseContractTests(unittest.TestCase):
    """tools.py: a JSON string always, a raise never (A.3.6)."""

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        self.plugin = _load_plugin()
        self.plugin.runtime.set_ctx(RecordingContext())

    def tearDown(self):
        try:
            self.plugin.runtime.run_unload()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def _json(self, raw):
        parsed = json.loads(raw)
        self.assertIsInstance(raw, str)
        return parsed

    def test_import_runs_the_one_pipeline(self):
        """No mode guards any more: reapx_import always reaches the driver.
        Without hermes the driver fails at its data-dir lookup; with hermes
        the missing input is the clean import_invalid error. Either way it
        is error JSON, not a mode refusal."""
        from reapx_under_test import tools
        out = self._json(tools.import_({}))
        self.assertFalse(out["ok"])
        self.assertIsInstance(out["error"], str)
        self.assertNotIn("disabled_in", out["error"])

    def test_status_never_raises_without_hermes(self):
        from reapx_under_test import tools
        out = self._json(tools.status({}))
        self.assertTrue(out["ok"])

    def test_review_never_raises_without_hermes(self):
        from reapx_under_test import tools
        out = self._json(tools.review({}))
        self.assertTrue(out["ok"])

    def test_handlers_accept_var_kwargs(self):
        import inspect
        from reapx_under_test import tools
        for name in ("import_", "status", "review"):
            sig = inspect.signature(getattr(tools, name))
            self.assertTrue(any(p.kind is inspect.Parameter.VAR_KEYWORD
                                for p in sig.parameters.values()), name)

    def test_import_with_a_raising_worker_returns_error_json(self):
        from reapx_under_test import tools

        class Boom:
            def run(self, args):
                raise RuntimeError("engine exploded")

        import reapx_under_test.harvest as h
        orig = h.run
        h.run = Boom().run
        try:
            out = self._json(tools.import_({"dry_run": True}))
        finally:
            h.run = orig
        self.assertFalse(out["ok"])
        self.assertIn("RuntimeError", out["error"])

    def test_import_strips_undeclared_args(self):
        """The tool is the allowlist's enforcement point: a call passing
        settings-shaped args still reaches the driver without them."""
        from reapx_under_test import tools

        seen = {}

        def fake(args):
            seen.update(args)
            return {"ok": True}

        import reapx_under_test.harvest as h
        orig = h.run
        h.run = fake
        try:
            tools.import_({"items": [], "harvest_mode": "browser",
                           "enrich": "rules", "register_generated_skills":
                           True, "browser": "chrome", "profile": "Default",
                           "max_scrolls": 3, "generate_limit": 2})
        finally:
            h.run = orig
        self.assertEqual(sorted(seen), ["generate_limit", "items"])


class SchemaAllowlistTests(unittest.TestCase):
    """The arg surface is a fixed allowlist: no setting is callable."""

    SETTINGS = {"harvest_mode", "browser", "browser_profile", "max_scrolls",
                "import_dir", "enrich", "llm_max_items",
                "register_generated_skills"}
    ALLOWED = {
        "reapx_import": {"path", "items", "generate_limit", "dry_run"},
        "reapx_status": set(),
        "reapx_review": {"since", "category", "opportunities_only",
                         "include_skill_md"},
    }

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        self.plugin = _load_plugin()

    def tearDown(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def test_exactly_the_allowlisted_args_are_declared(self):
        from reapx_under_test import schemas
        self.assertEqual(sorted(schemas.ALL), sorted(self.ALLOWED))
        for name, schema in schemas.ALL.items():
            declared = set(schema["parameters"].get("properties") or {})
            self.assertEqual(declared, self.ALLOWED[name], name)

    def test_no_setting_name_is_a_tool_arg(self):
        from reapx_under_test import schemas
        for name, schema in schemas.ALL.items():
            declared = set(schema["parameters"].get("properties") or {})
            self.assertEqual(declared & self.SETTINGS, set(), name)


class EngineAdapterTests(unittest.TestCase):
    """_engine: the namespace import + the never-bare-ImportError rule."""

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        self.plugin = _load_plugin()

    def tearDown(self):
        try:
            self.plugin.runtime.run_unload()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def test_load_imports_shared_engine_under_the_namespace(self):
        from reapx_under_test import _engine
        _engine.load()
        # shared engine: under the plugin's namespace …
        self.assertIn(PLUGIN_PKG + ".scripts.ledger", sys.modules)
        self.assertIn(PLUGIN_PKG + ".scripts.categorize_repos", sys.modules)
        self.assertIn(PLUGIN_PKG + ".scripts.generate_skills", sys.modules)
        # … and NOT top-level (two plugins never fight over the shared
        # engine names — reapx's vs starlearner's generate_skills differ).
        self.assertNotIn("ledger", sys.modules)
        self.assertNotIn("categorize_repos", sys.modules)
        self.assertNotIn("generate_skills", sys.modules)

    def test_load_never_imports_the_browser_chain(self):
        """Review point 1: the plugin path touches no browser module."""
        from reapx_under_test import _engine
        _engine.load()
        self.assertNotIn(PLUGIN_PKG + ".scripts.fetch_x_bookmarks",
                         sys.modules)
        self.assertNotIn("reapx_browser_locator", sys.modules)
        self.assertNotIn("reapx_cdp_launcher", sys.modules)
        self.assertNotIn("cookie_store", sys.modules)
        self.assertNotIn("fetch_x_bookmarks", sys.modules)
        self.assertFalse(hasattr(_engine, "load_fetch"))
        self.assertEqual(_engine.ENGINE_TOP_LEVEL, ("reapx_paths",))

    def test_broken_engine_import_is_a_runtimeerror(self):
        from reapx_under_test import _engine
        key = PLUGIN_PKG + ".scripts"
        old = sys.modules.get(key)
        sys.modules[key] = None  # None in sys.modules ⇒ ImportError
        _engine._LOADED = False
        try:
            with self.assertRaises(RuntimeError):
                _engine.load()
        finally:
            if old is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = old
            _engine._LOADED = False

    def test_evict_pops_the_reapx_exclusive_top_levels(self):
        from reapx_under_test import _engine
        for name in _engine.ENGINE_TOP_LEVEL:
            sys.modules[name] = types.ModuleType(name)
        _engine.evict()
        for name in _engine.ENGINE_TOP_LEVEL:
            self.assertNotIn(name, sys.modules)

    def test_engine_load_restores_sys_path(self):
        """THE R09 no-shadow proof (a fresh subprocess): the engine
        scripts carry their own standalone-era
        ``sys.path.insert(0, <repo root>)`` idiom — under the plugin that
        would leave THE PLUGIN ROOT on sys.path, and every plugin root
        has a top-level ``tools.py`` that would then shadow hermes
        core's ``tools`` package (imported lazily all over core) for
        every later import. _engine restores the path after every load;
        the engine itself keeps working (its modules land in sys.modules
        under the plugin's namespace)."""
        import json
        import os
        import subprocess
        import textwrap

        code = textwrap.dedent('''
            import importlib.util, sys, json
            from pathlib import Path
            REPO = Path(%r)
            if str(REPO) in sys.path:
                sys.path.remove(str(REPO))
            HERMES = %r
            if HERMES and str(HERMES) not in sys.path:
                sys.path.insert(0, str(HERMES))
            spec = importlib.util.spec_from_file_location(
                "engine_pkg", REPO / "__init__.py",
                submodule_search_locations=[str(REPO)])
            pkg = importlib.util.module_from_spec(spec)
            pkg.__path__ = [str(REPO)]
            sys.modules["engine_pkg"] = pkg
            spec.loader.exec_module(pkg)  # the import surface only
            from engine_pkg import _engine
            before = list(sys.path)
            _engine.load()
            out = {"restored": sys.path == before,
                   "root_anywhere": str(REPO) in sys.path,
                   "engine_loaded": _engine._LOADED,
                   "ledger_ns": "engine_pkg.scripts.ledger" in sys.modules}
            try:
                import tools
                out["tools_file"] = getattr(tools, "__file__", "") or ""
            except Exception as exc:
                out["tools_file"] = "IMPORT-ERROR " + type(exc).__name__
            print("R09PROBE " + json.dumps(out))
        ''') % (str(REPO_ROOT),
                str(HERMES_CHECKOUT) if HERMES_CHECKOUT.is_dir() else None)
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True,
            text=True, timeout=120, env=env, cwd=str(REPO_ROOT / "tests"))
        line = [l for l in proc.stdout.splitlines()
                if l.startswith("R09PROBE ")]
        self.assertTrue(line, "probe output missing: %r / %r"
                        % (proc.stdout[-300:], proc.stderr[-300:]))
        out = json.loads(line[0].split(" ", 1)[1])
        self.assertTrue(out["engine_loaded"])  # the engine really loaded
        self.assertTrue(out["ledger_ns"])  # under the namespace
        self.assertTrue(out["restored"])   # sys.path bit-for-bit restored
        self.assertFalse(out["root_anywhere"])  # no root left behind
        # a top-level 'tools' never resolves to the plugin's tools.py:
        self.assertNotIn(str(REPO_ROOT), out["tools_file"])

    def test_register_skill_bad_path_is_guarded(self):
        """The bundled-skill loop: a non-existent SKILL.md (the real
        register_skill raises FileNotFoundError) is a logged error, never a
        dead plugin."""
        ctx = RecordingContext()
        real_reg = ctx.register_skill

        def raising_reg(name, path, *a, **k):
            if name == "reapx-index":
                raise FileNotFoundError(f"SKILL.md not found at {path}")
            return real_reg(name, path, *a, **k)

        ctx.register_skill = raising_reg
        self.plugin.register(ctx)  # must NOT raise
        self.assertEqual(len(ctx.recorded["tools"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
