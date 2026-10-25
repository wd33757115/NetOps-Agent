"""Bounded platform discovery on one SSH session, using only server-owned commands."""

import re
from dataclasses import replace

from .catalog import DISCOVERY_PROFILES
from .contracts import Command, DeviceAccessError


def identify_platform(output):
    matches = []
    if re.search(r"(?im)^\s*(?:(?:H3C|HPE|HP)\s+)?Comware (?:Platform )?Software\b", output):
        matches.append("comware-lldp-v1")
    if re.search(r"(?im)^\s*Cisco Nexus Operating System\s*\(NX-OS\)\s*Software\b", output):
        matches.append("nxos-lldp-v1")
    if len(matches) > 1:
        raise DeviceAccessError("platform_ambiguous", "平台识别证据冲突，请人工确认此节点")
    return matches[0] if matches else None


class DiscoveryPlan:
    """A fresh plan per target. No hint or choice is inherited by adjacent devices."""

    def __init__(self, profile_id="auto", *, identifier=None):
        self._identify = identifier or (lambda output, command_id: identify_platform(output))
        if profile_id != "auto" and profile_id not in DISCOVERY_PROFILES:
            raise DeviceAccessError("platform_unsupported", "此平台尚无已验证的 LLDP 适配")
        self.profile_id = None if profile_id == "auto" else profile_id
        self.source = "version_probe" if profile_id == "auto" else "manual"
        self.evidence_command = None
        self.commands = (
            DISCOVERY_PROFILES[profile_id]
            if self.profile_id
            else (
                Command("detect-display", "display version", read_timeout=15),
                Command("detect-show", "show version", read_timeout=15),
                *(
                    replace(c, command_id=f"{key}:{c.command_id}")
                    for key, commands in DISCOVERY_PROFILES.items()
                    for c in commands
                ),
            )
        )

    def select(self, command, results):
        # A timeout/paging/transport error ends this attempt; never reconnect to probe.
        for result in results:
            if result.failure_code or not result.output_complete:
                raise DeviceAccessError(
                    result.failure_code or "output_incomplete", "平台识别或采集未完成"
                )
        if self.source == "manual":
            return True
        if self.profile_id is None:
            for result in results:
                if result.command_id.startswith("detect-"):
                    found = self._identify(result.output, result.command_id)
                    if found:
                        self.profile_id, self.evidence_command = found, result.command_id
                        break
        if command.command_id.startswith("detect-"):
            return self.profile_id is None
        if self.profile_id is None:
            raise DeviceAccessError(
                "platform_unrecognized", "无法识别此节点平台，请人工确认或补充适配"
            )
        return command.command_id.startswith(self.profile_id + ":")

    def metadata(self):
        return {
            "profile_id": self.profile_id,
            "source": self.source,
            "evidence_command": self.evidence_command,
            "status": "identified" if self.profile_id else "unrecognized",
        }

    def parser_outputs(self, outputs):
        if not self.profile_id:
            raise DeviceAccessError("platform_unrecognized", "无法识别此节点平台")
        prefix = self.profile_id + ":"
        return {key.removeprefix(prefix): value for key, value in outputs.items()}
