import importlib.util, sys, os
_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("agentseeai._core", os.path.join(_here, "_core.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["agentseeai._core"] = _mod
_spec.loader.exec_module(_mod)
sys.modules[__name__] = _mod
