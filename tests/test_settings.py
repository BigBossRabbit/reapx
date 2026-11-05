#!/usr/bin/env python3
"""test_settings.py — the settings law (the skill's step-6 gate).

Every setting lives under ``plugins.entries.reapx.settings.*`` (rendered
as the Desktop Settings form from ``plugin.yaml``'s config_schema), read
through ``runtime.setting`` with the precedence **tool args > settings >
default**, and the defaults are GENERIC (a settings field with a
hardcoded personal default is still a hardcode) and IDENTICAL to the
defaults the code actually uses (plugin.yaml == the code — R06's spirit
applied to settings):

  * precedence: args override settings; settings override the default;
    a missing context (no register()) = the default; a raising/None
    ``get_config`` = the default (a settings read never crashes);
  * config_schema: the 10 keys, types + choices valid, every key carries
    a label + description (the Settings form), and every default is a
    generic value (no personal paths/names/numbers);
  * code defaults == the schema defaults (harvest_mode=browser,
    browser=auto, browser_profile=Default, max_scrolls=500, import_dir="",
    enrich=llm, llm_max_items=50, generate_limit=5,
    register_generated_skills=true);
  * the mode guards read harvest_mode through the same precedence
    (args > settings): reapx_harvest refuses in export mode,
    reapx_import in browser mode, and args can override the settings
    mode for one call;
  * llm_max_items=0 disables LLM enrichment (the cap's documented
    meaning) — zero ctx.llm calls.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PLUGIN_PKG = "reapx_under_test"
HERMES_CHECKOUT = Path("/Users/fromthejump/.hermes/profiles/thanos/"
                       "hermes-agent")

if str(HERMES_CHECKOUT) not in sys.path and HERMES_CHECKOUT.is_dir():
    sys.path.insert(0, str(HERMES_CHECKOUT))
try:
    import hermes_constants  # noqa: F401
    import yaml  # noqa: F401
    HAVE_HERMES = True
except Exception:
    HAVE_HERMES = False


def _load_plugin(pkg=PLUGIN_PKG):
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


class StubCtx:
    """get_config only (settings); optionally raising (never a crash)."""

    def __init__(self, settings=None, raise_on_read=False):
        self._settings = settings or {}
        self._raise = raise_on_read

    def get_config(self, key, default=None):
        if self._raise:
            raise RuntimeError("settings backend exploded")
        return self._settings.get(key, default)


class SettingPrecedenceTests(unittest.TestCase):

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        self.plugin = _load_plugin()
        from reapx_under_test import runtime
        self.runtime = runtime

    def tearDown(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def test_args_beat_settings_beat_default(self):
        self.runtime.set_ctx(StubCtx({"enrich": "rules"}))
        self.assertEqual(self.runtime.setting("enrich", "llm"), "rules")
        self.assertEqual(
            self.runtime.setting("enrich", "llm",
                                 {"enrich": "llm"}), "llm")

    def test_no_context_is_the_default(self):
        self.runtime.set_ctx(None)
        self.assertEqual(self.runtime.setting("enrich", "llm"), "llm")

    def test_raising_or_none_get_config_is_the_default(self):
        self.runtime.set_ctx(StubCtx(raise_on_read=True))
        self.assertEqual(self.runtime.setting("max_scrolls", 500), 500)
        self.runtime.set_ctx(StubCtx({"enrich": None}))
        self.assertEqual(self.runtime.setting("enrich", "llm"), "llm")

    def test_a_settings_read_never_crashes_on_a_bare_ctx(self):
        # the validate probe's recording stub has no get_config at all
        class Bare:
            pass
        self.runtime.set_ctx(Bare())
        self.assertEqual(self.runtime.setting("enrich", "llm"), "llm")


@unittest.skipUnless(HAVE_HERMES, "plugin.yaml yaml + the engine map")
class ConfigSchemaTests(unittest.TestCase):

    SCHEMA_DEFAULTS = {
        "harvest_mode": "browser",
        "browser": "auto",
        "browser_profile": "Default",
        "max_scrolls": 500,
        "import_dir": "",
        "enrich": "llm",
        "llm_max_items": 50,
        "generate_limit": 5,
        "register_generated_skills": True,
    }

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        # pin the hermes home (a _Pipeline init touches plugin-data)
        self.tmp = tempfile.TemporaryDirectory(prefix="reapx-set-")
        self.addCleanup(self.tmp.cleanup)
        from hermes_constants import (reset_hermes_home_override,
                                      set_hermes_home_override)
        home = Path(self.tmp.name) / "hermes-home"
        home.mkdir(parents=True)
        self._token = set_hermes_home_override(home)
        self.addCleanup(lambda: reset_hermes_home_override(self._token))
        self.plugin = _load_plugin()

    def tearDown(self):
        try:
            self.plugin.runtime.run_unload()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def _schema(self):
        with (REPO_ROOT / "plugin.yaml").open(encoding="utf-8") as f:
            return yaml.safe_load(f)["config_schema"]

    def test_every_key_has_type_label_description_default(self):
        schema = self._schema()
        self.assertEqual(sorted(schema), sorted(self.SCHEMA_DEFAULTS))
        for key, spec in schema.items():
            self.assertIn(key, self.SCHEMA_DEFAULTS, key)
            self.assertIn(spec["type"], ("str", "int", "float", "bool",
                                         "list", "dict", "secret"), key)
            self.assertIn("label", spec, key)
            self.assertTrue(spec["label"], key)
            self.assertIn("description", spec, key)
            self.assertTrue(spec["description"], key)
            self.assertIn("default", spec, key)

    def test_defaults_are_generic(self):
        """A settings field with a hardcoded personal default is still a
        hardcode: no personal paths / names / numbers in any default."""
        schema = self._schema()
        self.assertEqual({k: v["default"] for k, v in schema.items()},
                         self.SCHEMA_DEFAULTS)
        for key, default in self.SCHEMA_DEFAULTS.items():
            if isinstance(default, str):
                self.assertNotIn("/", default, key)
                self.assertNotIn(":", default, key)

    def test_choices(self):
        schema = self._schema()
        self.assertEqual(schema["harvest_mode"]["choices"],
                         ["browser", "export"])
        self.assertEqual(schema["enrich"]["choices"], ["llm", "rules"])
        self.assertEqual(schema["browser"]["choices"],
                         ["auto", "brave", "chrome", "edge", "arc",
                          "opera"])

    def test_code_defaults_equal_the_schema_defaults(self):
        """plugin.yaml == the code (the defaults the handlers actually
        use when nothing is configured) — every default read through
        runtime.setting with an empty settings context."""
        from reapx_under_test import runtime
        runtime.set_ctx(StubCtx({}))
        from reapx_under_test import harvest
        p = harvest._Pipeline({}, "export")
        self.assertEqual(p.enrich, "llm")
        self.assertEqual(p.llm_max_items, 50)
        self.assertEqual(p.generate_limit, 5)
        self.assertEqual(p.register_skills, True)
        from reapx_under_test import tools
        self.assertEqual(tools.runtime.setting("harvest_mode", "browser"),
                         "browser")
        self.assertEqual(tools.runtime.setting("browser", "auto"),
                         "auto")
        self.assertEqual(tools.runtime.setting("browser_profile",
                                               "Default"), "Default")
        self.assertEqual(tools.runtime.setting("max_scrolls", 500), 500)
        self.assertEqual(tools.runtime.setting("import_dir", ""), "")


@unittest.skipUnless(HAVE_HERMES, "tools + the mode guards")
class ModeGuardSettingsTests(unittest.TestCase):

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        # pin the hermes home (the guards' one-arg-override paths run the
        # driver → plugin-data)
        self.tmp = tempfile.TemporaryDirectory(prefix="reapx-guard-")
        self.addCleanup(self.tmp.cleanup)
        from hermes_constants import (reset_hermes_home_override,
                                      set_hermes_home_override)
        home = Path(self.tmp.name) / "hermes-home"
        home.mkdir(parents=True)
        self._token = set_hermes_home_override(home)
        self.addCleanup(lambda: reset_hermes_home_override(self._token))
        self.plugin = _load_plugin()
        from reapx_under_test import runtime
        self.runtime = runtime
        from reapx_under_test import tools
        self.tools = tools

    def tearDown(self):
        try:
            self.plugin.runtime.run_unload()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def _run(self, raw):
        return json.loads(raw)

    def test_mode_guards_read_the_settings(self):
        self.runtime.set_ctx(StubCtx({"harvest_mode": "export"}))
        out = self._run(self.tools.harvest({}))
        self.assertEqual(out["error"], "harvest_disabled_in_export_mode")
        self.runtime.set_ctx(StubCtx({"harvest_mode": "browser"}))
        out = self._run(self.tools.import_({}))
        self.assertEqual(out["error"], "import_disabled_in_browser_mode")

    def test_tool_args_override_the_settings_mode(self):
        # args > settings (one call), the runtime.setting precedence:
        self.runtime.set_ctx(StubCtx({"harvest_mode": "export"}))
        # harvest unlocked for one call by the arg (then fails cleanly —
        # the browser "nonexistent" has no cookie DB):
        out = self._run(self.tools.harvest(
            {"harvest_mode": "browser", "browser": "nonexistent"}))
        self.assertNotEqual(out["error"],
                            "harvest_disabled_in_export_mode")
        self.assertFalse(out["ok"])
        # import unlocked for one call by the arg (no input given):
        self.runtime.set_ctx(StubCtx({"harvest_mode": "browser"}))
        out = self._run(self.tools.import_({"harvest_mode": "export"}))
        self.assertNotEqual(out["error"],
                            "import_disabled_in_browser_mode")
        self.assertFalse(out["ok"])

    def test_llm_max_items_zero_disables_the_llm(self):
        """The cap's documented meaning: 0 = zero ctx.llm calls."""
        from reapx_under_test import llm

        class Exploding:
            calls = []

            @property
            def llm(self):
                raise RuntimeError("must never be touched")

        class Ctx:
            def __init__(self):
                self.llm = Exploding()

            def get_config(self, key, default=None):
                return default

        # llm_max_items=0: _cap returns [] before the enricher is even
        # built → the (exploding) llm property is never reached:
        overrides, stats = llm.enrich_map(
            [{"_bookmark_id": "1", "name": "x",
              "description": "d", "topics": ["t"]}],
            ctx=Ctx(), llm_max_items=0, enrich="llm")
        self.assertEqual(overrides, {})
        self.assertEqual(stats.errors, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
