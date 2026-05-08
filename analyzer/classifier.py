import sys
import importlib.util


def classify_module(module_name):

    root_module = module_name.split(".")[0]

    if root_module in sys.stdlib_module_names:
        return "standard_library"

    spec = importlib.util.find_spec(root_module)

    if spec is None:
        return "local_module"

    origin = str(spec.origin)

    if "site-packages" in origin:
        return "third_party"

    return "local_module"