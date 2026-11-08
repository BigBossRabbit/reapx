#!/usr/bin/env python3
"""test_settings.py — the settings law (the skill's step-6 gate).

Every setting lives under ``plugins.entries.reapx.settings.*`` (rendered
as the Desktop Settings form from ``plugin.yaml``'s config_schema) and is
read through ``runtime.setting`` from the PLUGIN CONFIG ONLY. The defaults
are GENERIC (a settings field with a hardcoded personal default is still a
hardcode) and IDENTICAL to the defaults the code actually uses
(plugin.yaml == the code — R06's spirit applied to settings):

  * config-only: a tool arg with the same name does NOT override a setting
    (a prompt-injected call cannot flip enrich, lift llm_max_items, force
    registration or switch import_dir);
  * a missing context (no register()) = the default; a raising/None
    ``get_config`` = the default (a settings read never crashes);
  * config_schema: exactly the 5 keys, types + choices valid, every key
    carries a label + description (the Settings form), every default
    generic (no personal paths/names/numbers);
  * code defaults == the schema defaults (import_dir="", enrich=llm,
    llm_max_items=50, generate_limit=5, register_generated_skills=false);
  * llm_max_items=0 disables LLM enrichment (the cap's documented
    meaning) — zero ctx.llm calls — and 0 survives a real settings read
    (it is not swallowed by an ``or`` fallback);
  * ``runtime.bounded_arg`` is the ONE per-call knob: it may only LOWER
    the configured cap, never raise it, and junk/absent/negative = the cap.
"""
import importlib.util
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


class SettingLawTests(unittest.TestCase):

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

    def test_settings_come_from_config_only(self):
        """The old precedence (args > settings) is gone: an arg with the
        same name is IGNORED — only config counts."""
        self.runtime.set_ctx(StubCtx({"enrich": "rules",
                                      "llm_max_items": 3,
                                      "register_generated_skills": True}))
        hostile = {"enrich": "llm", "llm_max_items": 999,
                   "register_generated_skills": False,
                   "import_dir": "/tmp/pwned",
                   "harvest_mode": "browser"}
        self.assertEqual(self.runtime.setting("enrich", "llm"), "rules")
        self.assertEqual(self.runtime.setting("llm_max_items", 50), 3)
        self.assertIs(self.runtime.setting("register_generated_skills",
                                           False), True)
        # the legacy third positional is gone from the signature too
        import inspect
        params = list(inspect.signature(self.runtime.setting).parameters)
        self.assertEqual(params, ["key", "default"])

    def test_no_context_is_the_default(self):
        self.runtime.set_ctx(None)
        self.assertEqual(self.runtime.setting("enrich", "llm"), "llm")

    def test_raising_or_none_get_config_is_the_default(self):
        self.runtime.set_ctx(StubCtx(raise_on_read=True))
        self.assertEqual(self.runtime.setting("generate_limit", 5), 5)
        self.runtime.set_ctx(StubCtx({"enrich": None}))
        self.assertEqual(self.runtime.setting("enrich", "llm"), "llm")

    def test_a_settings_read_never_crashes_on_a_bare_ctx(self):
        # the validate probe's recording stub has no get_config at all
        class Bare:
            pass
        self.runtime.set_ctx(Bare())
        self.assertEqual(self.runtime.setting("enrich", "llm"), "llm")

    def test_zero_setting_is_a_value_not_an_absence(self):
        self.runtime.set_ctx(StubCtx({"llm_max_items": 0,
                                      "generate_limit": 0}))
        self.assertEqual(self.runtime.setting("llm_max_items", 50), 0)
        self.assertEqual(self.runtime.setting("generate_limit", 5), 0)


