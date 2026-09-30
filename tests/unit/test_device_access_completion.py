from types import SimpleNamespace

import pytest

from src.platform.device_access import completion, governance
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.human_cli import HumanCLI

HOTKEYS = """           -Defined command hotkeys-
           -Undefined command hotkeys-
           -System-reserved hotkeys-
CTRL_E  Move the cursor to the end of the line.
CTRL_R  Redisplay the current line.
CTRL_X  Delete all characters from the beginning of the line to the cursor.
"""


class Channel:
    def __init__(self, *, banner="SSH-2.0-Comware-7.1.064", prompt="<H3C>"):
        self.banner, self.prompt = banner, prompt
        self.pending, self.sent, self.received = [prompt.encode()], [], 0
        self.closed, self.hotkeys, self.clear_reply = False, HOTKEYS, None
        self.tab_reply, self.last_draft, self.on_tab = None, "", None
        self.tab_index, self.seed = 0, ""

    def get_transport(self):
        return SimpleNamespace(remote_version=self.banner)

    def recv_ready(self):
        return bool(self.pending)

    def recv(self, size):
        chunk = self.pending.pop(0)
        value, rest = chunk[:size], chunk[size:]
        if rest:
            self.pending.insert(0, rest)
        self.received += len(value)
        return value

    def settimeout(self, timeout):
        pass

    def exit_status_ready(self):
        return self.closed

    def close(self):
        self.closed = True

    def sendall(self, value):
        self.sent.append(value)
        if value == (completion.HOTKEY_COMMAND + "\n").encode():
            self.pending.append(
                (completion.HOTKEY_COMMAND + "\r\n" + self.hotkeys + "\r\n" + self.prompt).encode()
            )
        elif value == completion.CLEAR_AND_REDRAW:
            self.pending.append(
                self.clear_reply
                if self.clear_reply is not None
                else (f"\x1b[{len(self.last_draft)}D\x1b[J\r\r\n{self.prompt}".encode())
            )
        elif value.endswith(b"\t"):
            command = value[:-1].decode()
            # Real Comware retains the candidate cursor after E/X/R clears
            # the draft. Retyping that seed is not a new completion cycle.
            if command and command not in {self.seed, self.last_draft}:
                self.seed, self.tab_index = command, 0
            self.last_draft = (
                ("display client-verify", "display clock", "display cloud-management")[
                    self.tab_index % 3
                ]
                if self.seed == "display cl"
                else {"dis": "display "}.get(self.seed, self.seed)
            )
            self.tab_index += 1
            if self.tab_reply is not None:
                self.pending.extend(self.tab_reply)
            else:
                self.pending.append((command + "\r\r\n" + self.prompt + self.last_draft).encode())
            if self.on_tab:
                self.on_tab()
        else:
            self.pending.append(value.rstrip(b"\n") + b"\r\nresult\r\n" + self.prompt.encode())


@pytest.fixture
def setup():
    channel, output = Channel(), []
    operation = governance.SSHOperation("alice", "test", "fixture", "192.0.2.1")
    operation.event("connecting")
    cli = HumanCLI(channel, operation, lambda: None, on_output=output.append)
    cli.initialize()
    output.clear()
    return cli, channel, output


@pytest.mark.parametrize(
    "draft,completed",
    [
        ("dis", "display "),
        ("display cl", "display client-verify"),
        ("nonexistent-test", "nonexistent-test"),
    ],
)
def test_real_comware_reply_completes_clears_and_suppresses_temporary_echo(setup, draft, completed):
    cli, channel, output = setup
    before = channel.received
    result = cli.complete(draft, request_id="tab1")
    assert result == {
        "command": completed,
        "changed": completed != draft,
        "received_bytes": channel.received - before,
    }
    assert output == [] and cli.usable and not channel.closed
    assert channel.sent == [
        (completion.HOTKEY_COMMAND + "\n").encode(),
        draft.encode() + b"\t",
        completion.CLEAR_AND_REDRAW,
    ]
    event = governance.query_audit(kind="completion_finished")["items"][0]
    assert event["detail"]["cleared"] and event["detail"]["completed_command"] == completed
    assert event["status"] == "succeeded"


def test_reserved_keys_are_cached_but_final_command_uses_current_fullmatch(setup):
    cli, channel, _ = setup
    cli.complete("dis")
    governance.save_policy([r"display version"], "admin")
    # Editing a prefix is not command execution and does not create a partial grant.
    cli.complete("display cl")
    with pytest.raises(DeviceAccessError) as error:
        cli.execute("display client-verify")
    assert error.value.failure_code == "policy_denied"
    assert b"display client-verify\n" not in channel.sent
    assert channel.sent.count((completion.HOTKEY_COMMAND + "\n").encode()) == 1
    cli.execute("display version")
    assert channel.sent[-1] == b"display version\n"


@pytest.mark.parametrize(
    "draft",
    [
        "",
        " ",
        "dis\n",
        "dis\r",
        "dis\t",
        "dis?",
        "a;b",
        "a|b",
        "$x",
        "`x`",
        "a\x1b[D",
        "a" * 257,
        "中文",
    ],
)
def test_controls_and_unverified_editing_characters_never_reach_ssh(setup, draft):
    cli, channel, _ = setup
    with pytest.raises(DeviceAccessError):
        cli.complete(draft)
    assert channel.sent == [] and cli.usable
    event = governance.query_audit(kind="completion_finished")["items"][0]
    assert event["status"] == "denied" and event["detail"]["sent"] is False


