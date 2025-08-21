#!/usr/bin/env python3
# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

import dataclasses
import logging
import ops
from typing import Optional

from forgejo_runner_handler import generate_config


logger = logging.getLogger(__name__)

SERVICE_NAME = "forgejo-runner"  # Name of Pebble service that runs in the workload container.
FORGEJO_CLI = "/usr/local/bin/forgejo-runner"
CUSTOM_FORGEJO_RUNNER_CONFIG = "/etc/forgejo-runner.yaml"


@dataclasses.dataclass(frozen=True, kw_only=True)
class ForgejoRunnerConfig:
    """Configuration for the Forgejo Runner k8s charm."""

    log_level: str = "info"
    job_log_level: str = "info"
    registration_token: str
    labels: str = "docker"
    forgejo_url: str

    def __post_init__(self):
        """Configuration validation."""
        if self.log_level not in ['trace', 'debug', 'info', 'warn', 'error', 'fatal']:
            raise ValueError('Invalid log level number, should be one of trace, debug, info, warn, error, or fatal')

class ForgejoRunnerK8SOperatorCharm(ops.CharmBase):
    """Forgejo Runner K8s Charm."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)

        framework.observe(self.on.forgejo_pebble_ready, self._on_pebble_ready)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)

        self._name = "forgejo-runner"
        self.container = self.unit.get_container(self._name)
        self.pebble_service_name = SERVICE_NAME


    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        config = None
        try:
            config = self.load_config(ForgejoRunnerConfig)
        except ValueError as e:
            event.add_status(ops.BlockedStatus(str(e)))

        if config:
            if not config.registration_token:
                event.add_status(ops.BlockedStatus('Need a registration-token in charm config'))
            if not config.forgejo_url:
                event.add_status(ops.BlockedStatus('Need to set forgejo-url in charm config'))
        try:
            status = self.container.get_service(self.pebble_service_name)
        except (ops.pebble.APIError, ops.pebble.ConnectionError, ops.ModelError):
            event.add_status(ops.MaintenanceStatus('Waiting for Pebble in workload container'))
        else:
            if not status.is_running():
                event.add_status(ops.MaintenanceStatus('Waiting for the service to start up'))
        # If nothing is wrong, then the status is active.
        event.add_status(ops.ActiveStatus())

    @property
    def _forgejo_runner_version(self) -> Optional[str]:
        """Returns the version of Forgejo Runner.

        Returns:
            A string equal to the Forgejo Runner version.
        """
        if not self.container.can_connect():
            return None
        version_output, _ = self.container.exec([FORGEJO_CLI, "--version"]).wait_output()
        # Output looks like this:
        # forgejo-runner version v9.0.3
        result = version_output.split(" ")
        if result is None:
            return result
        ver = result[-1]
        if isinstance(ver, str):
            if ver.startswith('v'):
                return ver
        return None

    def _on_pebble_ready(self, _: ops.PebbleReadyEvent) -> None:
        """Handle pebble-ready event."""
        self._update_layer_and_restart()

    def _on_config_changed(self, _: ops.ConfigChangedEvent) -> None:
        self._update_layer_and_restart()


    def _get_pebble_layer(self) -> ops.pebble.Layer:
        """A Pebble layer for the Forgejo service."""
        command = [FORGEJO_CLI, 'daemon', f'--config={CUSTOM_FORGEJO_RUNNER_CONFIG}'] 
        pebble_layer: ops.pebble.LayerDict = {
            'summary': 'Forgejo Runner service',
            'description': 'pebble config layer for the Forgejo Runner',
            'services': {
                self.pebble_service_name: {
                    'override': 'replace',
                    'summary': 'Forgejo Runner service',
                    'command': ' '.join(command),
                    'startup': 'enabled',
                    'user-id': 1000,
                    'group-id': 1000,
                    "working-dir": "/data",
                }
            },
        }
        return ops.pebble.Layer(pebble_layer)

    def _update_layer_and_restart(self) -> None:
        self.unit.status = ops.MaintenanceStatus("starting workload")
        try:
            config = self.load_config(ForgejoRunnerConfig)
        except ValueError as e:
            logger.error('Configuration error: %s', e)
            self.unit.status = ops.BlockedStatus(str(e))
            return

        try:
            # write the config file to the forgejo runner container's filesystem
            cfg = generate_config(
                log_level=config.log_level,
                job_log_level=config.job_log_level,
            )
            self.container.push(
                CUSTOM_FORGEJO_RUNNER_CONFIG,
                cfg,
                user_id=1000,
                user='runner',
                group_id=1000
            )

            self.container.add_layer('forgejo', self._get_pebble_layer(), combine=True)
            logger.info("Added updated layer 'forgejo' to Pebble plan")

            # Tell Pebble to incorporate the changes, including restarting the
            # service if required.
            self.container.replan()
            logger.info(f"Replanned with '{self.pebble_service_name}' service")

            if version := self._forgejo_runner_version:
                self.unit.set_workload_version(version)
            else:
                logger.debug("Cannot set workload version at this time: could not get Forgejo Runner version.")
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.info('Unable to connect to Pebble: %s', e)


    def _register_runner(self, url: str, token: str, labels: str) -> bool:
        """Register the runner against the Forgejo server."""
        if not self.container.can_connect():
            return False
        registration_output, _ = self.container.exec([
            FORGEJO_CLI,
            "create-runner-file",
            "--instance", url,
            "--token", token,
            "--labels", labels,
            "--name", f"{self.model.name}-{self.unit.name}", # TODO: either prefix controller name too, or some random str
            "--no-interactive",
        ]).wait_output()
        logger.info(registration_output)
        return True



if __name__ == "__main__":  # pragma: nocover
    ops.main(ForgejoRunnerK8SOperatorCharm)
