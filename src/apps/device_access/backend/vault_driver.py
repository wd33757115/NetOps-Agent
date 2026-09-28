"""App credential orchestration inside the public service's existing SSH lease."""

from dataclasses import replace

from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver
from src.platform.device_access.errors import CommandExecutionError, failure_code


class RedactedConnection:
    def __init__(self, connection, material):
        self._connection = connection
        self._secrets = tuple(
            sorted(filter(None, (material.password, material.enable_secret)), key=len, reverse=True)
        )

    def _redact(self, value):
        value = str(value or "")
        for secret in self._secrets:
            value = value.replace(secret, "[redacted]")
        return value

    def redact(self, value):
        return self._redact(value)

    def _call(self, method, *args, **kwargs):
        try:
            return self._redact(getattr(self._connection, method)(*args, **kwargs))
        except Exception as exc:
            raise CommandExecutionError(
                failure_code(exc), "设备命令执行失败", self._redact(getattr(exc, "output", ""))
            ) from None

    def find_prompt(self):
        return self._call("find_prompt")

    def send_command(self, *args, **kwargs):
        return self._call("send_command", *args, **kwargs)

    @property
    def channel(self):
        # Human terminal transport is never stored as command evidence or sent to a model.
        return self._connection.channel

    def disconnect(self):
        # Closing a transport may precede the final audit write; keep the redactor
        # until this backend-only wrapper is released, as with the driver's profile.
        self._connection.disconnect()


class VaultDriver:
    def __init__(
        self, vault, owner, *, temporary_ref=None, credential_set_id=None, guard=None, driver=None
    ):
        self.vault = vault
        self.owner = owner
        self.temporary_ref = temporary_ref
        self.credential_set_id = credential_set_id
        self.guard = guard
        self.driver = driver or ParamikoDriver()
        self.name, self.version = self.driver.name, self.driver.version

    def connect(self, target, profile, *, interactive=False):
        def connect(material, remaining):
            resolved = replace(
                profile,
                username=material.username,
                password=material.password,
                credential_ref=None,
                connection_attempts=1,
                conn_timeout=min(profile.conn_timeout, remaining),
                auth_timeout=min(profile.auth_timeout, remaining),
                banner_timeout=min(profile.banner_timeout, remaining),
                prompt_timeout=min(profile.prompt_timeout, remaining),
                session_setup_timeout=min(profile.session_setup_timeout, remaining),
            )
            connection = self.driver.connect(target, resolved, interactive=interactive)
            return RedactedConnection(connection, material)

        return self.vault.login(
            self.owner,
            target.management_domain,
            target.host,
            connect,
            temporary_ref=self.temporary_ref,
            credential_set_id=self.credential_set_id,
            budget_seconds=profile.session_setup_timeout,
            should_continue=self.guard,
        )