@pytest.mark.parametrize(
    "banner,prompt",
    [
        ("SSH-2.0-OpenSSH_9.0", "<H3C>"),
        ("SSH-2.0-Comware-7.1.064", "user@linux$"),
    ],
)
def test_banner_and_prompt_must_both_support_comware(setup, banner, prompt):
    cli, channel, _ = setup
    channel.banner, cli.last_prompt = banner, prompt
    assert cli.completion_platform is None
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("dis")
    assert error.value.failure_code == "completion_unsupported"
    assert channel.sent == []


@pytest.mark.parametrize(
    "table",
    [
        HOTKEYS.replace("System-reserved hotkeys", "Defined function hotkeys"),
        HOTKEYS.replace("Redisplay the current line.", "reboot"),
        HOTKEYS.replace("CTRL_X", "CTRL_U"),
    ],
)
def test_unverified_or_rebindable_keys_disable_tab_without_sending_edit_bytes(setup, table):
    cli, channel, _ = setup
    channel.hotkeys = table
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("dis")
    assert error.value.failure_code == "completion_unsupported"
    assert channel.sent == [(completion.HOTKEY_COMMAND + "\n").encode()]
    assert cli.usable


def test_policy_must_authorize_the_real_internal_hotkey_query(setup):
    cli, channel, _ = setup
    governance.save_policy(["display version"], "admin")
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("dis")
    assert error.value.failure_code == "policy_denied"
    assert channel.sent == []


@pytest.mark.parametrize(
    "reply",
    [b"\x1b[8D\x1b[J\r\r\n<H3C>display ", b"<H3C>", b"\r\r\n<Other>", b"\r\r\n<H3C>\x1b[1D"],
)
def test_uncertain_clear_closes_channel_and_prevents_next_execute(setup, reply):
    cli, channel, _ = setup
    channel.clear_reply = reply
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("dis")
    assert error.value.received_bytes > 0
    assert not cli.usable and channel.closed
    sent = list(channel.sent)
    with pytest.raises(DeviceAccessError):
        cli.execute("display version")
    assert channel.sent == sent
    event = governance.query_audit(kind="completion_finished")["items"][0]
    assert event["status"] == "failed" and event["detail"]["cleared"] is False


def test_split_ansi_and_backspace_before_verified_redraw_are_supported(setup):
    cli, channel, _ = setup
    channel.tab_reply = [b"dis\x08\x08\x08\x1b[", b"3D\x1b[J\r", b"\r\n<H3C>display "]
    assert cli.complete("dis")["command"] == "display "


@pytest.mark.parametrize("case", ["timeout", "too-large", "disconnected", "invalid-result"])
def test_tab_failure_is_bounded_and_cannot_leave_a_reusable_draft(setup, case):
    cli, channel, _ = setup
    if case == "timeout":
        channel.tab_reply = []
    elif case == "too-large":
        channel.tab_reply = [b"x" * (completion.MAX_REPLY + 1)]
    elif case == "disconnected":
        channel.on_tab = channel.close
    else:
        channel.tab_reply = [b"dis\r\r\n<H3C>reboot"]
    with pytest.raises(DeviceAccessError):
        cli.complete("dis", timeout=0.12 if case == "timeout" else 3)
    assert not cli.usable and channel.closed


def test_completion_audit_failure_never_sends_tab(setup, ssh_log_storage):
    cli, channel, _ = setup
    ssh_log_storage.fail = True
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("dis")
    assert error.value.failure_code == "audit_unavailable"
    assert channel.sent == []
    assert not cli.usable and channel.closed


def test_authorization_revocation_after_audit_is_checked_before_tab(setup):
    cli, channel, _ = setup
    cli.complete("dis")
    channel.sent.clear()

    def denied():
        raise DeviceAccessError("not_authorized", "revoked")

    cli.before_send = denied
    with pytest.raises(DeviceAccessError):
        cli.complete("display cl")
    assert channel.sent == []


def test_empty_policy_disables_cached_completion(setup):
    cli, channel, _ = setup
    cli.complete("dis")
    channel.sent.clear()
    governance.save_policy([], "admin")
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("dis")
    assert error.value.failure_code == "policy_denied" and channel.sent == []


def test_repeated_tab_requests_advance_device_cursor_once_per_keypress(setup):
    cli, channel, _ = setup
    results = [cli.complete("display cl", tab_count=count)["command"] for count in (1, 2, 3)]
    assert results == ["display client-verify", "display clock", "display cloud-management"]
    assert channel.sent[1:] == [b"display cl\t", completion.CLEAR_AND_REDRAW] * 3
    cli.execute("display clock")
    assert channel.sent[-1] == b"display clock\n"
    assert cli.complete("dis", tab_count=1)["command"] == "display "


def test_round_metadata_never_replays_prior_tab_presses(setup):
    cli, channel, _ = setup
    assert cli.complete("display cl", tab_count=32)["command"] == "display client-verify"
    assert channel.sent[-2:] == [b"display cl\t", completion.CLEAR_AND_REDRAW]


@pytest.mark.parametrize("count", [0, 33, -1, True, "2", 1.5])
def test_tab_count_is_bounded_before_any_network_input(setup, count):
    cli, channel, _ = setup
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("dis", tab_count=count)
    assert error.value.failure_code == "invalid_completion" and not channel.sent


def test_cancel_before_clear_closes_draft_without_sending_more_edit_bytes(setup):
    cli, channel, _ = setup
    cli.complete("dis")
    channel.sent.clear()
    calls = []

    def revoked():
        calls.append(True)
        if len(calls) == 2:
            raise DeviceAccessError("cancelled", "stopped")

    cli.before_send = revoked
    with pytest.raises(DeviceAccessError) as error:
        cli.complete("display cl", tab_count=3)
    assert error.value.failure_code == "cancelled"
    assert channel.sent == [b"display cl\t"]
    assert channel.closed and not cli.usable
