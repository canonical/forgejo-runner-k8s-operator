# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""Functions for interacting with the workload.

The intention is that this module could be used outside the context of a charm.
"""

import logging
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)


def generate_config(
    log_level: str = "info",
    job_log_level: str = "info",
) -> str:
    env = Environment(loader=FileSystemLoader("."))
    template = env.get_template("./templates/config.yaml.j2")
        
    return template.render({
        "log_level": log_level,
        "job_log_level": job_log_level,
    })