class BoundedArgTests(unittest.TestCase):
    """The ONE per-call knob: it can only lower the configured cap."""

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

    def test_lower_wins(self):
        self.assertEqual(self.runtime.bounded_arg({"generate_limit": 2},
                                                  "generate_limit", 5), 2)
        self.assertEqual(self.runtime.bounded_arg({"generate_limit": 0},
                                                  "generate_limit", 5), 0)

    def test_raise_is_clamped_to_the_setting(self):
        self.assertEqual(self.runtime.bounded_arg({"generate_limit": 99},
                                                  "generate_limit", 5), 5)
        self.assertEqual(self.runtime.bounded_arg({"generate_limit": 999999},
                                                  "generate_limit", 0), 0)

    def test_absent_junk_and_negative_mean_the_cap(self):
        for args in ({}, None, {"generate_limit": None},
                     {"generate_limit": "junk"},
                     {"generate_limit": -4}):
            self.assertEqual(
                self.runtime.bounded_arg(args, "generate_limit", 5), 5,
                args)


@unittest.skipUnless(HAVE_HERMES, "plugin.yaml yaml + the engine map")
class ConfigSchemaTests(unittest.TestCase):

    SCHEMA_DEFAULTS = {
        "import_dir": "",
        "enrich": "llm",
        "llm_max_items": 50,
        "generate_limit": 5,
        "register_generated_skills": False,
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

    def test_exactly_the_five_settings(self):
        """harvest_mode / browser / browser_profile / max_scrolls are gone:
        the plugin touches no browser, and max_scrolls bounded a scroll loop
        that no longer exists on the plugin path."""
        self.assertEqual(sorted(self._schema()),
                         sorted(self.SCHEMA_DEFAULTS))

    def test_every_key_has_type_label_description_default(self):
        schema = self._schema()
        for key, spec in schema.items():
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
        self.assertEqual(self._schema()["enrich"]["choices"],
                         ["llm", "rules"])

    def test_registration_defaults_to_off(self):
        self.assertIs(self._schema()["register_generated_skills"]["default"],
                      False)

    def test_code_defaults_equal_the_schema_defaults(self):
        """plugin.yaml == the code (the defaults the handlers actually
        use when nothing is configured) — every default read through
        runtime.setting with an empty settings context."""
        from reapx_under_test import runtime
        runtime.set_ctx(StubCtx({}))
        from reapx_under_test import harvest
        p = harvest._Pipeline({})
        self.assertEqual(p.enrich, "llm")
        self.assertEqual(p.llm_max_items, 50)
        self.assertEqual(p.generate_limit, 5)
        self.assertIs(p.register_skills, False)
        self.assertEqual(runtime.setting("import_dir", ""), "")


@unittest.skipUnless(HAVE_HERMES, "the driver + llm fallback")
class DriverSettingUseTests(unittest.TestCase):

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
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

    def tearDown(self):
        try:
            self.plugin.runtime.run_unload()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    def test_generate_limit_arg_is_capped_by_the_setting(self):
        from reapx_under_test import harvest
        self.runtime.set_ctx(StubCtx({"generate_limit": 3}))
        self.assertEqual(harvest._Pipeline({"generate_limit": 2})
                         .generate_limit, 2)
        self.assertEqual(harvest._Pipeline({"generate_limit": 50})
                         .generate_limit, 3)
        self.assertEqual(harvest._Pipeline({}).generate_limit, 3)

    def test_the_setting_zero_survives_the_driver(self):
        """generate_limit=0 (assess-only) must not be turned into 5."""
        from reapx_under_test import harvest
        self.runtime.set_ctx(StubCtx({"generate_limit": 0}))
        self.assertEqual(harvest._Pipeline({}).generate_limit, 0)

    def test_harvest_mode_arg_is_ignored(self):
        """There is no harvest_mode setting any more — passing the old arg
        changes nothing, the tool always runs the one pipeline."""
        from reapx_under_test import harvest
        self.runtime.set_ctx(StubCtx({}))
        a = harvest._Pipeline({"harvest_mode": "browser", "enrich": "rules"})
        self.assertEqual((a.enrich, a.generate_limit), ("llm", 5))

    def test_llm_max_items_zero_disables_the_llm(self):
        """The cap's documented meaning: 0 = zero ctx.llm calls."""
        from reapx_under_test import llm

        class Exploding:
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
