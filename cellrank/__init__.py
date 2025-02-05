from cellrank import pl, models, kernels, logging, datasets, external, estimators
from cellrank.settings import settings
from cellrank._utils._lineage import Lineage

__author__ = ", ".join(["Marius Lange", "Michal Klein", "Philipp Weiler"])
__maintainer__ = ", ".join(["Marius Lange", "Michal Klein", "Philipp Weiler"])
__version__ = "1.5.1"
__email__ = "info@cellrank.org"

try:
    from importlib_metadata import version  # Python < 3.8
except ImportError:
    from importlib.metadata import version  # Python = 3.8

from packaging.version import parse

try:
    __full_version__ = parse(version(__name__))
    __full_version__ = (
        f"{__version__}+{__full_version__.local}"
        if __full_version__.local
        else __version__
    )
except ImportError:
    __full_version__ = __version__

del version, parse
