"""Service reachability monitor.

Probes HTTP endpoints and ZooKeeper-registered Dubbo services across several
environments, then reports unreachable ones through pluggable notification
channels.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]