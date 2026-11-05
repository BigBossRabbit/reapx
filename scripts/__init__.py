# makes scripts/ a package for the plugin layer (hermes_plugins.<plugin>.scripts.*)
# so the shared engine can be imported under the plugin namespace — zero effect
# on `python3 scripts/<x>.py` or the standalone `sys.path`-style imports.
